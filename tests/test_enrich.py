"""Tests for LLM enrichment validation. No network — validation is the risk."""

import json
import unittest

from jsa.enrich import Enrichment, validate


class TestValidation(unittest.TestCase):
    def test_clean_response(self):
        e = validate({
            "degree_required": True, "clearance_required": False,
            "years_required": 2, "seniority": "entry",
            "tech_stack": ["Python", "Docker"], "note": "BS required",
        })
        self.assertTrue(e.degree_required)
        self.assertFalse(e.clearance_required)
        self.assertEqual(e.years_required, 2)
        self.assertEqual(e.seniority, "entry")
        self.assertEqual(e.tech_stack, ["Python", "Docker"])

    def test_non_dict_rejected(self):
        for bad in ([], "text", 5, None):
            with self.assertRaises(ValueError):
                validate(bad)

    def test_template_echo_is_discarded(self):
        """The exact failure seen on 2026-09-14 must not become data."""
        e = validate({"tech_stack": ["up to 5 primary technologies"]})
        self.assertEqual(e.tech_stack, [])

    def test_unknown_is_none_not_false(self):
        """NULL means 'we don't know', which is not the same as 'no'."""
        e = validate({})
        self.assertIsNone(e.degree_required)
        self.assertIsNone(e.clearance_required)
        self.assertIsNone(e.years_required)

    def test_string_booleans_coerced(self):
        e = validate({"degree_required": "true", "clearance_required": "FALSE"})
        self.assertIs(e.degree_required, True)
        self.assertIs(e.clearance_required, False)

    def test_garbage_booleans_become_none(self):
        e = validate({"degree_required": "maybe", "clearance_required": 7})
        self.assertIsNone(e.degree_required)
        self.assertIsNone(e.clearance_required)

    def test_absurd_years_discarded(self):
        self.assertIsNone(validate({"years_required": 900}).years_required)
        self.assertIsNone(validate({"years_required": -3}).years_required)
        self.assertEqual(validate({"years_required": 5}).years_required, 5)

    def test_bool_is_not_a_year(self):
        # True is an int in Python; it must not become years_required=1.
        self.assertIsNone(validate({"years_required": True}).years_required)

    def test_invalid_seniority_dropped(self):
        self.assertIsNone(validate({"seniority": "wizard"}).seniority)
        self.assertEqual(validate({"seniority": " Mid "}).seniority, "mid")

    def test_tech_stack_capped_and_cleaned(self):
        e = validate({"tech_stack": ["a"*50, "Go", 42, "", "Rust", "C", "D", "E"]})
        self.assertNotIn(42, e.tech_stack)
        self.assertLessEqual(len(e.tech_stack), 5)
        self.assertIn("Go", e.tech_stack)

    def test_note_truncated(self):
        self.assertLessEqual(len(validate({"note": "x" * 900}).note), 200)

    def test_non_list_stack_ignored(self):
        self.assertEqual(validate({"tech_stack": "Python, Go"}).tech_stack, [])


class TestEnrichConfig(unittest.TestCase):
    def test_thinking_is_disabled_in_the_call(self):
        """Thinking on = 2% parse rate. This must never regress."""
        import inspect
        from jsa import enrich
        src = inspect.getsource(enrich.enrich_one)
        self.assertIn("thinking=False", src)

    def test_cheap_model_first(self):
        from jsa.enrich import ENRICH_MODELS
        self.assertIn("lightning", ENRICH_MODELS[0])
        self.assertGreater(len(ENRICH_MODELS), 1, "needs a fallback")


class TestPendingQuery(unittest.TestCase):
    def test_reenriches_when_description_changed(self):
        import inspect
        from jsa import enrich
        src = inspect.getsource(enrich.pending)
        self.assertIn("enrichment_hash IS NOT j.description_hash", src)


class TestRedoingOnePosting(unittest.TestCase):
    """`jsa enrich --job N`. Jobs 288 and 514 were enriched when this read
    6,000 characters; their degree lines sit past 6,800, so the stored fact
    said no degree was required. The only way to redo two rows was --force
    over eight hundred."""

    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path
        from jsa import db

        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        path = self.dir / "t.db"
        db.init_db(path)
        self.con = db.connect(path)
        self.addCleanup(self.con.close)
        self.con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'A','a')")
        # Fresh (already enriched, text unchanged) and scoring below any
        # threshold: exactly what the normal pass skips.
        for job_id in (1, 2):
            self.con.execute(
                "INSERT INTO jobs (id,company_id,title,url,description,"
                "description_hash,enrichment_hash,enriched_at,match_score) "
                "VALUES (?,1,'Engineer',?,?,'h','h','2026-09-15T00:00:00Z',0.1)",
                (job_id, f"https://a.test/{job_id}", "x" * 400))
        self.con.commit()

    def pending(self, **kw):
        from jsa.enrich import pending
        return [r["id"] for r in pending(self.con, min_score=0.5, limit=100,
                                         force=False, **kw)]

    def test_the_normal_pass_skips_them(self):
        self.assertEqual(self.pending(), [])

    def test_naming_one_redoes_it_whatever_its_score_or_freshness(self):
        self.assertEqual(self.pending(job_ids=[2]), [2])

    def test_only_the_named_ones(self):
        self.assertEqual(self.pending(job_ids=[1, 2]), [1, 2])

    def test_an_unknown_id_is_nothing_not_everything(self):
        self.assertEqual(self.pending(job_ids=[99]), [])


class TestSchemaAgreement(unittest.TestCase):
    """The validator's vocabulary must match the database's CHECK constraint.

    These drifted once: the validator accepted "lead", the column did not, and
    a 486-listing pass died partway through on IntegrityError.
    """

    def _schema_seniority(self):
        import re
        from jsa.config import SCHEMA_PATH
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        m = re.search(r"seniority\s+TEXT\s+CHECK\s*\(seniority IN \((.*?)\)\)", sql, re.S)
        self.assertIsNotNone(m, "could not find the seniority CHECK constraint")
        return {v.strip().strip("'") for v in m.group(1).split(",")}

    def test_validator_vocabulary_is_a_subset_of_the_column(self):
        from jsa.enrich import SENIORITY
        allowed = self._schema_seniority()
        self.assertTrue(
            SENIORITY <= allowed,
            f"validator allows values the column rejects: {SENIORITY - allowed}",
        )

    def test_every_alias_target_is_storable(self):
        from jsa.enrich import SENIORITY_ALIASES
        allowed = self._schema_seniority()
        bad = {v for v in SENIORITY_ALIASES.values() if v not in allowed}
        self.assertEqual(bad, set(), f"aliases map onto unstorable values: {bad}")

    def test_lead_is_mapped_not_dropped(self):
        from jsa.enrich import validate
        self.assertEqual(validate({"seniority": "lead"}).seniority, "senior")

    def test_prompt_only_offers_storable_values(self):
        from jsa.enrich import PROMPT
        allowed = self._schema_seniority()
        import re
        offered = set(re.findall(r'"(\w+)"', PROMPT.split('"seniority"')[1].split("\n")[0]))
        self.assertTrue(offered <= allowed, f"prompt offers unstorable: {offered - allowed}")


if __name__ == "__main__":
    unittest.main()
