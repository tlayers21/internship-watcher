# Internship Watcher

## About

`internship-watcher` watches GitHub repos that track internship postings (like
SimplifyJobs) and emails you whenever a new listing shows up so you don't have
to manually check each repo or deal with using the GitHub watch repo feature.

**How it decides something is "new":** every run, it fetches the current
README for each repo you're tracking, pulls out the listing rows, and
emails you the ones it has *never seen before*. It's smart about a few
things:

- Supports repos that use plain markdown tables (`| Company | Role | ... |`)
  **and** repos that use raw HTML tables (`<table><tr><td>...`) — both
  formats are auto-detected, no configuration needed.
- A listing's identity is its Apply link, with rotating tracking params
  (`utm_*`, `ref`) stripped but real job ids (`?job=342550`) kept. So a
  listing isn't re-sent when a display-only field changes (an "Age" column
  ticking `0d` -> `3d`, a 🔥 flag appearing), and two distinct jobs on the
  same job board don't get collapsed into one.
- `state.json` remembers **every listing ever seen**, not just the previous
  README. This matters: the upstream repos churn — rows vanish for a few
  hours and come back. Diffing against only the last snapshot reported each
  return as brand new, which is why old postings kept reappearing in your
  inbox. Replayed over a month of history, that accounted for 398 duplicate
  alerts out of 1468 sent.
- Rows that continue the listing above them (`↳`) inherit that company's
  name, so emails don't read `• ↳ — Summer Analyst Intern`.

**Where it runs:** entirely on GitHub Actions, on a schedule you control
(hourly by default). No server is required.

---

## How it works

1. A scheduled GitHub Actions workflow runs the script on a timer.
2. The script fetches each tracked repo's README via the GitHub API.
3. It keys every listing row by its Apply link and checks that against
   `state.json`, the set of every listing key already seen (committed to
   the repo).
4. Any genuinely new listings get emailed to you via SMTP.
5. Only after the email is sent does the script write `state.json` back —
   if SMTP fails, the listings stay unseen and are retried next run rather
   than being silently lost. The workflow commits the file back to the repo.

`state.json` is `{"version": 2, "seen": {repo: {listing_key: last_seen_date}}}`.
Keys not seen upstream for 120 days are forgotten, so the file stays small
(~140 KB) instead of growing forever. A v1 state file (which stored whole
README texts) is migrated automatically on the next run — it seeds from the
saved README, so upgrading doesn't email you hundreds of old listings.

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
  "keywords": [],
  "max_age_days": null
}
```

**Optional keyword filter:** leave `"keywords": []` to get every new
listing across all tracked repos. Or list terms (case-insensitive) -
a new listing only counts and gets emailed if it contains at least one:

```json
"keywords": ["machine learning", "AI", "data", "software engineer"]
```

**Optional age filter:** `"max_age_days": null` (the default) emails every
listing you haven't seen. Set it to a number to also skip listings whose
posted date is older than that many days:

```json
"max_age_days": 7
```

You usually don't need this. Since the run is hourly, a genuinely new
listing shows up as `0d` — old dates in your inbox were almost always
duplicates, which the seen-set already fixes. It's useful mainly for repos
that list a job's *original* posting date rather than when the row was
added. Listings with an unreadable date (`-`) are always treated as new, so
enabling this never silently hides something.

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
6. Run the tests:
   ```bash
   uv run pytest
   ```
   `test_check_internships.py` covers the dedup rules, including the
   regression that caused repeated alerts: a listing that disappears from
   the upstream README and comes back must be emailed exactly once.