"""The browser extension never submits, and reaches only what it must (plan 30).

Read from the files, the way tests/test_outreach.py's TestCannotSend reads
outreach: a submission path, a synthetic click, a stray network call or a
broad permission fails here before anyone loads the extension.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extension"
BOARD_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co",
               "jobs.ashbyhq.com"}


def code(path: Path) -> str:
    """The file without its comments, so a comment saying what is never done
    doesn't count as doing it."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)(^|[^:\\])//.*$", r"\1", text)


def shipped_scripts() -> list[Path]:
    return sorted(p for p in EXT.glob("*.js"))


class TestNeverSubmits(unittest.TestCase):
    FORBIDDEN = {
        r"\bsubmit\s*\(": "calls submit()",
        r"requestSubmit": "calls requestSubmit()",
        r"\.click\s*\(": "clicks something",
        r"new\s+(Mouse|Pointer|Keyboard|Submit)Event": "fakes a click, key or submit",
        r"""new\s+Event\s*\(\s*["']submit""": "fires a submit event",
        r"contentWindow|contentDocument": "reaches into another frame (a CAPTCHA's)",
    }

    def test_no_script_can_submit_click_or_press_a_key(self):
        for path in shipped_scripts() + sorted((EXT / "test" / "forms").glob("*.js")):
            source = code(path)
            for pattern, what in self.FORBIDDEN.items():
                with self.subTest(file=path.name, rule=what):
                    self.assertIsNone(re.search(pattern, source), f"{path.name} {what}")

    def test_the_guard_bites(self):
        sample = 'form.requestSubmit(); button.click(); x.submit();'
        hits = [w for p, w in self.FORBIDDEN.items() if re.search(p, sample)]
        self.assertEqual(len(hits), 3)

    def test_no_markup_from_data(self):
        for path in shipped_scripts():
            source = code(path)
            for pattern in (r"innerHTML", r"outerHTML", r"insertAdjacentHTML",
                            r"document\.write", r"\beval\s*\(", r"new\s+Function"):
                with self.subTest(file=path.name, pattern=pattern):
                    self.assertIsNone(re.search(pattern, source))


class TestReachesOnlyItsOwnDashboard(unittest.TestCase):
    def test_only_the_background_worker_makes_requests(self):
        for path in shipped_scripts():
            source = code(path)
            for pattern in (r"\bfetch\s*\(", r"XMLHttpRequest", r"WebSocket",
                            r"sendBeacon", r"EventSource"):
                if path.name == "background.js" and pattern == r"\bfetch\s*\(":
                    continue
                with self.subTest(file=path.name, pattern=pattern):
                    self.assertIsNone(re.search(pattern, source))

    def test_every_address_is_the_dashboard_or_a_board(self):
        allowed = BOARD_HOSTS | {"127.0.0.1"}
        for path in shipped_scripts() + [EXT / "options.html"]:
            for host in re.findall(r"https?://([^/:\"'\s`]+)", code(path)):
                with self.subTest(file=path.name, host=host):
                    self.assertIn(host, allowed)

    def test_the_background_worker_only_calls_ext_routes(self):
        source = code(EXT / "background.js")
        paths = re.findall(r"""(?:json|call)\(\s*["'](/[^"'?]*)""", source)
        self.assertTrue(paths)
        for path in paths:
            self.assertTrue(path.startswith("/ext/"), path)

    def test_it_asks_for_the_frame_it_was_sent_from(self):
        # The page being filled is the one Chrome says sent the message, not
        # whatever address a message claims.
        source = code(EXT / "background.js")
        self.assertIn("sender.url", source)
        self.assertIn("sender.id !== chrome.runtime.id", source)


def body_of(source: str, name: str) -> str:
    """One function's text: from its header to the next function header."""
    match = re.search(r"(?:async\s+)?function\s+" + name + r"\s*\(|\b" + name +
                      r"\s*\([^)]*\)\s*\{", source)
    assert match, name
    rest = source[match.end():]
    end = re.search(r"\n\s*(?:async\s+)?function\s+\w+\s*\(|\n  async \w+\(", rest)
    return rest[:end.start()] if end else rest


class TestNeverMovesOnByItself(unittest.TestCase):
    """The apply session (plan 31): every step to another job is a button."""

    NAV = ("session_submitted", "session_skip", "session_back", "session_end")
    BUTTONS = ("nextAfterSubmit", "skipNext", "backToCurrent", "endSession")

    def test_only_the_worker_changes_a_pages_address_and_only_in_one_place(self):
        for path in shipped_scripts():
            if path.name == "background.js":
                continue
            source = code(path)
            with self.subTest(file=path.name):
                self.assertNotIn("tabs.update", source)
                self.assertIsNone(re.search(
                    r"location\s*(\.href)?\s*=[^=]|location\.(assign|replace)", source))
        source = code(EXT / "background.js")
        self.assertEqual(source.count("tabs.update"), 1)
        navigate = body_of(source, "navigate")
        self.assertIn("tabs.update", navigate)
        self.assertIn("Session.isQueued(state, address)", navigate)
        self.assertIn("BOARD_HOSTS.includes(host)", navigate)

    def test_navigation_is_reached_only_from_the_session_messages(self):
        source = code(EXT / "background.js")
        names = set(re.findall(r"(?:async\s+)?function\s+(\w+)\s*\(", source)) | set(
            re.findall(r"\n  async (\w+)\(", source))
        callers = {n for n in names if n != "navigate"
                   and re.search(r"\b(navigate|moveOn)\s*\(", body_of(source, n))}
        self.assertEqual(callers, {"moveOn", "session_submitted", "session_skip",
                                   "session_back"})

    def test_no_clock_or_page_event_can_drive_it(self):
        for name in ("background.js", "session.js"):
            source = code(EXT / name)
            for pattern in (r"setTimeout", r"setInterval", r"chrome\.alarms",
                            r"chrome\.tabs\.on", r"webNavigation", r"addEventListener"):
                with self.subTest(file=name, pattern=pattern):
                    self.assertIsNone(re.search(pattern, source))

    def test_the_panel_sends_session_steps_only_from_its_buttons(self):
        source = code(EXT / "content.js")
        for kind in self.NAV:
            self.assertEqual(source.count('"' + kind + '"'), 1, kind)
            holders = [n for n in self.BUTTONS if '"' + kind + '"' in body_of(source, n)]
            self.assertEqual(len(holders), 1, kind)
        for name in self.BUTTONS:
            uses = re.findall(r"\b" + name + r"\b", source)
            wired = re.findall(r"button\([^;]*?,\s*" + name + r"\b", source)
            # Its definition and button(...) wiring only: never called directly.
            self.assertGreaterEqual(len(wired), 1, name)
            self.assertEqual(len(uses), 1 + len(wired), name)

    def test_recording_comes_before_moving_on(self):
        body = body_of(code(EXT / "background.js"), "session_submitted")
        self.assertLess(body.index("HANDLERS.applied"), body.index("moveOn"))
        self.assertIn("if (!recorded.ok) return", body)

    def test_skipping_never_records(self):
        body = body_of(code(EXT / "background.js"), "session_skip")
        self.assertNotIn("applied", body)


class TestSuggestionsWaitForYou(unittest.TestCase):
    """Plan 35: a suggestion is drafted only when you press Suggest, and goes
    into a field only when you press Use this."""

    def setUp(self):
        self.source = code(EXT / "content.js")

    def test_use_option_is_reached_only_from_its_button(self):
        uses = re.findall(r"\buseOption\b", self.source)
        wired = re.findall(r'button\("Use this", \(\) => useOption\(', self.source)
        self.assertEqual(len(wired), 1)
        self.assertEqual(len(uses), 1 + len(wired))       # its definition and the button

    def test_only_use_option_writes_a_suggestion_into_the_page(self):
        for name in ("card", "showOptions", "suggestFor", "suggestAll", "loadEarlier",
                     "questionsSection", "rememberAnswers"):
            with self.subTest(function=name):
                self.assertNotIn("native(", body_of(self.source, name))
        self.assertIn("native(field", body_of(self.source, "useOption"))

    def test_a_paid_suggestion_is_asked_for_only_from_a_button(self):
        self.assertEqual(self.source.count('type: "suggest"'), 1)
        self.assertIn('type: "suggest"', body_of(self.source, "suggestFor"))
        callers = re.findall(r"\bsuggestFor\b", self.source)
        # Its definition, the Suggest button, and Suggest for all's loop.
        self.assertEqual(len(callers), 3)
        self.assertIn('button("Suggest", () => suggestFor(q))', self.source)
        self.assertIn("for (const q of openQuestions) await suggestFor(q)",
                      body_of(self.source, "suggestAll"))
        self.assertEqual(len(re.findall(r"\bsuggestAll\b", self.source)), 2)
        self.assertIn("window.confirm", body_of(self.source, "suggestAll"))

    def test_answers_are_remembered_only_after_you_say_you_submitted(self):
        self.assertEqual(self.source.count('type: "remember"'), 1)
        self.assertIn('type: "remember"', body_of(self.source, "rememberAnswers"))
        holders = [n for n in ("submitted", "nextAfterSubmit", "recordFromPrompt")
                   if "rememberAnswers()" in body_of(self.source, n)]
        self.assertEqual(sorted(holders), ["nextAfterSubmit", "recordFromPrompt", "submitted"])
        self.assertEqual(len(re.findall(r"\brememberAnswers\b", self.source)), 4)
        for name in ("submitted", "nextAfterSubmit"):
            body = body_of(self.source, name)
            self.assertLess(body.index("window.confirm"), body.index("rememberAnswers()"))


class TestRecordingIsYourClick(unittest.TestCase):
    """Plan 36: the board's "application received" page brings up a
    question; only a button records the application."""

    def setUp(self):
        self.source = code(EXT / "content.js")

    def test_the_applied_message_is_sent_only_from_two_buttons(self):
        holders = [n for n in ("submitted", "recordFromPrompt")
                   if 'type: "applied"' in body_of(self.source, n)]
        self.assertEqual(holders, ["submitted", "recordFromPrompt"])
        self.assertEqual(self.source.count('type: "applied"'), 2)
        for name in ("submitted", "recordFromPrompt"):
            # Wired to a button, and never called: its only "name(" is its definition.
            wired = re.findall(r"button\([^;]*?,\s*" + name + r"\)", self.source)
            calls = re.findall(r"(?<![.\w])" + name + r"\s*\(", self.source)
            self.assertGreaterEqual(len(wired), 1, name)
            self.assertEqual(len(calls), 1, name)

    def test_watching_the_page_only_shows_the_question(self):
        for name in ("watchForConfirmation", "offerRecord", "pageFacts"):
            body = body_of(self.source, name)
            with self.subTest(function=name):
                self.assertNotIn('type: "applied"', body)
                self.assertNotIn("rememberAnswers", body)
                self.assertNotRegex(body, r"\b(recordFromPrompt|nextAfterSubmit)\s*\(")
                self.assertNotIn("session_submitted", body)
        # The observer's only act is to ask whether to show the question.
        watch = body_of(self.source, "watchForConfirmation")
        self.assertIn("MutationObserver", watch)
        self.assertIn("offerRecord()", watch)

    def test_the_fill_memory_is_cleared_once_recorded(self):
        background = code(EXT / "background.js")
        applied = body_of(background, "applied")
        self.assertIn("if (body.ok) await forgetFill(sender)", applied)
        self.assertNotRegex(background, r"setTimeout|setInterval")


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))

    def test_narrow_permissions(self):
        self.assertEqual(self.manifest["manifest_version"], 3)
        self.assertEqual(self.manifest["permissions"], ["storage"])
        hosts = {re.match(r"https?://([^/]+)/", h).group(1)
                 for h in self.manifest["host_permissions"]}
        self.assertEqual(hosts, BOARD_HOSTS | {"127.0.0.1"})
        text = json.dumps(self.manifest)
        for broad in ("<all_urls>", "*://*/*", "\"tabs\"", "scripting", "webRequest",
                      "cookies", "history", "externally_connectable", "nativeMessaging"):
            self.assertNotIn(broad, text)

    def test_content_scripts_only_on_the_three_boards(self):
        for block in self.manifest["content_scripts"]:
            for pattern in block["matches"]:
                host = re.match(r"https://([^/]+)/", pattern).group(1)
                self.assertIn(host, BOARD_HOSTS)
            self.assertEqual(block["js"], ["fill.js", "session.js", "research.js",
                                           "content.js"])

    def test_every_file_it_names_exists(self):
        names = ["background.js", "options.html", "options.js"] + [
            f for block in self.manifest["content_scripts"] for f in block["js"]]
        self.assertEqual(self.manifest["background"]["service_worker"], "background.js")
        for name in names:
            self.assertTrue((EXT / name).is_file(), name)


class TestNotShipped(unittest.TestCase):
    def test_the_extension_is_not_in_the_wheel_or_the_image(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('packages = ["jsa", "jsa.web"]', pyproject)
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("extension", dockerfile)

    def test_ci_runs_its_tests(self):
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("node --test extension/test/*.test.mjs", ci)


if __name__ == "__main__":
    unittest.main()
