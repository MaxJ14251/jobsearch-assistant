"""Tests for the LLM client. No network — the parsing logic is what breaks."""

import json
import os
import unittest

from jsa import llm


class TestReasoningStripping(unittest.TestCase):
    """Some NIM models emit chain-of-thought ahead of the answer."""

    def test_strips_think_tags(self):
        out = llm._strip_reasoning("<think>hmm, let me consider</think>The answer.")
        self.assertEqual(out, "The answer.")

    def test_strips_thinking_process_preamble(self):
        # Exactly what nemotron-3.5-lightning returned when asked to "Say OK".
        raw = ("Here's a thinking process:  1. **Analyze User Input** the user "
               "wants a greeting.\n\nOK.")
        self.assertEqual(llm._strip_reasoning(raw), "OK.")

    def test_leaves_normal_output_alone(self):
        text = "Start every bullet with a strong action verb."
        self.assertEqual(llm._strip_reasoning(text), text)

    def test_does_not_eat_legitimate_content(self):
        text = "Let me think about pricing is a phrase you should avoid in a cover letter."
        self.assertIn("cover letter", llm._strip_reasoning(text))


class TestApiKey(unittest.TestCase):
    def test_missing_key_message_is_actionable(self):
        saved = os.environ.pop(llm.API_KEY_ENV, None)
        try:
            with self.assertRaises(llm.LLMError) as ctx:
                llm.api_key()
            msg = str(ctx.exception)
            self.assertIn(llm.API_KEY_ENV, msg)
            self.assertIn("build.nvidia.com", msg)
        finally:
            if saved is not None:
                os.environ[llm.API_KEY_ENV] = saved

    def test_key_is_never_hardcoded(self):
        """A key must never be committed — guard against pasting one in."""
        import pathlib
        src = pathlib.Path(llm.__file__).read_text(encoding="utf-8")
        self.assertNotIn("nvapi-", src.replace("nvapi-...", ""))


class TestJsonExtraction(unittest.TestCase):
    """Smaller models wrap JSON in prose or fences even when told not to."""

    def _extract(self, text):
        from jsa.llm import extract_json
        import json as _json
        try:
            return extract_json(text)
        except _json.JSONDecodeError:
            return None

    def test_plain_json(self):
        self.assertEqual(self._extract('{"a": 1}'), {"a": 1})

    def test_json_in_fences(self):
        self.assertEqual(
            self._extract('```json\n{"a": 1}\n```'), {"a": 1}
        )

    def test_json_with_preamble(self):
        self.assertEqual(
            self._extract('Sure! Here is the JSON:\n{"a": 1}\nHope that helps.'),
            {"a": 1},
        )

    def test_array_response_when_a_list_is_wanted(self):
        from jsa.llm import extract_json
        self.assertEqual(
            extract_json('Here you go: [1, 2, 3]', expect=list), [1, 2, 3]
        )

    def test_array_response_rejected_when_an_object_is_wanted(self):
        # Changed deliberately on 2026-09-14: accepting a stray array under the
        # default let an echoed schema fragment through as real data.
        self.assertIsNone(self._extract('Here you go: [1, 2, 3]'))


class TestFallbackChain(unittest.TestCase):
    def test_chain_is_ordered_and_nonempty(self):
        self.assertTrue(llm.DEFAULT_MODELS)
        self.assertEqual(len(llm.DEFAULT_MODELS), len(set(llm.DEFAULT_MODELS)))

    def test_retry_statuses_cover_overload(self):
        # A 503 was observed mid-probe on 2026-09-14; it must be retried.
        self.assertIn(503, llm.RETRY_STATUS)
        self.assertIn(429, llm.RETRY_STATUS)
        # A 404 means "not available for this account" — retrying is pointless.
        self.assertNotIn(404, llm.RETRY_STATUS)




class TestDotenv(unittest.TestCase):
    """.env loading: the file is a convenience, the shell is the authority."""

    def setUp(self):
        import tempfile, pathlib
        self.tmp = pathlib.Path(tempfile.mkdtemp()) / ".env"

    def test_loads_values(self):
        from jsa.config import load_dotenv
        self.tmp.write_text("JSA_TEST_A=hello\n", encoding="utf-8")
        os.environ.pop("JSA_TEST_A", None)
        try:
            self.assertIn("JSA_TEST_A", load_dotenv(self.tmp))
            self.assertEqual(os.environ["JSA_TEST_A"], "hello")
        finally:
            os.environ.pop("JSA_TEST_A", None)

    def test_real_environment_wins(self):
        from jsa.config import load_dotenv
        self.tmp.write_text("JSA_TEST_B=from_file\n", encoding="utf-8")
        os.environ["JSA_TEST_B"] = "from_shell"
        try:
            load_dotenv(self.tmp)
            self.assertEqual(os.environ["JSA_TEST_B"], "from_shell")
        finally:
            os.environ.pop("JSA_TEST_B", None)

    def test_placeholder_is_not_loaded(self):
        from jsa.config import load_dotenv
        self.tmp.write_text("JSA_TEST_C=<paste-your-key-here>\n", encoding="utf-8")
        os.environ.pop("JSA_TEST_C", None)
        load_dotenv(self.tmp)
        self.assertNotIn("JSA_TEST_C", os.environ)

    def test_comments_blanks_and_quotes(self):
        from jsa.config import load_dotenv
        self.tmp.write_text(
            '# a comment\n\nJSA_TEST_D="quoted value"\n', encoding="utf-8"
        )
        os.environ.pop("JSA_TEST_D", None)
        try:
            load_dotenv(self.tmp)
            self.assertEqual(os.environ["JSA_TEST_D"], "quoted value")
        finally:
            os.environ.pop("JSA_TEST_D", None)

    def test_missing_file_is_not_an_error(self):
        from jsa.config import load_dotenv
        import pathlib
        self.assertEqual(load_dotenv(pathlib.Path("nope-does-not-exist.env")), [])

    def test_env_file_is_gitignored(self):
        import pathlib
        from jsa.config import ROOT
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", ignored)

    def test_example_file_holds_no_real_key(self):
        from jsa.config import ROOT
        example = (ROOT / "jsa" / "resources" / ".env.example").read_text(encoding="utf-8")
        self.assertNotIn("nvapi-i", example)
        self.assertIn("<paste", example)



class TestMalformedJson(unittest.TestCase):
    """Real output observed from NIM models on 2026-09-14."""

    def test_unbalanced_output_is_rejected_not_silently_wrong(self):
        import json as _json
        from jsa.llm import extract_json
        # nemotron-3-ultra actually returned this, with json_object requested.
        bad = '{"ok":{ "provider": "nim" }'
        with self.assertRaises(_json.JSONDecodeError):
            extract_json(bad)

    def test_fenced_json_is_recovered(self):
        from jsa.llm import extract_json
        self.assertEqual(
            extract_json('```json\n{"a": 1}\n```'), {"a": 1}
        )

    def test_prose_wrapped_json_is_recovered(self):
        from jsa.llm import extract_json
        self.assertEqual(
            extract_json('Sure thing!\n{"a": [1, 2]}\nLet me know.'), {"a": [1, 2]}
        )

    def test_json_system_prompt_is_explicit(self):
        from jsa.llm import JSON_SYSTEM
        self.assertIn("JSON", JSON_SYSTEM)
        self.assertIn("no markdown", JSON_SYSTEM.lower())



class TestReasoningModelExtraction(unittest.TestCase):
    """Regression tests from nemotron-3.5-lightning, 2026-09-14.

    It is a reasoning model: it echoes the requested schema while thinking.
    A naive first-brace scan returns the echoed template as if it were data —
    well-formed, confident, and wrong.
    """

    # Real truncated output, shortened.
    ECHO = (
        '1.  **Analyze User Request:**\n   - Output exactly this JSON shape:\n'
        '     ```json\n     {\n       "degree_required": true|false|null,\n'
        '       "tech_stack": ["up to 5 primary technologies"]\n     }\n'
        '     ```\n   - Input: Job posting titled "Inside Sales"\n\n'
        '2.  **Analyze the Job Posting:**\n   - I need to map the content'
    )

    def test_template_echo_does_not_parse_as_data(self):
        import json as _json
        from jsa.llm import extract_json
        # The echoed schema is not valid JSON (true|false|null), so this must
        # raise rather than returning the stray array inside it.
        with self.assertRaises(_json.JSONDecodeError):
            extract_json(self.ECHO)

    def test_bare_array_rejected_when_object_expected(self):
        import json as _json
        from jsa.llm import extract_json
        with self.assertRaises(_json.JSONDecodeError):
            extract_json('["up to 5 primary technologies"]')
        # ...but allowed when the caller actually wants a list.
        self.assertEqual(extract_json('["a"]', expect=list), ["a"])

    def test_last_object_wins_over_echoed_first(self):
        from jsa.llm import extract_json
        text = (
            'Thinking: the shape is {"seniority": "<string>"} ...\n'
            'Final answer:\n{"seniority": "entry", "years_required": 1}'
        )
        got = extract_json(text)
        self.assertEqual(got["seniority"], "entry")
        self.assertEqual(got["years_required"], 1)

    def test_braces_inside_strings_do_not_break_scanning(self):
        from jsa.llm import extract_json
        text = '{"reason": "uses {curly} braces", "ok": true}'
        self.assertEqual(extract_json(text)["ok"], True)

    def test_thinking_defaults_off(self):
        import inspect
        from jsa.llm import complete
        self.assertIs(
            inspect.signature(complete).parameters["thinking"].default, False
        )


if __name__ == "__main__":
    unittest.main()
