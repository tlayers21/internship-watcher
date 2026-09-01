#!/usr/bin/env python3
"""
Watches GitHub repos' READMEs for newly added rows (new internship postings)
and emails you a summary when new ones show up.

How it decides something is "new":
- It fetches the current README for each repo listed in config.json and pulls
  out the listing rows, supporting two table formats: raw HTML
  (<table><tr>...</tr></table>) and markdown pipe tables (| Company | ... |).
- A row's identity is its apply link (minus rotating tracking params), not its
  full text — so a listing isn't re-reported just because a display-only field
  changed (an "Age" column ticking up, a trending flag appearing).
- state.json holds every listing key ever seen, so a listing is emailed exactly
  once. This matters because the upstream READMEs churn: rows disappear for a
  few hours and come back. Diffing against only the previous README (what this
  script used to do) reported each return as a brand-new listing — 29% of all
  alerts sent were duplicates, and 96% of the "old" listings people complained
  about were things they had already been emailed days earlier.

State is stored in state.json and committed back to the repo by the GitHub
Action, so the next run knows what it already saw.
"""

import base64
import json
import os
import re
import smtplib
import sys
from datetime import UTC, date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()  # loads .env if present (local dev)
except ImportError:
    pass  # python-dotenv isn't installed in CI (GitHub Actions sets secrets as real env vars)

CONFIG_FILE = "config.json"
STATE_FILE = "state.json"
GITHUB_API = "https://api.github.com"

STATE_VERSION = 2
# Drop keys we haven't seen upstream in this long. Rows flap in and out on an
# hours timescale, so this is far outside the churn window — it only bounds
# state.json growth.
FORGET_AFTER_DAYS = 120

# Query params that rotate between fetches and must not be part of a row's
# identity. Everything else is kept, because some boards put the job id in the
# query string (e.g. taleo's ?job=342550).
TRACKING_PARAMS = {"ref", "gh_src", "utm_source", "utm_medium", "utm_campaign",
                   "utm_term", "utm_content"}

CONTINUATION = "↳"  # marks a row that inherits the company above it


def today_utc() -> date:
    """The Action runs on UTC cron and upstream ages are UTC-relative, so stay in UTC."""
    return datetime.now(UTC).date()


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
    Order is document order, which summarize_rows relies on to resolve "↳" rows.
    """
    if "<tr>" in content:
        rows = re.findall(r"<tr>.*?</tr>", content, re.DOTALL)
        return [r for r in rows if "<th>" not in r]
    return [line.strip() for line in content.splitlines() if is_candidate_pipe_row(line)]


def strip_tags(s: str) -> str:
    """Strip HTML tags and collapse whitespace, for turning a cell's HTML into plain text."""
    text = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", text).strip()


def extract_link(cell_html: str) -> str | None:
    """
    Pull a listing's URL out of a single cell — this is reliably the 'Apply' link.
    Supports both raw HTML (href="...") and markdown ([text](url)) link syntax,
    since different tracked repos use either for their Apply column.
    """
    href = re.search(r'href="([^"]+)"', cell_html)
    if href:
        return href.group(1)
    md_link = re.search(r"\((https?://[^)]+)\)", cell_html)
    return md_link.group(1) if md_link else None


def canonical_link(url: str) -> str:
    """
    Normalize an apply URL into a stable identity: drop rotating tracking params
    and sort what's left. Keeping non-tracking params matters — boards like
    taleo and greenhouse carry the job id there (?job=342550), and blindly
    dropping the whole query string collapsed 25 distinct listings into 11 keys,
    silently hiding the rest forever.
    """
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(sorted(kept)), ""))


def row_cells(row: str) -> list[str]:
    """Split a row into its cells, whichever table format it came from."""
    if row.startswith("<tr>"):
        return re.findall(r"<td>(.*?)</td>", row, re.DOTALL)
    return [p.strip() for p in row.strip("|").split("|")]


def row_key(row: str, company: str = "") -> str:
    """
    Stable identity for a row. Prefers the listing's apply link because that's
    what actually identifies a specific job posting — unlike the row's full
    text, it doesn't change when a volatile display-only field does (a relative
    'Age' column '0d' -> '5d', a trending flag appearing later).

    Falls back to the row text minus its trailing age/date cell. The fallback is
    prefixed with the resolved company, because a linkless '↳' row is otherwise
    just "↳|Software Engineer Intern|Chantilly, VA|🔒" and collides across
    employers.
    """
    cells = row_cells(row)
    link = extract_link(cells[3]) if len(cells) > 3 else None
    if link:
        return canonical_link(link)

    if row.startswith("<tr>"):
        # drop the last <td>...</td> before </tr> — that's the Age column
        text = re.sub(r"<td>[^<]*</td>\s*</tr>\s*$", "</tr>", row)
    else:
        text = "|".join(cells[:-1]) if len(cells) > 1 else row
    return f"{company}\x1f{text}" if company else text


def clean_company(cell: str) -> str:
    """Cell text minus markdown emphasis and the leading 🔥 'trending' flag."""
    name = re.sub(r"[\[\]*]", "", strip_tags(cell))
    return name.removeprefix("🔥").strip()


def parse_age_days(posted: str | None, today: date | None = None) -> int | None:
    """
    Days since a listing was posted, or None if unparseable. The tracked repos
    use three different formats:
      '16d' / '1mo'  (SimplifyJobs, relative)
      'Aug 21'       (vanshb03, no year)
      '2026-07-21'   (sndsh404, absolute; may also be '-')
    """
    if not posted:
        return None
    text = posted.strip()
    today = today or today_utc()

    m = re.fullmatch(r"(\d+)\s*d", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"(\d+)\s*mo", text, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 30
    m = re.fullmatch(r"(\d+)\s*y", text, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 365

    try:
        return (today - date.fromisoformat(text)).days
    except ValueError:
        pass

    for fmt in ("%b %d", "%b %d %Y", "%B %d", "%B %d %Y"):
        try:
            parsed = datetime.strptime(text, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
        if "%Y" not in fmt:
            parsed = parsed.replace(year=today.year)
            # No year given: a date more than a few days ahead is last year's.
            if parsed > today + timedelta(days=3):
                parsed = parsed.replace(year=today.year - 1)
        return (today - parsed).days

    return None


def summarize_rows(rows: list[str]) -> list[dict]:
    """
    Turn raw rows (HTML or markdown) into structured dicts for the email:
    {"company", "role", "location", "link", "posted", "key"}

    Rows whose company cell is '↳' continue the listing above them, so the last
    real company name is carried forward — otherwise the email reads
    "• ↳ — Summer Analyst Intern" with no employer at all.
    """
    out: list[dict] = []
    company = "?"
    for row in rows:
        cells = row_cells(row)
        raw_company = clean_company(cells[0]) if cells else ""
        if raw_company and raw_company != CONTINUATION:
            company = raw_company
        out.append({
            "company": company,
            "role": strip_tags(cells[1]) if len(cells) > 1 else "?",
            "location": strip_tags(cells[2]) if len(cells) > 2 else "?",
            "link": extract_link(cells[3]) if len(cells) > 3 else None,
            "posted": strip_tags(cells[4]) if len(cells) > 4 else None,
            "key": row_key(row, company),
        })
    return out


def find_new_listings(
    seen: dict,
    readme: str,
    keywords: list[str] | None = None,
    max_age_days: int | None = None,
) -> tuple[list[dict], set[str]]:
    """
    Returns (listings to email, every key present in this README).

    The caller marks *all* returned keys as seen — including ones filtered out
    by keyword or age — so a row can never resurface as "new" later.
    """
    listings = summarize_rows(extract_rows(readme))
    all_keys = {listing["key"] for listing in listings}

    new = []
    for listing in listings:
        if listing["key"] in seen:
            continue
        if keywords:
            haystack = " ".join(
                str(listing[f]) for f in ("company", "role", "location")
            ).lower()
            if not any(k.lower() in haystack for k in keywords):
                continue
        if max_age_days is not None:
            age = parse_age_days(listing["posted"])
            if age is not None and age > max_age_days:
                continue  # unparseable ages fall through: never silently dropped
        new.append(listing)
    return new, all_keys


def load_state() -> dict[str, dict[str, str]]:
    """
    Returns {repo: {listing_key: last_seen_iso_date}}.

    Migrates the v1 format ({repo: <entire previous README text>}) by seeding
    the seen-set from that README, so upgrading doesn't email ~850 listings at
    once.
    """
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if raw.get("version") == STATE_VERSION:
        return raw.get("seen", {})

    today = today_utc().isoformat()
    seen: dict[str, dict[str, str]] = {}
    for repo, readme in raw.items():
        if not isinstance(readme, str):
            continue  # not v1 either; skip rather than guess
        seen[repo] = {
            listing["key"]: today for listing in summarize_rows(extract_rows(readme))
        }
        print(f"[info] migrated {repo} to state v{STATE_VERSION}: {len(seen[repo])} known listings")
    return seen


def save_state(seen: dict[str, dict[str, str]]) -> None:
    """Write state sorted, so the bot's hourly commit is a small, readable diff."""
    payload = {"version": STATE_VERSION, "seen": seen}
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")


def prune(keys: dict[str, str], today: date) -> dict[str, str]:
    """Forget keys absent from upstream for FORGET_AFTER_DAYS, to bound growth."""
    cutoff = (today - timedelta(days=FORGET_AFTER_DAYS)).isoformat()
    return {k: v for k, v in keys.items() if v >= cutoff}


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


def format_email(all_new: dict[str, list[dict]]) -> str:
    sections = []
    for repo, listings in all_new.items():
        block = [repo, "-" * len(repo)]
        for listing in listings:
            block.append(f"• {listing['company']} — {listing['role']} ({listing['location']})")
            if listing["posted"]:
                block.append(f"  Posted: {listing['posted']}")
            if listing["link"]:
                block.append(f"  Apply: {listing['link']}")
            block.append("")  # blank line between listings
        sections.append("\n".join(block))
    return "New internship listings found:\n\n" + "\n\n".join(sections)


def normalize_repo_entry(entry) -> tuple[str, str | None]:
    """Accepts either 'owner/repo' or {'repo': 'owner/repo', 'branch': 'dev'}."""
    if isinstance(entry, str):
        return entry, None
    return entry["repo"], entry.get("branch")


def main() -> int:
    config = load_config()
    repos = config.get("repos", [])
    keywords = config.get("keywords") or None  # e.g. ["machine learning", "AI", "data"]
    max_age_days = config.get("max_age_days")  # null/absent = no age filter

    if not repos:
        print("[warn] no repos listed in config.json — nothing to check.", file=sys.stderr)
        return 0

    seen = load_state()
    today = today_utc()
    stamp = today.isoformat()
    all_new: dict[str, list[dict]] = {}

    for entry in repos:
        repo, branch = normalize_repo_entry(entry)
        try:
            readme = get_readme(repo, ref=branch)
        except Exception as e:  # noqa: BLE001 - one repo's failure must not abort the whole run
            print(f"[warn] failed to fetch {repo}: {e}", file=sys.stderr)
            continue

        known = seen.get(repo)
        if known is None:
            known = seen[repo] = {}
            _, all_keys = find_new_listings({}, readme)
            print(f"[info] no prior state for {repo}, saving baseline only "
                  f"({len(all_keys)} listings)")
        else:
            new, all_keys = find_new_listings(known, readme, keywords, max_age_days)
            if new:
                all_new[repo] = new

        # Mark everything currently upstream as seen — including rows we chose
        # not to email — and refresh the timestamp on rows still present.
        for key in all_keys:
            known[key] = stamp
        seen[repo] = prune(known, today)

    if all_new:
        total = sum(len(v) for v in all_new.values())
        # Send before persisting: if SMTP fails we must not have already
        # recorded these listings as seen, or they'd never be reported at all.
        send_email(f"{total} new internship posting(s) found", format_email(all_new))
        save_state(seen)
        print(f"Emailed {total} new listing(s).")
    else:
        save_state(seen)
        print("No new listings found.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
