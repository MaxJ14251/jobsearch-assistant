"""With the subject-aware switch off, every drafting prompt is byte-identical
to what it was before plan 39 (the suggestions' and the written answers'
first prompt, system text and retry, and the measurement tool's old
three-angle prompt). Pinned as SHA-256 digests of the texts, captured on the
Riverton fixture before any edit."""

import hashlib
import sys
import unittest
from unittest import mock

from jsa import answers, suggest
from jsa.config import ROOT
from jsa.llm import Usage
from tests.test_answers import GOOD
from tests.test_ext import Ext

QUESTION = "What excites you about Riverton Grid?"
REFUSED = GOOD + " I led a team of forty engineers at Google."

# Captured 2026-10-11, before plan 39 changed anything.
PINNED = {
    "suggest_prompt": "05e80eb274d06eb271b36e7262f8849c14604a4a3f91977515bd48018c2f8d0a",
    "suggest_system": "febfd4c90a794ac1410b2ac4060155cf7d4099cd45f4d2e7a00db7d915254391",
    "suggest_retry": "405cdd5e9879aeec7103cc66341ec959e407f41ee00dfe5747f344280ae6029f",
    "suggest_prompt_capped": "6e702383b1c01b2d0f93f7bdef0906a64c531c4839b6e80bb58cd156fbd98428",
    "answers_prompt": "bf8ef189845dbd6727250b64b608c8ba8d029632db50bed087d6541a018ce768",
    "answers_system": "80428051fe118a93262657c249b104ecb1c038ba2240eca0f7ac7add0d4e45fa",
    "answers_retry": "e88645014e605a9cc6582ec84db8c50aae71fecc085d40ff0198bdf9b87b3d6b",
    "measure_three": "6c72196811b5841d6dabdea8f90bd674d8894a3c61acb5bb12aa48881e58bf5a",
    # Captured 2026-10-25, before G7 changed letter.py (letter.SUBJECT_AWARE off).
    "letter_prompt": "fe52424a87f61fca2e68f5a6bfb3d0d6253c4edd87dcd5f81369f762e3e9e309",
    "letter_system": "a02934017633301f36c6f463ad428ccbc2e9be314f25af332183fb667e2041cf",
    "letter_retry": "5ef25927fee068b782a389a917a9995ec3ec701da6d94a4056a1645029f3971e",
}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def capture(case) -> dict:
    """Every text the model is sent, on fixed fixture data, switch off."""
    from tests.test_answers import ready  # noqa: F401  (fixture profile lives there)
    seen: dict[str, str] = {}
    job = answers.job_row(case.con, 1)
    calls = []

    def fake(prompt, **k):
        calls.append((prompt, k.get("system") or ""))
        return {"role": REFUSED, "project": GOOD,
                "why_role": REFUSED, "relevant_project": GOOD}, Usage(model="t")

    with mock.patch("jsa.answers.SUBJECT_AWARE", False), \
            mock.patch("jsa.suggest.llm.complete_json", side_effect=fake), \
            mock.patch("jsa.answers.llm.complete_json", side_effect=fake):
        bullets = answers.evidence(case.con, job, case.profile)
        suggest.options(case.profile, job, bullets, QUESTION)
        seen["suggest_prompt"], seen["suggest_system"] = calls[0]
        seen["suggest_retry"] = calls[1][0]
        calls.clear()
        suggest.options(case.profile, job, bullets, QUESTION, maxlength=300)
        seen["suggest_prompt_capped"] = calls[0][0]
        calls.clear()
        answers.write(case.con, 1, case.profile)
        seen["answers_prompt"], seen["answers_system"] = calls[0]
        seen["answers_retry"] = calls[1][0]
    # Cover letters (G7): their own switch, letter.SUBJECT_AWARE.
    from types import SimpleNamespace
    from jsa import letter
    from jsa.llm import Completion
    letter_calls = []

    def fake_letter(prompt, **k):
        letter_calls.append((prompt, k.get("system") or ""))
        return Completion(text="Dear Hiring Manager,\n" + REFUSED, usage=Usage(model="t"))

    draft = SimpleNamespace(summary=GOOD, bullets=bullets)
    with mock.patch("jsa.letter.llm.complete", side_effect=fake_letter), \
            mock.patch.object(letter, "SUBJECT_AWARE", False, create=True):
        letter.write(job, case.profile, draft)
    seen["letter_prompt"], seen["letter_system"] = letter_calls[0]
    seen["letter_retry"] = letter_calls[1][0]
    sys.path.insert(0, str(ROOT / "tools"))
    import measure_verifier
    job = {k: job.get(k) for k in ("id", "title", "company", "location", "track",
                                   "description")}
    seen["measure_three"] = measure_verifier.prompt_for(case.profile, job, bullets,
                                                        QUESTION)[0]
    return seen


class TestPromptsUnchanged(Ext):
    def setUp(self):
        super().setUp()
        self.con.execute("UPDATE jobs SET description = ? WHERE id = 1",
                         ("Riverton Grid builds microgrid software for utilities. " * 30,))
        self.con.commit()

    def test_every_prompt_is_byte_identical_with_the_switch_off(self):
        seen = capture(self)
        for name, want in PINNED.items():
            with self.subTest(text=name):
                self.assertEqual(digest(seen[name]), want, name)


if __name__ == "__main__":
    unittest.main()
