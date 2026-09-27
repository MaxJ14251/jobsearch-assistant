"""The example profile is somebody else's life, invented, and stays that way.

n7 said it had been de-personalised. It had not: every one of its five
experience bullets was the author's own, verbatim, with the real employer, the
real titles and the real dates, and both projects were the author's. A
newcomer who copies this file inherits a stranger's employment history and may
send it to an employer under their own name.

These tests are the thing that makes "de-personalised" checkable. The
comparison against the author's real profile runs only where that file exists
-- it is gitignored, so on any clone and in CI there is nothing to compare
against, and that test skips rather than lying.
"""

import unittest
from pathlib import Path

import yaml

from jsa.config import ROOT

EXAMPLE = ROOT / "profile" / "master_profile.example.yaml"
REAL = ROOT / "profile" / "master_profile.yaml"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def has_a_personal_profile() -> bool:
    """True only when a REAL profile exists to compare against.

    On a fresh clone the README says to copy the example to
    master_profile.yaml, and `tools/fresh_clone_check.py` does exactly that --
    so the two files are the same file and every bullet is "shared". That is
    the newcomer's starting state, not a leak.
    """
    if not REAL.exists():
        return False
    return REAL.read_text(encoding="utf-8") != EXAMPLE.read_text(encoding="utf-8")


def bullets(profile: dict) -> list[str]:
    out = []
    for section in ("experience", "projects"):
        for entry in profile.get(section) or []:
            for bullet in entry.get("bullets") or []:
                text = " ".join(str(bullet.get("text", "")).split())
                if text:
                    out.append(text)
    return out


def summaries(profile: dict) -> list[str]:
    return [" ".join(str(s.get("text", "")).split())
            for s in profile.get("summaries") or []]


class TestItIsNotTheAuthorsLife(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.example = load(EXAMPLE)

    @unittest.skipUnless(has_a_personal_profile(),
                         "no personal profile to compare against")
    def test_no_bullet_is_shared_with_the_real_profile(self):
        real = set(bullets(load(REAL)))
        shared = [b for b in bullets(self.example) if b in real]
        self.assertEqual(shared, [], "the example ships the author's own work")

    @unittest.skipUnless(has_a_personal_profile(),
                         "no personal profile to compare against")
    def test_no_summary_is_shared_with_the_real_profile(self):
        real = set(summaries(load(REAL)))
        self.assertEqual([s for s in summaries(self.example) if s in real], [])

    @unittest.skipUnless(has_a_personal_profile(),
                         "no personal profile to compare against")
    def test_no_employer_school_or_project_name_is_shared(self):
        real = load(REAL)
        theirs = {str(e.get("company") or "").lower()
                  for e in real.get("experience") or []}
        theirs |= {str(p.get("name") or "").lower()
                   for p in real.get("projects") or []}
        theirs |= {str(e.get("institution") or "").lower()
                   for e in real.get("education") or []}
        theirs |= {str(c.get("issuer") or "").lower()
                   for c in real.get("certifications") or []}
        theirs.discard("")
        # The job search assistant itself is in both on purpose: it is this
        # repository, and it is a legitimate portfolio project for anyone.
        theirs.discard("agentic job search assistant")

        mine = {str(e.get("company") or "").lower()
                for e in self.example.get("experience") or []}
        mine |= {str(p.get("name") or "").lower()
                 for p in self.example.get("projects") or []}
        mine |= {str(e.get("institution") or "").lower()
                 for e in self.example.get("education") or []}
        mine |= {str(c.get("issuer") or "").lower()
                 for c in self.example.get("certifications") or []}
        self.assertEqual(sorted(mine & theirs), [])


class TestItStillTeachesTheShape(unittest.TestCase):
    """De-personalising it must not flatten what a newcomer copies it for."""

    @classmethod
    def setUpClass(cls):
        cls.example = load(EXAMPLE)

    def test_two_roles_at_one_employer_with_ids(self):
        """The promotion case ADR 0011 fixed: without `id:` both roles collapse
        into one heading and every bullet prints under both."""
        experience = self.example["experience"]
        companies = [e["company"] for e in experience]
        self.assertEqual(len(companies), len(set(companies)) + 1,
                         "exactly one employer appears twice")
        for entry in experience:
            self.assertTrue(entry.get("id"), f"{entry['title']} has no id")

    def test_a_work_history_bullet_is_demonstrated(self):
        entries = [e for e in self.example["experience"]
                   if e.get("work_history_bullet")]
        self.assertTrue(entries, "nothing shows how work_history_bullet is used")
        for entry in entries:
            ids = {b["id"] for b in entry["bullets"]}
            self.assertIn(entry["work_history_bullet"], ids)

    def test_a_project_is_still_in_development(self):
        statuses = [p.get("status") for p in self.example["projects"]]
        self.assertIn("in_development", statuses,
                      "the honesty rule has nothing to bite on")

    def test_the_families_the_tracks_prefer_are_all_present(self):
        families = {e.get("family") for e in self.example["experience"]}
        families |= {p.get("family") for p in self.example["projects"]}
        for family in ("sales", "technical_field", "ai_engineering", "data_ml"):
            self.assertIn(family, families)

    def test_every_bullet_carries_tags_and_a_strength(self):
        for section in ("experience", "projects"):
            for entry in self.example[section]:
                for bullet in entry.get("bullets") or []:
                    self.assertTrue(bullet.get("tags"), bullet["id"])
                    self.assertIn(bullet.get("strength"), (1, 2, 3), bullet["id"])

    def test_it_claims_no_conferred_degree(self):
        """Half the postings state a degree requirement. The example teaches
        the harder answer, because the easy one needs no teaching."""
        from jsa.prep import assert_no_degree_claim
        for entry in self.example["education"]:
            credential = str(entry.get("credential") or "")
            assert_no_degree_claim(credential)
            self.assertIn("not conferred", credential.lower())

    def test_the_do_not_claim_list_is_not_empty(self):
        self.assertTrue(
            self.example["ats_keywords"]["aspirational_do_not_claim"],
            "a newcomer copying this gets no refusal list")


if __name__ == "__main__":
    unittest.main()
