"""Tests for the discovery filter.

Run: python -m unittest discover -s tests
"""

import unittest

from jsa.config import Preferences
from jsa.scoring import (
    dedup_key,
    is_non_us,
    required_years,
    score_job,
    title_score,
)

PREFS = Preferences(
    target_titles=[
        "AI Engineer",
        "Forward Deployed Engineer",
        "Solutions Architect",
        "Technical Account Manager",
        "Support Engineer",
        "Software Engineer",
    ],
    locations=["Remote (US)", "Los Angeles, CA", "Example Town, CA"],
    exclude_keywords=["Senior", "Staff", "Principal", "Engineering Manager"],
    have_keywords=["Python", "LLM", "Claude", "Prompt Engineering", "Automation"],
)


def job(**kw):
    base = {
        "title": "AI Engineer",
        "location": "Remote - US",
        "remote": "remote",
        "description": "",
    }
    base.update(kw)
    return base


class TestNonUS(unittest.TestCase):
    def test_flags_foreign_locations(self):
        for loc in ["Korea", "Dubai", "Stockholm", "Remote - Singapore",
                    "London, UK", "Toronto, Canada", "Europe", "Bengaluru, India"]:
            self.assertTrue(is_non_us(loc), loc)

    def test_allows_us_locations(self):
        for loc in ["San Francisco, CA", "Remote - US", "New York, NY",
                    "Example Town, California", "United States", ""]:
            self.assertFalse(is_non_us(loc), loc)

    def test_two_letter_country_code_is_not_a_state(self):
        # 'Berlin, DE' must not read as Delaware.
        self.assertTrue(is_non_us("Berlin, DE"))

    def test_explicit_us_wins_over_region_word(self):
        self.assertFalse(is_non_us("United States (Remote) - Europe team"))


class TestTitles(unittest.TestCase):
    def test_exact_match(self):
        score, match = title_score("Forward Deployed Engineer", PREFS.target_titles)
        self.assertEqual(score, 1.0)
        self.assertEqual(match, "Forward Deployed Engineer")

    def test_senior_titles_rejected(self):
        for t in ["Senior AI Engineer", "Staff Software Engineer",
                  "Principal Solutions Architect", "Sr. Support Engineer",
                  "Engineering Lead", "Software Engineer III"]:
            score, reasons = score_job(job(title=t), PREFS)
            self.assertEqual(score, 0.0, f"{t} -> {reasons}")

    def test_target_titles_containing_manager_survive(self):
        # 'Technical Account Manager' is a target; a blanket 'manager' reject
        # would have thrown it away.
        score, reasons = score_job(job(title="Technical Account Manager"), PREFS)
        self.assertGreater(score, 0.5, reasons)

    def test_solutions_architect_survives(self):
        score, _ = score_job(job(title="Solutions Architect"), PREFS)
        self.assertGreater(score, 0.5)

    def test_junior_variant_of_senior_word_survives(self):
        score, _ = score_job(job(title="Associate Solutions Architect"), PREFS)
        self.assertGreater(score, 0.5)


class TestYears(unittest.TestCase):
    def test_extracts_max(self):
        self.assertEqual(required_years("2+ years required, 5 years preferred"), 5)

    def test_none_when_absent(self):
        self.assertIsNone(required_years("We want someone great."))

    def test_rejects_too_senior(self):
        score, reasons = score_job(
            job(description="Requires 8+ years of experience."), PREFS
        )
        self.assertEqual(score, 0.0)
        self.assertIn("8", reasons[0])

    def test_accepts_within_range(self):
        score, _ = score_job(job(description="2 years of experience."), PREFS)
        self.assertGreater(score, 0.5)


class TestDedup(unittest.TestCase):
    def test_regional_variants_collapse(self):
        titles = [
            "Forward Deployed Engineer, Agentic Platform (Korea)",
            "Forward Deployed Engineer, Agentic Platform (West Coast)",
            "Forward Deployed Engineer, Agentic Platform",
        ]
        keys = {dedup_key(7, t) for t in titles}
        self.assertEqual(len(keys), 1, keys)

    def test_different_companies_do_not_collapse(self):
        self.assertNotEqual(
            dedup_key(1, "AI Engineer"), dedup_key(2, "AI Engineer")
        )

    def test_genuinely_different_titles_stay_separate(self):
        self.assertNotEqual(
            dedup_key(1, "AI Engineer"), dedup_key(1, "Support Engineer")
        )


class TestScoring(unittest.TestCase):
    def test_score_is_bounded(self):
        score, _ = score_job(
            job(description="Python LLM Claude Prompt Engineering Automation"), PREFS
        )
        self.assertLessEqual(score, 1.0)
        self.assertGreater(score, 0.0)

    def test_every_score_has_reasons(self):
        score, reasons = score_job(job(), PREFS)
        self.assertTrue(reasons)

    def test_foreign_location_rejected_outright(self):
        score, reasons = score_job(job(location="Seoul, Korea"), PREFS)
        self.assertEqual(score, 0.0)
        self.assertIn("outside the US", reasons[0])



class TestGenericWordFalsePositives(unittest.TestCase):
    """One shared generic word must not count as a title match."""

    def test_single_generic_word_is_not_a_match(self):
        score, match = title_score(
            "Manager, Globalization Production", PREFS.target_titles
        )
        self.assertEqual(score, 0.0, f"matched {match!r}")

    def test_producer_is_not_an_engineer_role(self):
        score, _ = title_score("Technical Producer II", PREFS.target_titles)
        self.assertEqual(score, 0.0)

    def test_two_shared_words_still_match(self):
        score, match = title_score("Software Engineer, Payments", PREFS.target_titles)
        self.assertEqual(score, 1.0)
        self.assertEqual(match, "Software Engineer")




class TestSourceNormalization(unittest.TestCase):
    """Adapter-level normalization, so a new board can't break an insert."""

    def test_employment_types_map_to_schema_enum(self):
        from jsa.sources import norm_employment
        allowed = {"full-time", "part-time", "contract", "internship", "unknown"}
        cases = {
            "FullTime": "full-time",      # Ashby
            "Full-time": "full-time",     # Lever / Workable
            "FULL_TIME": "full-time",     # Rivian
            "Regular": "full-time",       # Snap
            "PartTime": "part-time",
            "Contractor": "contract",
            "Internship": "internship",
            "Seasonal Whatever": "unknown",
            None: "unknown",
        }
        for raw, expected in cases.items():
            got = norm_employment(raw)
            self.assertEqual(got, expected, f"{raw!r} -> {got!r}")
            self.assertIn(got, allowed)

    def test_strip_html_produces_readable_text(self):
        from jsa.sources import strip_html
        out = strip_html("<p>Build <strong>LLM</strong> tools</p><li>Python</li>")
        self.assertIn("LLM", out)
        self.assertNotIn("<", out)

    def test_missing_config_fields_fail_loudly(self):
        from jsa.sources import fetch
        r = fetch({"kind": "workday", "tenant": "acme"})   # no `site`
        self.assertFalse(r.ok)
        self.assertIn("site", r.status)

    def test_unknown_custom_handler_fails_loudly(self):
        from jsa.sources import fetch
        r = fetch({"kind": "custom", "handler": "nope"})
        self.assertFalse(r.ok)
        self.assertIn("nope", r.status)



class TestPeopleManagerRoles(unittest.TestCase):
    """'Manager' as head noun = people management; trailing = IC role."""

    def test_head_noun_manager_rejected(self):
        for t in ["Manager, Software Engineering", "Manager of Support Engineering",
                  "Senior Manager, Data"]:
            score, reasons = score_job(job(title=t), PREFS)
            self.assertEqual(score, 0.0, f"{t} -> {reasons}")

    def test_trailing_manager_survives(self):
        for t in ["Technical Account Manager", "Enterprise Support Engineer"]:
            score, _ = score_job(job(title=t), PREFS)
            self.assertGreater(score, 0.5, t)

    def test_clearance_roles_excluded(self):
        prefs = Preferences(
            target_titles=["Software Engineer"],
            locations=["Remote (US)"],
            exclude_keywords=["Clearance", "Top Secret"],
            have_keywords=["Python"],
        )
        score, reasons = score_job(
            job(title="Software Engineer, HITL - Top Secret Clearance"), prefs
        )
        self.assertEqual(score, 0.0, reasons)



class TestCityMatching(unittest.TestCase):
    """City matching must not fire on substrings of other cities."""

    def setUp(self):
        self.prefs = Preferences(
            target_titles=["Software Engineer"],
            locations=["York, PA", "Baltimore, MD", "San Diego, CA",
                       "Los Angeles, CA", "Remote (US)"],
            have_keywords=["Python"],
        )

    def test_york_pa_does_not_match_new_york(self):
        from jsa.scoring import location_score
        score, reason = location_score("New York, NY", "onsite", self.prefs)
        self.assertNotIn("York, PA", reason)

    def test_york_pa_matches_actual_york(self):
        from jsa.scoring import location_score
        score, reason = location_score("York, PA", "onsite", self.prefs)
        self.assertEqual(score, 1.0)
        self.assertIn("York, PA", reason)

    def test_full_state_name_also_matches(self):
        from jsa.scoring import location_score
        score, _ = location_score("San Diego, California", "onsite", self.prefs)
        self.assertEqual(score, 1.0)

    def test_multi_location_string_picks_a_real_city(self):
        from jsa.scoring import location_score
        score, reason = location_score(
            "New York, NY; Baltimore, MD", "onsite", self.prefs
        )
        self.assertEqual(score, 1.0)
        self.assertIn("Baltimore, MD", reason)



class TestTrackPriority(unittest.TestCase):
    """Sales roles are in scope but must never outrank engineering."""

    def setUp(self):
        self.prefs = Preferences(
            target_titles=["Software Engineer", "AI Engineer",
                           "Technical Account Manager"],
            locations=["San Diego, CA", "Remote (US)"],
            have_keywords=["Python", "LLM"],
            fallback_titles=["Account Executive", "Account Manager"],
            fallback_weight=0.7,
        )

    def test_account_executive_is_now_in_scope(self):
        score, _ = score_job(
            job(title="Account Executive", location="San Diego, CA",
                remote="onsite"), self.prefs
        )
        self.assertGreater(score, 0.0, "AE roles should no longer be invisible")

    def test_engineering_outranks_equivalent_sales_role(self):
        common = dict(location="San Diego, CA", remote="onsite",
                      description="Python and LLM work")
        eng, _ = score_job(job(title="Software Engineer", **common), self.prefs)
        sales, _ = score_job(job(title="Account Executive", **common), self.prefs)
        self.assertGreater(eng, sales)

    def test_sales_match_is_labelled(self):
        _, reasons = score_job(
            job(title="Account Executive", location="San Diego, CA",
                remote="onsite"), self.prefs
        )
        self.assertTrue(any("sales track" in r for r in reasons), reasons)

    def test_technical_account_manager_stays_tier_one(self):
        from jsa.scoring import job_track
        # It sits in target_titles, so it must not be demoted to the sales tier
        # just because 'Account Manager' also appears in the fallback list.
        self.assertEqual(
            job_track("Technical Account Manager", self.prefs), "engineering"
        )

    def test_track_is_reported(self):
        from jsa.scoring import job_track
        self.assertEqual(job_track("Account Executive", self.prefs), "sales")
        self.assertEqual(job_track("Software Engineer", self.prefs), "engineering")



class TestNoPersonalDataCommitted(unittest.TestCase):
    """This project is published. Nothing personal may live in tracked files.

    Delegates to tools/scan_secrets.py rather than keeping a second list of
    forbidden strings. The duplicate list drifted once already: the scanner
    learned that a repo URL is not a profile link, and this test did not.
    """

    def test_scanner_reports_the_working_tree_clean(self):
        import subprocess
        import sys
        from jsa.config import ROOT

        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "scan_secrets.py")],
            cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        self.assertEqual(
            result.returncode, 0,
            (result.stdout or "") + "\n" + (result.stderr or ""))

    def test_real_profile_is_gitignored(self):
        from jsa.config import ROOT
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("profile/master_profile.yaml", ignored)

    def test_example_profile_exists_and_is_loadable(self):
        import yaml
        from jsa.config import ROOT
        p = ROOT / "profile" / "master_profile.example.yaml"
        self.assertTrue(p.exists(), "example profile must be committed")
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        self.assertIn("job_search_preferences", data)
        # Assert the property, not the literal: embedding the placeholder here
        # made tools/scan_secrets.py flag this file under a fresh clone, where
        # the example IS the active profile.
        email = data["identity"]["email"]
        self.assertTrue(email.endswith("@example.com"),
                        f"example profile must use a reserved domain, got {email!r}")
        self.assertNotIn("@gmail", email)


class TestConfigurability(unittest.TestCase):
    """Nothing candidate-specific may be hardcoded in the package."""

    def test_user_agent_has_no_baked_in_contact(self):
        import inspect
        from jsa import config
        src = inspect.getsource(config)
        self.assertNotIn("Example", src.lower())

    def test_user_agent_uses_env_override(self):
        import os
        from jsa.config import user_agent
        os.environ["JSA_CONTACT_EMAIL"] = "someone@example.org"
        try:
            self.assertIn("someone@example.org", user_agent())
        finally:
            os.environ.pop("JSA_CONTACT_EMAIL", None)

    def test_years_ceiling_comes_from_prefs(self):
        strict = Preferences(target_titles=["Engineer"], locations=["Remote (US)"],
                             max_years_experience=2)
        loose = Preferences(target_titles=["Engineer"], locations=["Remote (US)"],
                            max_years_experience=10)
        j = job(title="Engineer", description="Requires 6 years of experience.")
        self.assertEqual(score_job(j, strict)[0], 0.0)
        self.assertGreater(score_job(j, loose)[0], 0.0)

    def test_regions_come_from_the_profile(self):
        import inspect
        from jsa import cli
        src = inspect.getsource(cli)
        # The old hardcoded REGIONS dict must be gone.
        self.assertNotIn("Example Town", src)
        self.assertNotIn("Example Town", src)

    def test_region_clause_escapes_quotes(self):
        from jsa.cli import region_clause
        clause = region_clause({"x": ["O'Fallon"]}, "x")
        self.assertIn("O''Fallon", clause)

    def test_unknown_region_does_not_filter_everything_out(self):
        from jsa.cli import region_clause
        self.assertEqual(region_clause({}, "nope"), "1=1")


if __name__ == "__main__":
    unittest.main()
