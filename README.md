# Internship Watcher (intern-radar)

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
3. Run the script locally to test it (you'll need `EMAIL_ADDRESS`, `EMAIL_PASSWORD`,
   and optionally `TO_EMAIL` set as environment variables first — see step 2 under
   Deployment below for how to generate them):
   ```bash
   uv run python check_internships.py
   ```
4. Lint / format with ruff, which is included as a dev dependency:
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
   optionally add keywords to filter on:

   ```json
   {
     "repos": [
       "SimplifyJobs/Summer2026-Internships",
       "vanshb03/Summer2026-Internships"
     ],
     "keywords": ["machine learning", "AI", "data"]
   }
   ```

   - `repos` — required. Each entry is `owner/repo-name` (the part after
     `github.com/` in the repo's URL).
   - `keywords` — optional. Leave as `[]` to get every new row. If you list
     keywords, a new row only counts (and gets emailed) if it contains at
     least one of them, case-insensitive.

5. **Pick your run time**: edit the `cron` line in
   `.github/workflows/daily-check.yml`. Cron times are in UTC. For example,
   `0 13 * * *` = 1:00 PM UTC = 9:00 AM Eastern (during EDT).
   Use https://crontab.guru if you want to double check a schedule.

6. **First run**: go to the Actions tab in your repo, select
   "Daily Internship Check", and click "Run workflow" to trigger it manually.
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
- **Multiple times a day**: add more `cron` lines under `schedule:`.
