#!/usr/bin/env python3
"""
Watches GitHub repos' READMEs for newly added rows (new internship postings)
and emails you a summary when new ones show up.

How it decides something is "new":
- It fetches the current README for each repo listed in config.json.
- It compares it against the last saved version in state.json to find
  candidate listing rows, supporting two table formats: raw HTML
  (<table><tr>...</tr></table>) and markdown pipe tables (| Company | ... |).
- Separator rows (e.g. "|---|---|") and header rows are filtered out.

State is stored in state.json and committed back to the repo by the GitHub
Action, so the next run knows what it already saw.
"""

import base64
import json
import os
import re
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()  # loads .env if present (local dev)
except ImportError:
    pass  # python-dotenv isn't installed in CI (GitHub Actions sets secrets as real env vars)

CONFIG_FILE = "config.json"
STATE_FILE = "state.json"
GITHUB_API = "https://api.github.com"


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(
            f"{CONFIG_FILE} not found. Create it with a 'repos' list "
            '(e.g. {"repos": ["owner/repo-name"], "keywords": []}).'
        )
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_readme(repo: str, ref: str | None = None) -> str:
    """Fetch raw README text for a repo via the GitHub API, optionally from a specific branch."""
    url = f"{GITHUB_API}/repos/{repo}/readme"
    if ref:
        url += f"?ref={ref}"
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.get(url, headers=headers, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"{resp.status_code} {resp.reason}: {resp.text[:300]}")
    data = resp.json()
    content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    return content


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def is_candidate_pipe_row(line: str) -> bool:
    """Filter out separator/header noise in markdown pipe tables, keep likely listing rows."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    cell_content = stripped.replace("|", "").replace("-", "").replace(":", "").strip()
    if cell_content == "":
        return False
    lowered = stripped.lower()
    return not ("company" in lowered and "role" in lowered)  # True unless header row


def extract_rows(content: str) -> list[str]:
    """
    Pulls out candidate listing rows, supporting two formats seen in the wild:
    - Raw HTML tables: <table><tr>...</tr></table> (e.g. SimplifyJobs repos)
    - Markdown pipe tables: | Company | Role | ... |
    """
    if "<tr>" in content:
        rows = re.findall(r"<tr>.*?</tr>", content, re.DOTALL)
        return [r for r in rows if "<th>" not in r]
    return [line.strip() for line in content.splitlines() if is_candidate_pipe_row(line)]


def row_key(row: str) -> str:
    """
    Stable identity for a row, ignoring fields that naturally change over time
    for the SAME listing (like a relative 'Age' column e.g. '0d' -> '5d'),
    so aging listings don't get mistaken for new ones every run.
    """
    if row.startswith("<tr>"):
        # drop the last <td>...</td> before </tr> — that's the Age column
        return re.sub(r"<td>[^<]*</td>\s*</tr>\s*$", "</tr>", row)
    return row


def extract_link(cell_html: str) -> str | None:
    """Pull the first href out of a table cell — this is reliably the 'Apply' link."""
    hrefs = re.findall(r'href="([^"]+)"', cell_html)
    return hrefs[0] if hrefs else None


def summarize_row(row: str) -> dict:
    """
    Turn a raw row (HTML or markdown) into a structured dict for the email:
    {"company": ..., "role": ..., "location": ..., "link": ...}
    """
    if row.startswith("<tr>"):
        cells = re.findall(r"<td>(.*?)</td>", row, re.DOTALL)

        def strip_tags(s: str) -> str:
            text = re.sub(r"<[^>]+>", " ", s)
            return re.sub(r"\s+", " ", text).strip()

        return {
            "company": strip_tags(cells[0]) if len(cells) > 0 else "?",
            "role": strip_tags(cells[1]) if len(cells) > 1 else "?",
            "location": strip_tags(cells[2]) if len(cells) > 2 else "?",
            "link": extract_link(cells[3]) if len(cells) > 3 else None,
        }

    # markdown pipe row fallback: | Company | Role | Location | [Apply](url) | Date |
    parts = [p.strip() for p in row.strip("|").split("|")]
    link_match = re.search(r"\((https?://[^)]+)\)", row)
    return {
        "company": re.sub(r"[\[\]*]", "", parts[0]) if len(parts) > 0 else "?",
        "role": parts[1] if len(parts) > 1 else "?",
        "location": parts[2] if len(parts) > 2 else "?",
        "link": link_match.group(1) if link_match else None,
    }


def find_new_rows(old_text: str, new_text: str, keywords: list[str] | None = None) -> list[str]:
    old_rows = extract_rows(old_text)
    new_rows = extract_rows(new_text)
    old_keys = {row_key(r) for r in old_rows}

    added = []
    for row in new_rows:
        if row_key(row) in old_keys:
            continue
        if keywords:
            lowered = row.lower()
            if not any(k.lower() in lowered for k in keywords):
                continue
        added.append(summarize_row(row))
    return added


def send_email(subject: str, body: str) -> None:
    sender = os.environ["EMAIL_ADDRESS"]
    password = os.environ["EMAIL_PASSWORD"]
    # TO_EMAIL supports a single address or a comma-separated list, e.g.:
    # "me@gmail.com,friend@gmail.com, other@school.edu"
    raw_recipients = os.environ.get("TO_EMAIL", sender)
    recipients = [addr.strip() for addr in raw_recipients.split(",") if addr.strip()]
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, recipients, msg.as_string())


def normalize_repo_entry(entry) -> tuple[str, str | None]:
    """Accepts either 'owner/repo' or {'repo': 'owner/repo', 'branch': 'dev'}."""
    if isinstance(entry, str):
        return entry, None
    return entry["repo"], entry.get("branch")


def main() -> int:
    config = load_config()
    repos = config.get("repos", [])
    keywords = config.get("keywords") or None  # e.g. ["machine learning", "AI", "data"]

    if not repos:
        print("[warn] no repos listed in config.json — nothing to check.", file=sys.stderr)
        return 0

    state = load_state()
    all_new = {}

    for entry in repos:
        repo, branch = normalize_repo_entry(entry)
        try:
            current_readme = get_readme(repo, ref=branch)
        except Exception as e:  # noqa: BLE001 - one repo's failure must not abort the whole run
            print(f"[warn] failed to fetch {repo}: {e}", file=sys.stderr)
            continue

        previous_readme = state.get(repo, "")
        if previous_readme:
            new_rows = find_new_rows(previous_readme, current_readme, keywords)
            if new_rows:
                all_new[repo] = new_rows
        else:
            print(f"[info] no prior state for {repo}, saving baseline only")

        state[repo] = current_readme

    save_state(state)

    if all_new:
        sections = []
        for repo, listings in all_new.items():
            block = [repo, "-" * len(repo)]
            for listing in listings:
                block.append(f"• {listing['company']} — {listing['role']} ({listing['location']})")
                if listing["link"]:
                    block.append(f"  Apply: {listing['link']}")
                block.append("")  # blank line between listings
            sections.append("\n".join(block))

        body = "New internship listings found:\n\n" + "\n\n".join(sections)
        total = sum(len(v) for v in all_new.values())
        send_email(f"{total} new internship posting(s) found", body)
        print(f"Emailed {total} new listing(s).")
    else:
        print("No new listings found.")

    return 0


if __name__ == "__main__":
    sys.exit(main())