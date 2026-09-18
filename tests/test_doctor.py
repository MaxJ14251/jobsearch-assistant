"""`jsa doctor`: what will not work yet, said before it bites.

The failures it reports are the ones this project actually produced: a null
work_authorization that refuses at the last step, tags no posting uses, a
credential line left out. It must report them and change nothing.
"""

import copy
import io
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import yaml

from jsa import db, doctor
from jsa.config import ROOT

EXAMPLE = ROOT / "profile" / "master_profile.example.yaml"

GOOD = {
    "identity": {"full_name": "Dana Rivers", "email": "dana@example.test",
                 "phone": "+1 (555) 555-0100"},
    "job_search_preferences": {
        "target_titles": ["Software Engineer"],
        "locations": ["Remote (US)", "Austin, TX"],
        "regions": {"home": ["Austin"]},
        "work_authorization": "US citizen",
        "compensation_floor_usd": "no_floor",
        "max_years_experience": 3,
    },
    "summaries": [{"id": "s", "family": "general", "text": "A summary."}],
    "experience": [{"id": "e", "company": "Acme", "title": "Technician",
                    "family": "technical_field", "bullets": [
                        {"id": "b1", "text": "Installed alarm panels.",
                         "tags": ["commissioning"], "strength": 1}]}],
    "education": [{"institution": "State University",
                   "credential": "BS Computer Science, 2020"}],
}

BROKEN = {
    "identity": {"full_name": "Your Full Name", "email": "you@example.com",
                 "phone": ""},
    "job_search_preferences": {
        "target_titles": [],
        "locations": ["Your City, ST"],
        "regions": {"home": ["Your City"]},
        "work_authorization": None,
        "compensation_floor_usd": None,
    },
    "summaries": [],
    "experience": [{"id": "e", "company": "Acme", "bullets": [
        {"id": "b1", "text": "Did a thing.", "tags": [], "strength": 1}]}],
    "education": [{"institution": "State University", "credential": ""}],
}


def tracker(jobs: int = 0) -> Path:
    path = Path(tempfile.mkdtemp()) / "t.db"
    db.init_db(path)
    if jobs:
        con = db.connect(path)
        con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
        for n in range(jobs):
            con.execute(
                "INSERT INTO jobs (company_id,title,url,description) "
                "VALUES (1,'Engineer',?,?)",
                (f"https://acme.test/{n}", "we need commissioning work"))
        con.commit()
        con.close()
    return path


def findings(report):
    return " | ".join(f.what + " " + f.fix for f in report.findings)


class TestABrokenProfile(unittest.TestCase):
    """Scenario a."""

    def setUp(self):
        con = db.connect(tracker())
        self.addCleanup(con.close)
        self.report = doctor.run(copy.deepcopy(BROKEN), con)

    def test_it_blocks(self):
        self.assertFalse(self.report.ok)

    def test_it_names_every_one(self):
        text = findings(self.report).lower()
        for expected in ("work_authorization", "compensation_floor_usd",
                         "target job titles", "name is still the example",
                         "phone", "no summary variants", "credential line",
                         "no tags"):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)

    def test_placeholder_locations_block(self):
        self.assertIn("placeholders", findings(self.report))

    def test_it_says_what_to_do_about_each_one(self):
        for finding in self.report.findings:
            self.assertTrue(finding.fix.strip(), finding.what)
            self.assertGreater(len(finding.fix), 20, finding.what)


class TestTheExampleProfile(unittest.TestCase):
    """Scenario b. It ships unfinished on purpose."""

    def test_it_blocks_and_says_which_fields(self):
        profile = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
        con = db.connect(tracker())
        self.addCleanup(con.close)
        report = doctor.run(profile, con)
        self.assertFalse(report.ok)
        text = findings(report).lower()
        self.assertIn("work_authorization", text)
        self.assertIn("name is still the example", text)


class TestAWorkingProfile(unittest.TestCase):
    """Scenario c."""

    def test_it_passes(self):
        con = db.connect(tracker(jobs=25))
        self.addCleanup(con.close)
        report = doctor.run(copy.deepcopy(GOOD), con)
        self.assertTrue(report.ok, findings(report))

    def test_an_empty_tracker_is_advice_not_a_blocker(self):
        con = db.connect(tracker())
        self.addCleanup(con.close)
        report = doctor.run(copy.deepcopy(GOOD), con)
        self.assertTrue(report.ok)
        self.assertIn("no postings yet", findings(report))

    def test_a_missing_profile_blocks_and_names_the_copy_command(self):
        report = doctor.run(None, None)
        self.assertFalse(report.ok)
        self.assertIn("master_profile.example.yaml", findings(report))

    def test_tags_that_match_nothing_are_reported_with_their_cost(self):
        profile = copy.deepcopy(GOOD)
        profile["experience"][0]["bullets"][0]["tags"] = ["commissioning", "zzz"]
        con = db.connect(tracker(jobs=25))
        self.addCleanup(con.close)
        report = doctor.run(profile, con)
        text = findings(report)
        self.assertIn("zzz", text)
        self.assertIn("cannot help a bullet get picked", text)
        self.assertTrue(report.ok, "an unused tag is not a blocker")


class TestItChangesNothing(unittest.TestCase):
    """Scenario d. Watch this fail against a version that fills a default."""

    def test_no_file_and_no_row_changes(self):
        path = tracker(jobs=25)
        profile_file = Path(tempfile.mkdtemp()) / "p.yaml"
        profile_file.write_text(yaml.safe_dump(BROKEN), encoding="utf-8")
        before_mtime = profile_file.stat().st_mtime_ns
        before_text = profile_file.read_text(encoding="utf-8")

        con = db.connect(path)
        counts = lambda: {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                          for t in ("jobs", "applications", "documents",
                                    "approvals", "application_events")}
        before_rows = counts()
        doctor.run(yaml.safe_load(profile_file.read_text(encoding="utf-8")), con)
        self.assertEqual(counts(), before_rows)
        con.close()

        self.assertEqual(profile_file.stat().st_mtime_ns, before_mtime)
        self.assertEqual(profile_file.read_text(encoding="utf-8"), before_text)

    def test_the_module_contains_no_writes(self):
        source = (ROOT / "jsa" / "doctor.py").read_text(encoding="utf-8")
        for needle in ("write_text", "safe_dump", "INSERT", "UPDATE", "DELETE",
                       "commit("):
            self.assertNotIn(needle, source, needle)

    def test_the_check_would_notice_a_helpful_version(self):
        """Fail-first, kept: a doctor that 'helpfully' fills a field."""
        path = tracker()
        con = db.connect(path)

        def helpful(profile, connection):
            connection.execute(
                "INSERT INTO companies (name, slug) VALUES ('Helpful','h')")
            connection.commit()
            return doctor.Report()

        with mock.patch("jsa.doctor.run", side_effect=helpful):
            before = con.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
            doctor.run({}, con)
            after = con.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        con.close()
        self.assertNotEqual(before, after,
                            "the row check must be able to see a write")


class TestTheCommand(unittest.TestCase):
    def run_doctor(self, profile, db_path):
        out = io.StringIO()
        from jsa import cli
        real = db.connect
        with mock.patch("jsa.cli.load_profile", return_value=profile), \
             mock.patch("jsa.db.connect", side_effect=lambda *a, **k: real(db_path)), \
             mock.patch("jsa.cli.DB_PATH", db_path), \
             redirect_stdout(out):
            code = cli.cmd_doctor(Namespace())
        return code, out.getvalue()

    def test_a_broken_profile_exits_1(self):
        code, out = self.run_doctor(copy.deepcopy(BROKEN), tracker())
        self.assertEqual(code, 1)
        self.assertIn("to fix before this works", out)
        self.assertIn("changed nothing", out)

    def test_a_good_profile_exits_0_and_says_what_is_next(self):
        code, out = self.run_doctor(copy.deepcopy(GOOD), tracker(jobs=25))
        self.assertEqual(code, 0)
        self.assertIn("jsa discover", out)

    def test_it_never_prints_a_key(self):
        """Scenario: presence, never the value."""
        import os
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-secret-value"}):
            _, out = self.run_doctor(copy.deepcopy(GOOD), tracker(jobs=25))
        self.assertNotIn("nvapi-secret-value", out)
        self.assertNotIn("secret", out.lower())


class TestItSpeaksPlainly(unittest.TestCase):
    """Scenario f. A newcomer has not met this project's vocabulary."""

    def test_no_internal_jargon(self):
        con = db.connect(tracker(jobs=25))
        self.addCleanup(con.close)
        profile = copy.deepcopy(GOOD)
        profile["experience"][0]["bullets"][0]["tags"] = ["zzz"]
        text = findings(doctor.run(profile, con)).lower()
        text += findings(doctor.run(copy.deepcopy(BROKEN), con)).lower()
        for jargon in ("dead tag", "idf", "family bonus", "rarity weight",
                       "verify_draft", "added share", "adr "):
            self.assertNotIn(jargon, text, jargon)


class TestTheFirstHourIsReal(unittest.TestCase):
    """Every command the README tells a newcomer to run must exist.

    The setup section told them to set ANTHROPIC_API_KEY. The tool reads
    NVIDIA_API_KEY from .env, so the first thing a stranger did was wrong.
    """

    @classmethod
    def setUpClass(cls):
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_every_jsa_command_in_the_readme_exists(self):
        import re
        from jsa import cli
        parser_names = set()
        real = cli.argparse.ArgumentParser.add_subparsers

        def capture(self, **kw):
            sub = real(self, **kw)
            add_parser = sub.add_parser

            def wrapped(name, *a, **k):
                parser_names.add(name)
                return add_parser(name, *a, **k)

            sub.add_parser = wrapped
            return sub

        with mock.patch.object(cli.argparse.ArgumentParser, "add_subparsers", capture), \
                redirect_stdout(io.StringIO()):
            try:
                cli.main(["--help"])      # prints usage; we only want the names
            except SystemExit:
                pass
        used = set(re.findall(r"-m jsa ([a-z-]+)", self.readme))
        self.assertTrue(used)
        self.assertLessEqual(used, parser_names, "README names a command that does not exist")

    def test_the_key_it_names_is_the_key_the_code_reads(self):
        from jsa.llm import API_KEY_ENV
        self.assertIn(API_KEY_ENV, self.readme)
        self.assertNotIn("ANTHROPIC_API_KEY", self.readme)


    def test_a_missing_key_is_a_message_not_a_traceback(self):
        """Walking the README with no key printed a traceback: a newcomer
        reads that as broken software, not as a step they skipped."""
        import io
        from jsa import cli, llm
        err = io.StringIO()
        with mock.patch("jsa.llm.api_key",
                        side_effect=llm.LLMError("NVIDIA_API_KEY is not set.")),              mock.patch("jsa.cli.cmd_tailor",
                        side_effect=lambda args: llm.api_key()),              redirect_stderr(err):
            code = cli.main(["tailor", "1"])
        self.assertEqual(code, 2)
        self.assertIn("NVIDIA_API_KEY is not set", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_the_first_hour_starts_with_the_profile_and_doctor(self):
        section = self.readme[self.readme.index("## Your first hour"):]
        section = section[:section.index("## Usage")]
        for expected in ("master_profile.example.yaml", "jsa doctor",
                         "jsa discover", "jsa matches", "jsa tailor",
                         "jsa review", "jsa applied"):
            self.assertIn(expected, section, expected)
        self.assertLess(section.index("jsa doctor"), section.index("jsa discover"))


if __name__ == "__main__":
    unittest.main()
