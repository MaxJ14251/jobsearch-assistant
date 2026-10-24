"""Any OpenAI-compatible provider, with NVIDIA unchanged by default (G3).

Settings: JSA_LLM_BASE_URL, JSA_LLM_API_KEY (else NVIDIA_API_KEY),
JSA_LLM_MODELS, JSA_LLM_ENRICH_MODELS, JSA_LLM_THINKING_SWITCH. With none of
them set, every request is exactly what it was before (pinned below, captured
from the code before the change).
"""

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

from jsa import llm

SETTINGS = ("JSA_LLM_BASE_URL", "JSA_LLM_API_KEY", "JSA_LLM_MODELS",
            "JSA_LLM_ENRICH_MODELS", "JSA_LLM_THINKING_SWITCH", "NVIDIA_API_KEY")

# Captured 2026-10-24 from the code before this change: the default call, then
# json_mode + thinking + an explicit chain.
BEFORE = [
    {"headers": {"Authorization": "Bearer nvapi-test", "Content-Type": "application/json"},
     "json": {"chat_template_kwargs": {"enable_thinking": False}, "max_tokens": 1500,
              "messages": [{"content": "Sys", "role": "system"},
                           {"content": "Hello", "role": "user"}],
              "model": "nvidia/nemotron-3-ultra-550b-a55b", "temperature": 0.2},
     "url": "https://integrate.api.nvidia.com/v1/chat/completions"},
    {"headers": {"Authorization": "Bearer nvapi-test", "Content-Type": "application/json"},
     "json": {"chat_template_kwargs": {"enable_thinking": True}, "max_tokens": 1500,
              "messages": [{"content": "Hi", "role": "user"}], "model": "a/b",
              "response_format": {"type": "json_object"}, "temperature": 0.2},
     "url": "https://integrate.api.nvidia.com/v1/chat/completions"},
]


class Ok:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"content": "{}"}}], "usage": {}}


class Missing:
    status_code = 404
    text = "Not found for account"


class Provider(unittest.TestCase):
    def setUp(self):
        saved = {k: os.environ.pop(k, None) for k in SETTINGS}

        def restore():
            for k in SETTINGS:
                os.environ.pop(k, None)
                if saved[k] is not None:
                    os.environ[k] = saved[k]
        self.addCleanup(restore)
        self.sent = []

    def post(self, replies=None):
        replies = list(replies or [])

        def fake(client, url, headers=None, json=None):
            self.sent.append({"url": url, "headers": headers, "json": json})
            return replies.pop(0) if replies else Ok()
        return mock.patch("httpx.Client.post", fake)


class TestNothingChangesByDefault(Provider):
    def test_requests_are_byte_identical_to_before(self):
        os.environ["NVIDIA_API_KEY"] = "nvapi-test"
        with self.post():
            llm.complete("Hello", system="Sys", purpose="test")
            llm.complete("Hi", json_mode=True, thinking=True, models=["a/b"], purpose="test")
        self.assertEqual(json.dumps(self.sent, sort_keys=True),
                         json.dumps(BEFORE, sort_keys=True))

    def test_the_default_chains(self):
        from jsa.enrich import ENRICH_MODELS
        self.assertEqual(llm.drafting_models(), llm.DEFAULT_MODELS)
        self.assertEqual(llm.enrich_models(), ENRICH_MODELS)
        self.assertTrue(llm.is_nvidia())


class TestAnotherProvider(Provider):
    def configure(self):
        os.environ.update({"JSA_LLM_BASE_URL": "https://llm.example.test/v1/",
                           "JSA_LLM_API_KEY": "sk-example-123",
                           "JSA_LLM_MODELS": "first-model, second-model",
                           "JSA_LLM_ENRICH_MODELS": "small-model"})

    def test_its_endpoint_key_and_models_are_used_in_order(self):
        self.configure()
        with self.post([Missing()]):
            result = llm.complete("Hello", purpose="test")
        self.assertEqual([s["json"]["model"] for s in self.sent], ["first-model", "second-model"])
        self.assertEqual(self.sent[0]["url"], "https://llm.example.test/v1/chat/completions")
        self.assertEqual(self.sent[0]["headers"]["Authorization"], "Bearer sk-example-123")
        self.assertEqual(result.usage.model, "second-model")
        self.assertEqual(llm.enrich_models(), ["small-model"])

    def test_nvidias_thinking_field_is_not_sent_elsewhere_unless_asked(self):
        self.configure()
        with self.post():
            llm.complete("Hello", purpose="test")
        self.assertNotIn("chat_template_kwargs", self.sent[-1]["json"])
        os.environ["JSA_LLM_THINKING_SWITCH"] = "1"
        with self.post():
            llm.complete("Hello", purpose="test")
        self.assertEqual(self.sent[-1]["json"]["chat_template_kwargs"], {"enable_thinking": False})

    def test_the_generic_key_wins_and_the_old_name_still_works(self):
        os.environ["NVIDIA_API_KEY"] = "nvapi-old"
        self.assertEqual(llm.api_key(), "nvapi-old")
        self.assertEqual(llm.key_env(), "NVIDIA_API_KEY")
        os.environ["JSA_LLM_API_KEY"] = "sk-new"
        self.assertEqual(llm.api_key(), "sk-new")
        self.assertEqual(llm.key_env(), "JSA_LLM_API_KEY")

    def test_no_key_names_both_settings(self):
        with self.assertRaises(llm.LLMError) as caught:
            llm.api_key()
        self.assertIn("NVIDIA_API_KEY", str(caught.exception))
        self.assertIn("JSA_LLM_API_KEY", str(caught.exception))

    def test_the_key_is_never_printed_or_recorded(self):
        self.configure()
        rows = []
        with self.post(), mock.patch.object(llm, "recorder", rows.append):
            llm.complete("Hello", purpose="test")
        self.assertNotIn("sk-example-123", json.dumps(rows))
        from jsa import cli
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["env"])
        text = out.getvalue()
        self.assertNotIn("sk-example-123", text)
        self.assertIn("llm.example.test", text)
        self.assertIn("first-model, second-model", text)


if __name__ == "__main__":
    unittest.main()
