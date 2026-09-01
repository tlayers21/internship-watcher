"""Regression tests for the alert-dedup logic.

The bug these exist to prevent: upstream READMEs drop listings and re-add them
hours later. Diffing against only the previous README reported every return as
a brand-new listing.
"""

from datetime import date

import check_internships as ci


def html_row(company, role, location, url, posted):
    return (
        f"<tr>\n<td>{company}</td>\n<td>{role}</td>\n<td>{location}</td>\n"
        f'<td><div align="center"><a href="{url}">'
        f'<img src="https://i.imgur.com/x.png" alt="Apply"></a></div></td>\n'
        f"<td>{posted}</td>\n</tr>"
    )


def readme(*rows):
    return "<table><tr><th>Company</th></tr>\n" + "\n".join(rows) + "\n</table>"


GS = html_row("Goldman Sachs", "Quant Strategist Intern", "NYC",
              "https://higher.gs.com/roles/175424?type=students&utm_source=Simplify&ref=Simplify",
              "16d")
STRYKER = html_row("Stryker", "SWE Intern", "San Jose, CA",
                   "https://stryker.wd1.myworkdayjobs.com/job/R572624?utm_source=Simplify", "0d")


def run(seen, text, **kw):
    """Do what main() does for one repo: collect new listings, then mark all seen."""
    new, all_keys = ci.find_new_listings(seen, text, **kw)
    for key in all_keys:
        seen[key] = "2026-09-01"
    return new


class TestChurnDedup:
    def test_flapping_row_is_emailed_exactly_once(self):
        """The actual bug: present -> absent -> present must not re-alert."""
        seen = {}
        run(seen, readme(GS, STRYKER))          # baseline run establishes both
        assert run(seen, readme(STRYKER)) == []  # GS vanishes upstream
        assert run(seen, readme(GS, STRYKER)) == []  # ...and comes back

    def test_genuinely_new_row_is_reported(self):
        seen = {}
        run(seen, readme(GS))
        new = run(seen, readme(GS, STRYKER))
        assert [n["company"] for n in new] == ["Stryker"]

    def test_age_column_ticking_up_is_not_new(self):
        seen = {}
        run(seen, readme(html_row("Stryker", "SWE Intern", "CA", "https://x.com/j/1", "0d")))
        assert run(seen, readme(html_row("Stryker", "SWE Intern", "CA",
                                         "https://x.com/j/1", "5d"))) == []

    def test_filtered_rows_are_still_marked_seen(self):
        """A row skipped by the age filter must not resurface if the filter loosens."""
        seen = {}
        run(seen, readme(GS), max_age_days=7)   # GS is 16d, filtered out
        assert run(seen, readme(GS)) == []      # filter off: still not "new"


class TestRowKey:
    def test_tracking_params_are_stripped(self):
        a = ci.row_key(html_row("A", "r", "l", "https://x.com/j?utm_source=Simplify&ref=S", "0d"))
        b = ci.row_key(html_row("A", "r", "l", "https://x.com/j", "9d"))
        assert a == b

    def test_job_id_in_query_string_survives(self):
        """Regression: split('?')[0] collapsed 6 distinct Textron roles into one key."""
        base = "https://textron.taleo.net/careersection/textron/jobdetail.ftl"
        keys = {
            ci.row_key(html_row("Textron", "r", "l", f"{base}?job={jid}&utm_source=Simplify", "0d"))
            for jid in (342550, 342688, 342915)
        }
        assert len(keys) == 3

    def test_param_order_does_not_change_key(self):
        a = ci.row_key(html_row("A", "r", "l", "https://x.com/j?b=2&a=1", "0d"))
        b = ci.row_key(html_row("A", "r", "l", "https://x.com/j?a=1&b=2", "0d"))
        assert a == b

    def test_linkless_continuation_rows_do_not_collide(self):
        """'↳|SWE Intern|Chantilly, VA|🔒' is identical across employers."""
        rows = [
            "| Acme | SWE Intern | Chantilly, VA | 🔒 | Aug 21 |",
            "| ↳ | SWE Intern | Chantilly, VA | 🔒 | Aug 21 |",
            "| Globex | SWE Intern | Chantilly, VA | 🔒 | Aug 21 |",
            "| ↳ | SWE Intern | Chantilly, VA | 🔒 | Aug 21 |",
        ]
        keys = {listing["key"] for listing in ci.summarize_rows(rows)}
        assert len(keys) == 4


class TestSummarize:
    def test_continuation_inherits_company(self):
        rows = ci.extract_rows(readme(GS, html_row("↳", "Summer Analyst", "Dallas, TX",
                                                   "https://higher.gs.com/roles/171546", "16d")))
        assert [r["company"] for r in ci.summarize_rows(rows)] == ["Goldman Sachs"] * 2

    def test_trending_flag_stripped_from_company(self):
        rows = ci.extract_rows(readme(html_row("🔥 <strong>AMD</strong>", "r", "l",
                                               "https://x.com/j", "0d")))
        assert ci.summarize_rows(rows)[0]["company"] == "AMD"

    def test_markdown_pipe_rows(self):
        rows = ci.extract_rows(
            "| Company | Role | Location | Apply | Added |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| Susquehanna | Quant Intern | New York, NY | [apply](https://careers.sig.com/jobs/10822) | 2026-07-21 |"
        )
        listing = ci.summarize_rows(rows)[0]
        assert listing["company"] == "Susquehanna"
        assert listing["link"] == "https://careers.sig.com/jobs/10822"
        assert listing["posted"] == "2026-07-21"


class TestAgeParsing:
    TODAY = date(2026, 9, 1)

    def test_relative_days_and_months(self):
        assert ci.parse_age_days("0d", self.TODAY) == 0
        assert ci.parse_age_days("16d", self.TODAY) == 16
        assert ci.parse_age_days("1mo", self.TODAY) == 30

    def test_iso_date(self):
        assert ci.parse_age_days("2026-08-25", self.TODAY) == 7

    def test_month_day_without_year(self):
        assert ci.parse_age_days("Aug 21", self.TODAY) == 11

    def test_month_day_rolls_back_a_year_when_in_future(self):
        assert ci.parse_age_days("Dec 15", self.TODAY) == 260

    def test_unparseable_is_none(self):
        assert ci.parse_age_days("-", self.TODAY) is None
        assert ci.parse_age_days(None, self.TODAY) is None

    def test_unknown_age_is_never_filtered_out(self):
        row = html_row("Acme", "r", "l", "https://x.com/j", "-")
        new, _ = ci.find_new_listings({}, readme(row), max_age_days=7)
        assert len(new) == 1


class TestState:
    def test_v1_migration_seeds_instead_of_flooding(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "state.json").write_text(
            __import__("json").dumps({"Simplify/Repo": readme(GS, STRYKER)})
        )
        seen = ci.load_state()
        assert len(seen["Simplify/Repo"]) == 2
        assert ci.find_new_listings(seen["Simplify/Repo"], readme(GS, STRYKER))[0] == []

    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ci.save_state({"a/b": {"https://x.com/j": "2026-09-01"}})
        assert ci.load_state() == {"a/b": {"https://x.com/j": "2026-09-01"}}

    def test_prune_forgets_only_long_absent_keys(self):
        today = date(2026, 9, 1)
        kept = ci.prune({"fresh": "2026-08-30", "ancient": "2026-01-01"}, today)
        assert set(kept) == {"fresh"}
