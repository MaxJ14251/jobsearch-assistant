"""The model-call ledger (plan 26): counts per attempt, never text.

HTTP is faked with httpx.MockTransport; every row goes to a temporary
tracker. Nothing here reaches a model or the real tracker.
"""

import ast
import io
import os
import shutil
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import httpx

from jsa import cli, db, ledger, llm

ROOT = Path(__file__).resolve().parents[1]
REAL_CONNECT = db.connect
REAL_CLIENT = httpx.Client


def reply(text="ok", usage=None):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}],
                                     **({"usage": usage} if usage else {})})


class Ledger(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = self.dir / "t.db"
        db.init_db(self.path)
        previous = ledger.install(self.path)
        self.addCleanup(setattr, llm, "recorder", previous)
        for patch in (mock.patch("jsa.llm.api_key", return_value="test-key"),
                      mock.patch("jsa.llm.time.sleep")):
            patch.start()
            self.addCleanup(patch.stop)

    def fake(self, *responses):
        queue = list(responses)

        def handler(request):
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        patch = mock.patch("jsa.llm.httpx.Client",
                           side_effect=lambda **kw: REAL_CLIENT(
                               transport=httpx.MockTransport(handler), **kw))
        patch.start()
        self.addCleanup(patch.stop)

    def rows(self):
        con = REAL_CONNECT(self.path)
        try:
            return [dict(r) for r in con.execute("SELECT * FROM model_calls ORDER BY id")]
        finally:
            con.close()


class TestEveryAttemptIsARow(Ledger):
    def test_two_failures_then_a_fallback_success(self):
        self.fake(httpx.Response(503, text="busy"), httpx.Response(503, text="busy"),
                  reply(usage={"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}))
        llm.complete("p", models=["m/one", "m/two"], purpose="tailor")
        rows = self.rows()
        self.assertEqual([(r["model"], r["attempt"], r["ok"], r["fell_back"], r["error_kind"])
                          for r in rows],
                         [("m/one", 1, 0, 0, "http 503"), ("m/one", 2, 0, 0, "http 503"),
                          ("m/two", 3, 1, 1, None)])
        self.assertEqual({r["purpose"] for r in rows}, {"tailor"})
        self.assertEqual((rows[2]["prompt_tokens"], rows[2]["completion_tokens"]), (12, 3))

    def test_a_transport_error_is_a_row_too(self):
        self.fake(httpx.ConnectError("down"), reply())
        llm.complete("p", models=["m/one", "m/two"], purpose="prep")
        self.assertEqual([r["error_kind"] for r in self.rows()], ["ConnectError", None])

    def test_complete_json_retries_are_all_recorded(self):
        self.fake(reply("not json"), reply('{"a": 1}'))
        data, _ = llm.complete_json("p", models=["m/one"], purpose="answers", attempts=2)
        self.assertEqual(data, {"a": 1})
        self.assertEqual([(r["ok"], r["purpose"]) for r in self.rows()],
                         [(1, "answers"), (1, "answers")])

    def test_a_failing_recorder_never_fails_the_call(self):
        self.fake(reply("fine"))
        with mock.patch.object(llm, "recorder", side_effect=OSError("disk full")):
            self.assertEqual(llm.complete("p", models=["m/one"], purpose="x").text, "fine")

    def test_eight_threads_at_once(self):
        def call(i):
            ledger.record({"purpose": "enrich", "model": f"m/{i}", "attempt": 1, "ok": True},
                          self.path)
        threads = [threading.Thread(target=call, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.rows()), 8)

    def test_no_tracker_no_row_and_no_error(self):
        ledger.record({"purpose": "x", "model": "m", "attempt": 1, "ok": True},
                      self.dir / "missing.db")
        self.assertFalse((self.dir / "missing.db").exists())


class TestNoTextIsKept(Ledger):
    def test_the_columns_are_counts_only(self):
        con = REAL_CONNECT(self.path)
        self.addCleanup(con.close)
        cols = [r["name"] for r in con.execute("PRAGMA table_info(model_calls)")]
        self.assertEqual(cols, ["id", "at", "purpose", "model", "prompt_tokens",
                                "completion_tokens", "total_tokens", "latency_s", "attempt",
                                "fell_back", "ok", "error_kind"])

    def test_the_prompt_and_reply_never_reach_the_ledger(self):
        self.fake(reply("A SECRET REPLY"))
        llm.complete("A SECRET PROMPT", models=["m/one"], purpose="x")
        blob = repr(self.rows())
        self.assertNotIn("SECRET", blob)


class TestEveryCallerNamesItsPurpose(unittest.TestCase):
    def test_purpose_on_every_model_call(self):
        missing = []
        for path in sorted((ROOT / "jsa").rglob("*.py")):
            if path.name == "llm.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                if name in ("complete", "complete_json") and not any(
                        k.arg == "purpose" for k in node.keywords):
                    missing.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(missing, [])


class TestTheCommand(Ledger):
    def add(self, **row):
        ledger.record({"purpose": "tailor", "model": "m/one", "attempt": 1, "ok": True,
                       **row}, self.path)

    def run_cli(self, *argv, env=None):
        out = io.StringIO()
        with mock.patch("jsa.cli.DB_PATH", self.path), \
             mock.patch("jsa.db.connect", side_effect=lambda *a, **k: REAL_CONNECT(self.path)), \
             mock.patch.dict(os.environ, env or {}, clear=False), redirect_stdout(out):
            code = cli.main(["usage", *argv])
        return code, out.getvalue()

    def test_totals_by_purpose(self):
        self.add(prompt_tokens=1000, completion_tokens=200, total_tokens=1200)
        self.add(purpose="prep", ok=False, error_kind="http 503")
        code, out = self.run_cli("--days", "1")
        self.assertEqual(code, 0)
        self.assertIn("tailor", out)
        self.assertIn("prep", out)
        self.assertIn("1,000", out)
        self.assertIn("estimated cost", out)            # how to set prices
        self.assertIn("no prompt or reply text", out)

    def test_with_prices_an_estimate(self):
        self.add(prompt_tokens=1_000_000, completion_tokens=500_000, total_tokens=1_500_000)
        env = {ledger.PRICE_IN_ENV: "0.50", ledger.PRICE_OUT_ENV: "2"}
        with mock.patch.dict(os.environ, env):
            _, out = self.run_cli("--days", "1")
        self.assertIn("$    1.50", out)
        self.assertIn("estimate", out)

    def test_a_provider_without_token_counts_is_named(self):
        self.add()
        _, out = self.run_cli()
        self.assertIn("doesn't report token counts", out)

    def test_cli_main_puts_the_recorder_back(self):
        before = llm.recorder
        self.run_cli()
        self.assertIs(llm.recorder, before)


class TestWhereItShows(Ledger):
    def test_turbo_status_counts_today(self):
        from jsa import turbo
        ledger.record({"purpose": "tailor", "model": "m", "attempt": 1, "ok": True},
                      self.path)
        con = REAL_CONNECT(self.path)
        self.addCleanup(con.close)
        self.assertEqual(turbo.status(con)["model_calls_today"], 1)

    def test_daily_reports_yesterday(self):
        from jsa import daily
        yesterday = datetime.combine(date.fromordinal(date.today().toordinal() - 1),
                                     datetime.min.time().replace(hour=12)).astimezone(timezone.utc)
        ledger.record({"purpose": "enrich", "model": "m", "attempt": 1, "ok": True,
                       "prompt_tokens": 40, "at": ledger.stamp(yesterday)}, self.path)
        con = REAL_CONNECT(self.path)
        self.addCleanup(con.close)
        summary = daily.summarize(con, "2000-01-01T00:00:00Z")
        self.assertEqual(summary["model_calls"]["calls"], 1)

    def test_doctor_warns_past_the_threshold(self):
        from jsa import doctor
        con = REAL_CONNECT(self.path)
        self.addCleanup(con.close)
        ledger.record({"purpose": "x", "model": "m", "attempt": 1, "ok": True}, self.path)
        with mock.patch.object(ledger, "USAGE_WARN_CALLS", 0):
            found = doctor.run(None, con)
        advisory = [f.what for f in found.advisory]
        self.assertTrue(any("model calls today" in w for w in advisory), advisory)
        with mock.patch.object(ledger, "USAGE_WARN_CALLS", 5):
            found = doctor.run(None, con)
        self.assertFalse(any("model calls today" in f.what for f in found.findings))


if __name__ == "__main__":
    unittest.main()
