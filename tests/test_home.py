"""Where the person's files live (plan 27): config.HOME.

JSA_HOME if set; else the clone itself (today's layout, unchanged); else a
per-user folder. Shipped files come from jsa/resources/. Stored document
paths resolve against HOME, which is the clone for every existing tracker.

Layouts other than this process's own are read in a subprocess, so the
config module here is never reloaded (a reload would make new exception
classes that other modules' `except` clauses no longer catch).
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsa import config

ROOT = Path(__file__).resolve().parents[1]
SHOW = ("import json; from jsa import config as c; print(json.dumps({k: str(getattr(c, k)) "
        "for k in ('HOME', 'PROFILE_PATH', 'ENV_PATH', 'DB_PATH', 'OUTPUT_DIR', "
        "'COMPANIES_PATH', 'SEED_COMPANIES')}))")


def layout(env: dict) -> dict:
    full = {k: v for k, v in os.environ.items() if k not in ("JSA_HOME", "JSA_DB")}
    full.update(env)
    out = subprocess.run([sys.executable, "-c", SHOW], cwd=ROOT, env=full,
                         capture_output=True, text=True, check=True).stdout
    return {k: Path(v) for k, v in json.loads(out).items()}


class Home(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)


class TestWhereHomeIs(Home):
    def test_a_clone_keeps_todays_layout(self):
        got = layout({})
        self.assertTrue(config.running_from_clone())
        self.assertEqual(got["HOME"], config.ROOT)
        self.assertEqual(got["PROFILE_PATH"], config.ROOT / "profile" / "master_profile.yaml")
        self.assertEqual(got["DB_PATH"], config.ROOT / "jobsearch.db")
        self.assertEqual(got["OUTPUT_DIR"], config.ROOT / "output")
        self.assertEqual(got["COMPANIES_PATH"], got["SEED_COMPANIES"])   # the tracked list

    def test_jsa_home_wins(self):
        got = layout({"JSA_HOME": str(self.dir)})
        self.assertEqual(got["HOME"], self.dir)
        self.assertEqual(got["PROFILE_PATH"], self.dir / "profile" / "master_profile.yaml")
        self.assertEqual(got["ENV_PATH"], self.dir / ".env")
        self.assertEqual(got["COMPANIES_PATH"], self.dir / "config" / "companies.yaml")

    def test_jsa_db_still_wins_for_the_tracker(self):
        got = layout({"JSA_HOME": str(self.dir), "JSA_DB": str(self.dir / "x.db")})
        self.assertEqual(got["DB_PATH"], self.dir / "x.db")

    def test_installed_without_jsa_home_is_a_per_user_folder(self):
        env = {"APPDATA": str(self.dir / "roaming"), "XDG_DATA_HOME": str(self.dir / "share")}
        with mock.patch.dict(os.environ, env), mock.patch.object(
                config, "running_from_clone", return_value=False):
            os.environ.pop("JSA_HOME", None)
            home = config._home()
        expected = (self.dir / "roaming" / "jsa" if os.name == "nt"
                    else self.dir / "share" / "jsa")
        self.assertEqual(home, expected)

    def test_a_wheel_install_is_not_a_clone(self):
        site = self.dir / "site-packages"
        (site / "jsa").mkdir(parents=True)
        self.assertFalse(config.running_from_clone(site))
        (site / "pyproject.toml").write_text("", encoding="utf-8")
        self.assertTrue(config.running_from_clone(site))

    def test_shipped_files_come_from_the_package(self):
        for path in (config.SCHEMA_PATH, config.EXAMPLE_PROFILE, config.ENV_EXAMPLE,
                     config.SEED_COMPANIES, config.COACH_PATH):
            self.assertTrue(path.is_file(), path)
            self.assertEqual(path.parent, config.RESOURCES)
        self.assertTrue((config.DATA_DIR / "us_places.csv.gz").is_file())


class TestInit(Home):
    def run_init(self) -> str:
        env = {k: v for k, v in os.environ.items() if k not in ("JSA_HOME", "JSA_DB")}
        env["JSA_HOME"] = str(self.dir)
        proc = subprocess.run([sys.executable, "-m", "jsa", "init"], cwd=ROOT, env=env,
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_it_starts_your_files_from_the_templates(self):
        out = self.run_init()
        self.assertEqual((self.dir / "profile" / "master_profile.yaml").read_bytes(),
                         config.EXAMPLE_PROFILE.read_bytes())
        self.assertEqual((self.dir / ".env").read_bytes(), config.ENV_EXAMPLE.read_bytes())
        self.assertEqual((self.dir / "config" / "companies.yaml").read_bytes(),
                         config.SEED_COMPANIES.read_bytes())
        self.assertTrue((self.dir / "jobsearch.db").exists())
        self.assertIn("created", out)

    def test_it_never_overwrites(self):
        (self.dir / "profile").mkdir()
        (self.dir / "profile" / "master_profile.yaml").write_text("mine\n", encoding="utf-8")
        (self.dir / ".env").write_text("NVIDIA_API_KEY=\n", encoding="utf-8")
        (self.dir / "config").mkdir()
        (self.dir / "config" / "companies.yaml").write_text("sources: []\n", encoding="utf-8")
        out = self.run_init()
        self.assertEqual((self.dir / "profile" / "master_profile.yaml").read_text(
            encoding="utf-8"), "mine\n")
        self.assertEqual((self.dir / ".env").read_text(encoding="utf-8"), "NVIDIA_API_KEY=\n")
        self.assertEqual((self.dir / "config" / "companies.yaml").read_text(
            encoding="utf-8"), "sources: []\n")
        self.assertIn("never overwritten", out)

    def test_an_installed_companies_list_is_seeded_on_first_use(self):
        target = self.dir / "config" / "companies.yaml"
        with mock.patch.object(config, "COMPANIES_PATH", target):
            self.assertEqual(config.seed_companies(target), target)
        self.assertEqual(target.read_bytes(), config.SEED_COMPANIES.read_bytes())
        target.write_text("sources: []\n", encoding="utf-8")
        config.seed_companies(target)
        self.assertEqual(target.read_text(encoding="utf-8"), "sources: []\n")


class TestStoredDocumentPaths(Home):
    def test_a_relative_path_resolves_against_home(self):
        from jsa import approvals
        (self.dir / "output").mkdir()
        (self.dir / "output" / "resume.docx").write_bytes(b"doc")
        with mock.patch("jsa.config.HOME", self.dir):
            digest = approvals._file_sha256("output/resume.docx")
        self.assertEqual(digest, hashlib.sha256(b"doc").hexdigest())


if __name__ == "__main__":
    unittest.main()
