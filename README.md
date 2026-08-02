# Internship Watcher

Watches GitHub repos (like SimplifyJobs/Summer2026-Internships) for new rows added
to their README, and emails you when new internships show up.

## Local dev setup (uv)

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

1. Install uv if you don't have it:
```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
```
2. From the project folder, install dependencies (creates a `.venv` automatically):
```bash
   uv sync
```
3. Set up your local secrets:
```bash
   cp .env.example .env
```
   Then open `.env` and fill in your real `EMAIL_ADDRESS`, Gmail app password
   for `EMAIL_PASSWORD`, and `TO_EMAIL`. This file is gitignored — it never
   gets committed.
4. Run the script locally to test it:
```bash
   uv run python check_internships.py
```
   The script loads `.env` automatically. First run just saves a baseline
   (no email sent). Delete a line from `state.json` afterward and re-run if
   you want to force-trigger the email path for testing.
5. Lint / format with ruff, which is included as a dev dependency:
```bash
   uv run ruff check .
```

`pyproject.toml` pins Python 3.11+ and lists `requests` as the only runtime
dependency, with `pytest` and `ruff` available for dev use. The GitHub Actions
workflow doesn't use uv — it just does a plain `pip install requests` on the
runner, since that's the only dependency it actually needs at run time.

## Deployment (GitHub Actions)

1. **Create a new GitHub repo** (private is fine) and push these files
   to it, keeping the folder structure:
   - `check_internships.py`
   - `config.json`
   - `.github/workflows/daily-check.yml`
   - This `README.md`

2. **Get a Gmail App Password** (don't use your real Gmail password):
   - Go to https://myaccount.google.com/apppasswords
   - Generate a 16-character app password for "Mail"
   - (Requires 2-Step Verification to be turned on for your Google account)

3. **Add repo secrets**: In your new GitHub repo, go to
   `Settings -> Secrets and variables -> Actions -> New repository secret`
   and add:
   - `EMAIL_ADDRESS` — the Gmail address sending the alert
   - `EMAIL_PASSWORD` — the app password from step 2
   - `TO_EMAIL` — where you want alerts sent. Supports a single address or a
     comma-separated list for multiple people, e.g.
     `you@gmail.com, friend@school.edu, other@gmail.com`

   You do NOT need to add a `GH_TOKEN` secret — the workflow automatically
   provides `GITHUB_TOKEN`, which is enough for reading public repo READMEs.

4. **Edit `config.json`** to whichever repos you actually want to track, and
   optionally add keywords to filter on. Each repo entry can be a plain
   `"owner/repo"` string (uses the default branch), or an object with a
   `branch` if the repo's real content lives somewhere other than the
   default branch (common for these internship-tracker repos — the main
   branch README is often just a landing page, and the actual table lives
   on a `dev` branch):

```json
   {
     "repos": [
       "some/simple-repo",
       { "repo": "SimplifyJobs/Summer2027-Internships", "branch": "dev" }
     ],
     "keywords": ["machine learning", "AI", "data"]
   }
```

   - `repos` — required. Plain string = default branch. Object with `branch`
     = fetch from that specific branch instead.
   - `keywords` — optional. Leave as `[]` to get every new row. If you list
     keywords, a new row only counts (and gets emailed) if it contains at
     least one of them, case-insensitive.

   **Tip:** if a repo shows "0 table rows found" when testing locally, check
   its GitHub page for a banner pointing to a different branch (e.g. "see
   the dev branch for the latest list") — that means the default branch
   README doesn't have the actual data.

5. **Pick your check frequency**: edit the `cron` line in
   `.github/workflows/daily-check.yml`. It currently runs every hour
   (`0 * * * *`). This is free on GitHub's tier regardless of repo
   visibility — public repos get unlimited Actions minutes, and private
   repos get 2,000 min/month free, and even hourly runs only use roughly
   250-350 of those. Use https://crontab.guru to build a different schedule
   if you want less/more frequent checks. GitHub doesn't guarantee
   scheduled runs fire at the exact minute — expect some drift, especially
   at busy times like the top of the hour.

6. **First run**: go to the Actions tab in your repo, select
   "Internship Check", and click "Run workflow" to trigger it manually.
   The first run just saves a baseline (no email, since it has nothing to
   compare against yet) — that's expected. Every run after that will detect
   and email you about new rows.

## How it works

- GitHub Actions runs the script daily on their infrastructure — no server
  needed on your end.
- The script fetches each repo's README via the GitHub API, diffs it against
  the last saved copy (`state.json`, committed back to your repo each run),
  and pulls out any newly added markdown table rows.
- If anything new is found, it emails you a plain-text summary grouped by repo.

## Customizing

- **Filter by keyword**: set the `keywords` list in `config.json` (see step 4
  above) — no code changes needed.
- **Different email provider**: change `SMTP_HOST` / `SMTP_PORT` secrets if
  you're not using Gmail (e.g. Outlook, a transactional email service like
  Resend or SendGrid's SMTP relay).
- **Different check frequency**: edit the `cron` line — see step 5 above.

## Optional: keep alert emails organized (Gmail) — personal note

This is a personal Gmail-organization tip, not part of this repo — if you
fork this project, you won't get this by default, and that's fine, it's
independent of the actual check/email pipeline.

If you don't want alert emails cluttering your main inbox long-term, but
still want a phone notification when one arrives:

1. In Gmail, create a filter matching the sender you configured as
   `EMAIL_ADDRESS` (Settings -> Filters and Blocked Addresses -> Create a
   new filter). Apply a label like `Internship Opportunities`. **Don't**
   check "Skip the Inbox" — mail that skips the Inbox generally won't
   trigger push notifications on mobile.
2. Optionally, set up a small Google Apps Script (in your own Google
   account, at script.google.com — unrelated to this GitHub repo) that
   auto-archives labeled mail out of your Inbox on an hourly timer, after
   it's had a chance to notify you.

## Using this for yourself (forking)

This project is written to be forked and reused — nothing here is
hardcoded to a specific person's repos or email:

1. Fork this repo to your own GitHub account (top-right "Fork" button on
   GitHub) rather than cloning it — a fork gives you your own independent
   copy with your own commit history, Actions runs, and secrets, with no
   ongoing link back to the original.
2. **Delete `state.json`** before your first run. It holds whoever set up
   the fork's saved README snapshots — starting fresh means your fork
   builds its own accurate baseline instead of inheriting someone else's.
3. Follow the Deployment steps above with your own repo — the only files
   you need to touch are `config.json` (which repos/keywords you want) and
   the three GitHub Secrets (your own sending/receiving email addresses).
4. `check_internships.py`, the workflow, and `pyproject.toml` don't need
   any edits for a standard setup — they're generic.
5. If you're tracking a different internship-listing repo that isn't in
   the SimplifyJobs/vanshb03/dev-branch family, just check whether its
   README's actual table is markdown pipe (`| Company | Role | ... |`) or
   raw HTML (`<table><tr><td>...`) — both are supported automatically, no
   code changes needed either way.