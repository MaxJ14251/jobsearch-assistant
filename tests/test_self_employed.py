"""An independent-work entry that covers a gap on the resume.

`self_employed: true` keeps it out of the employment arithmetic (years of
experience, the gap drill, the work-history rule). `always_include: true`
prints it on every resume instead of only when its bullets match a posting.
"""

import datetime as dt
import unittest

from jsa import facts
from jsa.tailor import always_included, most_recent_job, select_bullets

TODAY = dt.date(2026, 11, 10)

EMPLOYER = {
    "id": "exp_sales", "company": "Acme", "title": "Sales Rep", "family": "sales",
    "start": "2021-01", "end": "2023-11", "work_history_bullet": "b_sales",
    "bullets": [{"id": "b_sales", "strength": 1,
                 "text": "Sold alarm systems to commercial clients.",
                 "tags": ["sales", "customer-facing"]}],
}
INDEPENDENT = {
    "id": "exp_indep", "company": "Own Studio",
    "title": "Freelance Builder", "family": "ai_engineering",
    "start": "2023-12", "current": True,
    "self_employed": True, "always_include": True,
    "bullets": [
        {"id": "b_indep_build", "strength": 1,
         "text": "Built and released an open-source Python tool with Claude.",
         "tags": ["python", "llm"]},
        {"id": "b_indep_other", "strength": 1,
         "text": "Worked on an unrelated side pursuit.", "tags": []},
    ],
}
PROJECT = {
    "id": "proj_ai", "name": "Pipeline", "family": "ai_engineering",
    "bullets": [
        {"id": f"b_ai_{i}", "strength": 1,
         "text": f"Built Python pipeline stage {i} using Claude for video.",
         "tags": ["python", "llm", "claude", "pipeline"]}
        for i in range(6)
    ],
}


def profile(*entries):
    return {"experience": list(entries), "projects": [PROJECT]}


class Arithmetic(unittest.TestCase):
    def test_years_count_employment_only(self):
        with_indep = facts.years_of_experience(profile(INDEPENDENT, EMPLOYER), TODAY)
        without = facts.years_of_experience(profile(EMPLOYER), TODAY)
        self.assertEqual(with_indep, without)

    def test_gap_drill_still_fires(self):
        gap = facts.gap_since(profile(INDEPENDENT, EMPLOYER), TODAY)
        self.assertIsNotNone(gap)
        self.assertEqual(gap[1]["id"], "exp_sales")

    def test_most_recent_job_is_the_employer(self):
        self.assertEqual(most_recent_job(profile(INDEPENDENT, EMPLOYER))["id"],
                         "exp_sales")


class Selection(unittest.TestCase):
    POSTING = "Python LLM engineer building Claude pipelines."

    def ids(self, **kw):
        return [b.id for b in select_bullets(profile(INDEPENDENT, EMPLOYER),
                                             self.POSTING, "engineering",
                                             title="AI Engineer", **kw)]

    def test_always_included_bullets_are_on_every_resume(self):
        ids = self.ids()
        self.assertIn("b_indep_build", ids)
        self.assertIn("b_indep_other", ids)

    def test_they_take_no_ranked_slot(self):
        ranked = [i for i in self.ids() if not i.startswith("b_indep")]
        self.assertEqual(len(ranked), 6)

    def test_employer_keeps_its_work_history_bullet(self):
        self.assertIn("b_sales", self.ids())

    def test_short_note_leaves_them_out(self):
        ids = self.ids(limit=3, keep_work_history=False, keep_project=False,
                       keep_always=False)
        self.assertFalse(any(i.startswith("b_indep") for i in ids))

    def test_always_included_employer_does_not_take_the_work_history_slot(self):
        later = {"id": "exp_gig", "company": "Various", "title": "Crew",
                 "family": "other", "start": "2023", "end": "2025",
                 "always_include": True,
                 "bullets": [{"id": "b_gig", "strength": 1,
                              "text": "Moved people where they needed to be.",
                              "tags": []}]}
        p = profile(INDEPENDENT, later, EMPLOYER)
        self.assertEqual(most_recent_job(p)["id"], "exp_sales")
        ids = [b.id for b in select_bullets(p, self.POSTING, "engineering",
                                            title="AI Engineer")]
        self.assertIn("b_sales", ids)
        self.assertIn("b_gig", ids)

    def test_flag_absent_changes_nothing(self):
        plain = {k: v for k, v in INDEPENDENT.items()
                 if k not in ("always_include", "self_employed")}
        self.assertEqual(always_included(profile(plain, EMPLOYER)), set())


if __name__ == "__main__":
    unittest.main()
