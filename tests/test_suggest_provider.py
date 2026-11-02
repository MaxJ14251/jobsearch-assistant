"""Suggest's own provider, and marking what isn't backed up (2026-11-02).

Suggest may run on its own OpenAI-compatible provider (JSA_SUGGEST_BASE_URL,
JSA_SUGGEST_API_KEY, JSA_SUGGEST_MODELS); unset, every request is what it was.
With suggest.MARK_UNSUPPORTED on, a sentence the profile doesn't back up is
kept and marked, while a degree, a do-not-claim term or unfinished work called
done still drops the answer. Off, nothing changes. Fiction only.
"""

import os
import unittest
from unittest import mock

from jsa import answers, llm, suggest
from jsa.config import ROOT
from jsa.llm import Usage
from tests.test_answers import GOOD
from tests.test_listing import POSTING
from tests.test_ext import Ext
from tests.test_llm_provider import SETTINGS, Provider

ROUTE = {"JSA_SUGGEST_BASE_URL": "https://example.test/v1/openai/",
         "JSA_SUGGEST_API_KEY": "test-suggest-key",
         "JSA_SUGGEST_MODELS": "writer-one, writer-two"}
ALL = SETTINGS + tuple(ROUTE) + ("JSA_SUGGEST_REASONING_EFFORT",)


class Routes(Provider):
    def setUp(self):
        super().setUp()
        saved = {k: os.environ.pop(k, None) for k in ALL if k not in SETTINGS}

        def restore():
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
        self.addCleanup(restore)
        os.environ["NVIDIA_API_KEY"] = "nvapi-test"

    def test_unset_suggest_goes_where_everything_goes(self):
        with self.post():
            llm.complete("Hi", purpose="suggest")
        self.assertEqual(self.sent[0]["url"], "https://integrate.api.nvidia.com/v1/chat/completions")
        self.assertEqual(self.sent[0]["json"]["max_tokens"], 1500)

    def test_set_only_suggest_moves(self):
        os.environ.update(ROUTE)
        with self.post():
            llm.complete("Hi", purpose="suggest", max_tokens=900)
            llm.complete("Hi", purpose="letter")
        routed, other = self.sent
        self.assertEqual(routed["url"], "https://example.test/v1/openai/chat/completions")
        self.assertEqual(routed["headers"]["Authorization"], "Bearer test-suggest-key")
        self.assertEqual(routed["json"]["model"], "writer-one")
        self.assertNotIn("chat_template_kwargs", routed["json"])
        self.assertEqual(routed["json"]["max_tokens"], llm.ROUTED_MIN_TOKENS)
        self.assertEqual(other["url"], "https://integrate.api.nvidia.com/v1/chat/completions")
        self.assertEqual(other["headers"]["Authorization"], "Bearer nvapi-test")

    def test_the_effort_is_sent_only_when_set(self):
        os.environ.update(ROUTE)
        with self.post():
            llm.complete("Hi", purpose="suggest")
        self.assertNotIn("reasoning_effort", self.sent[0]["json"])
        os.environ["JSA_SUGGEST_REASONING_EFFORT"] = "low"
        with self.post():
            llm.complete("Hi", purpose="suggest")
        self.assertEqual(self.sent[1]["json"]["reasoning_effort"], "low")

    def test_a_key_without_the_rest_says_what_to_set(self):
        os.environ["JSA_SUGGEST_API_KEY"] = "test-suggest-key"
        with self.post(), self.assertRaises(llm.LLMError) as caught:
            llm.complete("Hi", purpose="suggest")
        self.assertIn("JSA_SUGGEST_BASE_URL", str(caught.exception))
        self.assertNotIn("test-suggest-key", str(caught.exception))
        self.assertEqual(self.sent, [])


SOFT = GOOD + " I wrote observability dashboards for every utility."
HARD = GOOD + " I have a PhD in computer science."


class Marking(Ext):
    def setUp(self):
        super().setUp()
        self.con.execute("UPDATE jobs SET description = ? WHERE id = 1", (POSTING,))
        self.con.commit()
        on = mock.patch("jsa.suggest.MARK_UNSUPPORTED", True)
        on.start()
        self.addCleanup(on.stop)

    def press(self, replies, question="Anything else we should know?"):
        seen = []

        def fake(prompt, **k):
            seen.append(prompt)
            return replies[min(len(seen) - 1, len(replies) - 1)], Usage(model="t")

        job = answers.job_row(self.con, 1)
        with mock.patch("jsa.suggest.llm.complete_json", side_effect=fake):
            found, _ = suggest.options(self.profile, job, answers.evidence(self.con, job,
                                                                           self.profile),
                                       question)
        return found, seen

    def test_an_unsupported_sentence_is_kept_and_marked_with_no_retry(self):
        found, seen = self.press([{"role": SOFT, "project": None}, {"project": None}])
        role = found[0]
        self.assertEqual(role["text"], SOFT)
        self.assertEqual([m["text"] for m in role["marks"]],
                         ["I wrote observability dashboards for every utility."])
        self.assertIn("observability", role["marks"][0]["words"])
        self.assertIn("Check or remove", role["note"])
        self.assertNotIn("role", seen[1].split("Rewrite only these keys:")[-1])

    def test_a_degree_claim_still_drops_the_answer(self):
        found, _ = self.press([{"role": HARD, "project": HARD}] * 2)
        for option in found:
            self.assertNotIn("PhD", option["text"])

    def test_the_prompt_asks_for_plain_words_and_still_no_new_facts(self):
        _, seen = self.press([{"role": GOOD, "project": GOOD.replace("this", "the")}])
        self.assertIn("in your own words", seen[0])
        self.assertIn("add no fact, tool, number, outcome or quality", seen[0])

    def test_off_nothing_is_marked(self):
        with mock.patch("jsa.suggest.MARK_UNSUPPORTED", False):
            found, _ = self.press([{"role": SOFT, "project": None}] * 2)
        self.assertFalse([o for o in found if o.get("marks")])
        self.assertNotIn(SOFT, [o["text"] for o in found])

    def test_the_panel_note_says_so(self):
        self.assertIn("marked, not removed", suggest.panel_note())
        with mock.patch("jsa.suggest.MARK_UNSUPPORTED", False):
            self.assertNotIn("marked, not removed", suggest.panel_note())


class TestThePanelAndTheScanner(unittest.TestCase):
    def test_marks_are_text_nodes_only(self):
        source = (ROOT / "extension" / "content.js").read_text(encoding="utf-8")
        block = source[source.index("function answerText"):source.index("The one way a suggestion")]
        self.assertIn("createTextNode", block)
        self.assertNotIn("innerHTML", block)

    def test_the_key_is_caught_anywhere_but_dotenv(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        import scan_secrets
        names = [name for name, pattern in scan_secrets.KEY_PATTERNS
                 if pattern.search("JSA_SUGGEST_API_KEY" + "=abc123")]
        self.assertIn("Suggest provider key in a settings line", names)
        google = "AIza" + "B" * 35
        self.assertTrue([n for n, p in scan_secrets.KEY_PATTERNS if p.search(google)])
        self.assertFalse([n for n, p in scan_secrets.KEY_PATTERNS
                          if p.search("# JSA_SUGGEST_API_KEY" + "=")])


if __name__ == "__main__":
    unittest.main()
