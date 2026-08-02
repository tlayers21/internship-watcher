#!/usr/bin/env python3
"""
Watches GitHub repos' READMEs for newly added rows (new internship postings)
and emails you a summary when new ones show up.

How it decides something is "new":
- It fetches the current README for each repo listed in config.json.
- It compares it, line by line, against the last saved version in state.json.
- Any added line that looks like a markdown table row (starts with "|") is
  treated as a candidate new listing.
- Separator rows (e.g. "|---|---|") and header rows are filtered out.

State is stored in state.json and committed back to the repo by the GitHub
Action, so the next run knows what it already saw.
"""

import os
import json
import base64
import difflib
import smtplib
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

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


def get_readme(repo: str) -> str:
    """Fetch raw README text for a repo via the GitHub API."""
    url = f"{GITHUB_API}/repos/{repo}/readme"
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
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


def is_candidate_row(line: str) -> bool:
    """Filter out separator/header noise, keep likely listing rows."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    # separator rows look like | --- | --- | --- |
    cell_content = stripped.replace("|", "").replace("-", "").replace(":", "").strip()
    if cell_content == "":
        return False
    lowered = stripped.lower()
    if "company" in lowered and "role" in lowered:
        return False  # header row
    return True


def find_new_rows(old_text: str, new_text: str, keywords: list[str] | None = None) -> list[str]:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff = difflib.unified_diff(old_lines, new_lines, lineterm="")
    added = []
    for line in diff:
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            content = line[1:]
            if not is_candidate_row(content):
                continue
            if keywords:
                lowered = content.lower()
                if not any(k.lower() in lowered for k in keywords):
                    continue
            added.append(content.strip())
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


def main() -> int:
    config = load_config()
    repos = config.get("repos", [])
    keywords = config.get("keywords") or None  # e.g. ["machine learning", "AI", "data"]

    if not repos:
        print("[warn] no repos listed in config.json — nothing to check.", file=sys.stderr)
        return 0

    state = load_state()
    all_new = {}

    for repo in repos:
        try:
            current_readme = get_readme(repo)
        except Exception as e:
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
        lines = []
        for repo, rows in all_new.items():
            lines.append(f"\n=== {repo} ===")
            lines.extend(rows)
        body = "New internship listings detected:\n" + "\n".join(lines)
        total = sum(len(v) for v in all_new.values())
        send_email(f"{total} new internship posting(s) found", body)
        print(f"Emailed {total} new listing(s).")
    else:
        print("No new listings found.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
