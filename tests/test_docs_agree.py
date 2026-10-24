"""The README and SECURITY.md agree with the code on what can be counted (G4).

A fact-check of a project write-up (2026-10-23) found the README giving four
different source counts, a test count of 888, scoring weights without pay,
and a hard reject (a required clearance) the code never had. Each check here
reads the fact from the code and the claim from the docs.

Dated measurements are history, not claims about now: a paragraph that
carries an ISO date (2026-09-26) is left alone.
"""

import json
import re
import unittest
from pathlib import Path

from jsa import config, intake, scoring, sources

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
SECURITY = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
DATED = re.compile(r"\b20\d\d-\d\d-\d\d\b")
EMPLOYER_KINDS = {"greenhouse", "lever", "ashby", "workday", "workable", "recruitee", "custom"}


def current(text: str) -> str:
    """The text without its dated paragraphs."""
    return "\n\n".join(p for p in re.split(r"\n\s*\n", text) if not DATED.search(p))


def sources_by_kind() -> dict[str, int]:
    out: dict[str, int] = {}
    for entry in config.load_sources():
        if entry.get("verified"):
            out[entry["kind"]] = out.get(entry["kind"], 0) + 1
    return out


class TestSourceCounts(unittest.TestCase):
    def setUp(self):
        self.all = config.load_sources()
        self.verified = sources_by_kind()
        self.text = current(README)

    def test_every_count_of_sources_matches_the_list(self):
        employer = sum(n for k, n in self.verified.items() if k in EMPLOYER_KINDS)
        rss = self.verified.get("rss", 0)
        facts = {
            r"(\d+) sources:": len(self.all),
            r"(\d+) of \d+ configured sources work": sum(self.verified.values()),
            r"\d+ of (\d+) configured sources": len(self.all),
            r"(\d+) employer boards": employer,
            r"(\d+) (?:remote-job )?RSS feeds": rss,
            r"(\d+) verified feeds": employer + rss,
            r"(\d+) employer feeds and aggregators": employer + rss,
        }
        seen = 0
        for pattern, truth in facts.items():
            for found in re.findall(pattern, self.text):
                seen += 1
                with self.subTest(claim=pattern, says=found):
                    self.assertEqual(int(found), truth)
        self.assertGreaterEqual(seen, 5, "the README's source counts were not found")

    def test_the_named_board_types_are_the_fetchers(self):
        line = re.search(r"\*\*Supported board types:\*\*(.*?)\.\s", self.text, re.S).group(1)
        named = {k for k in sources.FETCHERS
                 if k in line.lower().replace("the muse", "themuse")}
        for kind in ("greenhouse", "lever", "ashby", "workday", "workable", "recruitee",
                     "rss", "themuse", "usajobs"):
            self.assertIn(kind, named, f"{kind} is a fetcher the README doesn't name")
        self.assertIn("two company-specific APIs", line.replace("\n", " "))
        self.assertEqual(self.verified.get("custom"), 2)


class TestWhatTheToolReads(unittest.TestCase):
    def test_links_jsa_add_reads(self):
        boards = {b.strip() for b in re.split(r",| or ", intake.SUPPORTED)}
        sentence = re.search(r"A ([A-Z][\w ,]+) link is read from", README).group(1)
        self.assertEqual({b.strip() for b in re.split(r",| or ", sentence)}, boards)

    def test_the_extension_runs_on_the_boards_the_docs_name(self):
        manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
        hosts = {m.split("//")[1].split("/")[0]
                 for script in manifest["content_scripts"] for m in script["matches"]}
        self.assertEqual(hosts, {"boards.greenhouse.io", "job-boards.greenhouse.io",
                                 "jobs.lever.co", "jobs.ashbyhq.com"})
        self.assertIn("Greenhouse, Lever and Ashby", README)


class TestScoring(unittest.TestCase):
    def test_the_weights(self):
        text = " ".join(README.split())
        claim = re.search(r"fit \(title match (\d+)%, location (\d+)%, keyword overlap "
                          r"(\d+)%, seniority (\d+)%\) makes up (\d+)% of it and stated pay "
                          r"the other (\d+)%", text)
        self.assertIsNotNone(claim, "the README's scoring weights sentence changed shape")
        said = [int(x) / 100 for x in claim.groups()]
        self.assertEqual(said, [scoring.W_TITLE, scoring.W_LOCATION, scoring.W_KEYWORDS,
                                scoring.W_SENIORITY, scoring.W_FIT, scoring.W_COMPENSATION])

    def test_the_hard_rejects(self):
        import inspect
        text = " ".join(README.split())
        rejects = re.search(r"\*\*Hard rejects\*\*(.*?)\n?\"Manager\"", text + '"Manager"',
                            re.S).group(1)
        body = inspect.getsource(scoring.score_job) + inspect.getsource(scoring.title_rejection)
        claims = {"exclude_keywords": "exclude_keywords", "senior": "senior",
                  "people-management": "people-management", "max_years_experience": "years_filter",
                  "outside the US": "is_non_us", "floor": "compensation_floor"}
        for said, code in claims.items():
            with self.subTest(reject=said):
                self.assertIn(said, rejects)
                self.assertIn(code, body)
        # A clearance is shown as a flag, never a reject: not in the code's
        # rejects, and not in the README's list (only in the sentence after it
        # that says so).
        self.assertNotIn("clearance", body.split("# --- weighted signals")[0])
        listed, said, _ = rejects.partition("A required security clearance is not a reject")
        self.assertTrue(said, "the README no longer says a clearance isn't a reject")
        self.assertNotIn("clearance", listed)


class TestNamedThingsExist(unittest.TestCase):
    def test_every_command_the_docs_name_exists(self):
        from jsa import cli
        parser = cli.build_parser() if hasattr(cli, "build_parser") else None
        source = (ROOT / "jsa" / "cli.py").read_text(encoding="utf-8")
        commands = set(re.findall(r'add_parser\(\s*"([a-z-]+)"', source))
        named = set(re.findall(r"`jsa ([a-z][a-z-]+)", README + SECURITY))
        self.assertTrue(named)
        self.assertEqual(sorted(named - commands), [], "the docs name commands that don't exist")
        del parser

    def test_every_setting_the_docs_name_is_read_by_the_code(self):
        code = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "jsa").rglob("*.py"))
        named = set(re.findall(r"\b(JSA_[A-Z_]+|NVIDIA_API_KEY|MUSE_API_KEY|USAJOBS_[A-Z_]+)\b",
                               README + SECURITY))
        self.assertTrue(named)
        missing = sorted(n for n in named if n not in code)
        self.assertEqual(missing, [], "the docs name settings no code reads")


class TestTestCount(unittest.TestCase):
    def test_the_stated_test_count_is_close(self):
        defined = sum(len(re.findall(r"^\s+def test_", p.read_text(encoding="utf-8"), re.M))
                      for p in (ROOT / "tests").glob("test_*.py"))
        for found in re.findall(r"About ([\d,]+) Python tests|about ([\d,]+) tests",
                                current(README)):
            stated = int((found[0] or found[1]).replace(",", ""))
            with self.subTest(stated=stated):
                # "About": within 5% of the tests defined today.
                self.assertLess(abs(stated - defined) / defined, 0.05,
                                f"README says about {stated}; {defined} are defined")


if __name__ == "__main__":
    unittest.main()
