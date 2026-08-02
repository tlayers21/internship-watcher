# Internship Watcher

## What this is

`internship-watcher` watches GitHub repos that track internship postings (like
SimplifyJobs) and emails you whenever a new listing shows up so you don't have
to manually check each repo or deal with using the GitHub watch repo feature.

**How it decides something is "new":** every run, it fetches the current
README for each repo you're tracking, compares it against a saved copy
from the last run, and pulls out any listing rows that weren't there
before. It's smart about a few things:

- Supports repos that use plain markdown tables (`| Company | Role | ... |`)
  **and** repos that use raw HTML tables (`<table><tr><td>...`) — both
  formats are auto-detected, no configuration needed.
- Ignores fields that naturally change for an *existing* listing over time
  (like a relative "Age" column ticking from `0d` to `3d`), so you don't
  get spammed about the same posting every run just because a day passed.
- Pulls out the actual "Apply" link for each new listing, not just raw
  page text, so the email is directly actionable.

**Where it runs:** entirely on GitHub Actions, on a schedule you control
(hourly by default). No server is required.

---

## How it works, in short

1. A scheduled GitHub Actions workflow runs the script on a timer.
2. The script fetches each tracked repo's README via the GitHub API.
3. It diffs the fresh copy against `state.json` (the last saved version,
   committed to the repo).
4. Any genuinely new listings get emailed to you via SMTP.
5. The script commits the updated `state.json` back to the repo, so the
   next run knows what it already saw.

---

## Setup

### 1. Fork this repo

Fork this repo, then clone your fork locally:

```bash
git clone https://github.com/<your-username>/internship-watcher.git
cd internship-watcher
```

### 2. Set up a sending email account

You'll need a Gmail account and an **app password** (not your real Gmail
password). You can use your current account or setup a dedicated account.

1. Create or choose a Gmail account to send from.
2. Turn on **2-Step Verification**: https://myaccount.google.com/security
   (this is required - app passwords don't exist as an option until 2FA
   is on).
3. Go to https://myaccount.google.com/apppasswords
4. Name it anything (e.g. `internship-watcher`) -> **Create**
5. Copy the 16-character password shown - you'll only see it once.

### 3. Add your GitHub repo secrets

In your forked repo: **Settings -> Secrets and variables -> Actions ->
Secrets tab -> New repository secret**. Add all three:

| Name | Value |
|---|---|
| `EMAIL_ADDRESS` | The Gmail address sending the alerts |
| `EMAIL_PASSWORD` | The 16-character app password from step 2 |
| `TO_EMAIL` | Where alerts go. Supports a single address or a comma-separated list for multiple emails, e.g. `you@gmail.com, friend@school.edu` |

You do *not* need to add a `GH_TOKEN` secret - GitHub automatically
provides `GITHUB_TOKEN` to every workflow run, which the script uses to
get a higher API rate limit than anonymous requests get.

### 4. Add the repos you want to track

Open `config.json`. This is the only file most people need to touch to
customize what gets watched:

```json
{
  "repos": [
    "some/simple-repo",
    { "repo": "SimplifyJobs/Summer2027-Internships", "branch": "dev" }
  ],
  "keywords": []
}
```

**Optional keyword filter:** leave `"keywords": []` to get every new
listing across all tracked repos. Or list terms (case-insensitive) -
a new listing only counts and gets emailed if it contains at least one:

```json
"keywords": ["machine learning", "AI", "data", "software engineer"]
```

### 5. Pick your check frequency

The included workflow (`.github/workflows/daily-check.yml`) runs every
hour by default:

```yaml
schedule:
  - cron: "0 * * * *"
```

### 6. First run

Go to your repo's **Actions** tab -> click **Internship Check** in the
left sidebar -> click **Run workflow** -> confirm. This first run only
saves a baseline snapshot of each repo's README -> it won't email you,
since there's nothing to compare against yet.

**Before this first run**, make sure `state.json` doesn't already contain
someone else's saved data (e.g. if you're picking this up from an
existing fork) -> delete it if so, so your baseline is built fresh from
scratch:

```bash
rm state.json
git add state.json
git commit -m "Reset state for fresh fork"
git push
```

Then trigger the workflow.

---

## Local development & testing

This project uses [uv](https://docs.astral.sh/uv/) for local dependency
management (the GitHub Actions workflow itself just uses plain `pip`,
since it only needs one runtime dependency).

1. Install uv:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
2. Install dependencies:
   ```bash
   uv sync
   ```
3. Set up local secrets:
   ```bash
   cp .env.example .env
   ```
   Fill in your real values in `.env` — it's gitignored, never committed.
4. Run it:
   ```bash
   uv run python check_internships.py
   ```
5. Lint:
   ```bash
   uv run ruff check .
   ```