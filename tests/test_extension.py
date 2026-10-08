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
            self.assertEqual(block["js"], ["fill.js", "content.js"])

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
