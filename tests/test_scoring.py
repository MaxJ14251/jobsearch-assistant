"""Tests for the discovery filter.

Run: python -m unittest discover -s tests
"""

import re
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
    locations=["Remote (US)", "Los Angeles, CA", "Sacramento, CA"],
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
                    "Sacramento, California", "United States", ""]:
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

    def test_an_age_is_not_experience(self):
        for text in ("Must be 21 years of age or older", "Must be 18 years or older",
                     "at least 18 years old", "18 years or over"):
            self.assertIsNone(required_years(text), text)
        self.assertEqual(required_years("Must be 18 years or older. 3 years of "
                                        "retail experience."), 3)
        self.assertEqual(required_years("2 years or more of experience"), 2)

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
            locations=["York, NE", "Baltimore, MD", "San Diego, CA",
                       "Los Angeles, CA", "Remote (US)"],
            have_keywords=["Python"],
        )

    def test_york_does_not_match_new_york(self):
        from jsa.scoring import location_score
        score, reason = location_score("New York, NY", "onsite", self.prefs)
        self.assertNotIn("York, NE", reason)

    def test_york_matches_actual_york(self):
        from jsa.scoring import location_score
        score, reason = location_score("York, NE", "onsite", self.prefs)
        self.assertEqual(score, 1.0)
        self.assertIn("York, NE", reason)

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
        p = ROOT / "jsa" / "resources" / "master_profile.example.yaml"
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
        """The operator's own details, read from their profile rather than
        written here: a name in a test file is the leak it is testing for."""
        import inspect
        from jsa import config
        src = inspect.getsource(config).lower()
        try:
            identity = (config.load_profile().get("identity") or {})
        except Exception:                      # noqa: BLE001 - fresh clone
            self.skipTest("no profile to compare against")
        from tools.scan_secrets import looks_like_a_placeholder
        for value in (identity.get("full_name"), identity.get("email"),
                      identity.get("phone")):
            # On a fresh clone the profile IS the example, whose "Your Full
            # Name" and you@example.com are words this source legitimately
            # contains.
            if looks_like_a_placeholder(str(value or "")):
                continue
            for part in re.split(r"[^A-Za-z0-9]+", str(value or "")):
                if len(part) >= 4 and not looks_like_a_placeholder(part):
                    self.assertNotIn(part.lower(), src)

    def test_user_agent_uses_env_override(self):
        import os
        from jsa.config import user_agent
        os.environ["JSA_CONTACT_EMAIL"] = "someone@example.org"
        try:
            self.assertIn("someone@example.org", user_agent())
        finally:
            os.environ.pop("JSA_CONTACT_EMAIL", None)

    def test_years_filter_reject_rank_off(self):
        """The operator's choice: drop, keep lower, or ignore."""
        j = job(title="Engineer", description="Requires 6 years of experience.")
        fits = job(title="Engineer", description="Requires 2 years of experience.")
        def prefs(mode):
            return Preferences(target_titles=["Engineer"], locations=["Remote (US)"],
                               max_years_experience=3, years_filter=mode)
        self.assertEqual(score_job(j, prefs("reject"))[0], 0.0)

        ranked, reasons = score_job(j, prefs("rank"))
        self.assertGreater(ranked, 0.0, "rank must keep it")
        self.assertLess(ranked, score_job(fits, prefs("rank"))[0],
                        "and rank it below one that fits")
        self.assertTrue(any("ranked lower" in r for r in reasons))

        ignored, reasons = score_job(j, prefs("off"))
        self.assertGreater(ignored, ranked, "off scores it as if years were unstated")
        self.assertTrue(any("ignored" in r for r in reasons))

    def test_years_filter_unset_is_what_it_always_was(self):
        from jsa.config import Preferences as P
        prefs = P.from_profile({"job_search_preferences": {
            "target_titles": ["Engineer"], "locations": ["Remote (US)"]}})
        self.assertEqual(prefs.years_filter, "reject")

    def test_years_filter_typo_is_refused_not_guessed(self):
        from jsa.config import ConfigError, Preferences as P
        with self.assertRaises(ConfigError) as ctx:
            P.from_profile({"job_search_preferences": {
                "target_titles": ["Engineer"], "locations": ["Remote (US)"],
                "years_filter": "loose"}})
        self.assertIn("rank", str(ctx.exception))

    def test_a_degree_requirement_never_moves_a_score(self):
        """Shown, never filtered. The operator would rather be told no."""
        base = job(title="Engineer", description="Build tools in Python. " * 5)
        asks = job(title="Engineer", description="Build tools in Python. " * 5
                   + "Bachelor's degree in Computer Science required.")
        p = Preferences(target_titles=["Engineer"], locations=["Remote (US)"])
        self.assertEqual(score_job(base, p)[0], score_job(asks, p)[0])

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
        # The old hardcoded REGIONS dict must be gone: no town from the
        # operator's own profile may appear in the code.
        from tests.test_anywhere import operator_places
        towns, _ = operator_places()
        for town in towns:
            self.assertNotIn(town, src)

    def test_region_clause_escapes_quotes(self):
        from jsa.cli import region_clause
        clause = region_clause({"x": ["O'Fallon"]}, "x")
        self.assertIn("O''Fallon", clause)

    def test_unknown_region_does_not_filter_everything_out(self):
        from jsa.cli import region_clause
        self.assertEqual(region_clause({}, "nope"), "1=1")


if __name__ == "__main__":
    unittest.main()
