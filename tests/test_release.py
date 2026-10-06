"""Release-readiness checks.

The scanner test matters most: a security check that fails *open* is worse than
no check, because it is trusted. This one reported "clean" while silently
skipping the personal-data scan, because the hook's python lacked PyYAML.
"""

import subprocess
import sys
import unittest
from pathlib import Path

from jsa.config import ROOT


class TestSecretScanner(unittest.TestCase):
    SCANNER = ROOT / "tools" / "scan_secrets.py"

    def test_it_exists_and_is_wired_into_ci(self):
        self.assertTrue(self.SCANNER.exists())
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("scan_secrets.py", ci)

    def test_it_fails_closed_when_it_cannot_read_the_profile(self):
        """Never report clean when the check could not actually run."""
        import tools.scan_secrets as scanner
        self.assertTrue(hasattr(scanner, "ScannerUnavailable"))
        src = self.SCANNER.read_text(encoding="utf-8")
        # The bare `except Exception: return` that caused the bug must be gone.
        self.assertNotIn("except Exception:\n        return never, authorship", src)
        self.assertIn("raise ScannerUnavailable", src)

    def test_unavailable_exits_nonzero(self):
        import tools.scan_secrets as scanner
        self.assertIn("return 2", self.SCANNER.read_text(encoding="utf-8"))

    def test_key_patterns_cover_the_providers_in_use(self):
        import tools.scan_secrets as scanner
        labels = " ".join(label for label, _ in scanner.KEY_PATTERNS).lower()
        for provider in ("nvidia", "anthropic", "openai", "aws"):
            self.assertIn(provider, labels)

    def test_nvapi_key_shape_is_detected(self):
        import tools.scan_secrets as scanner
        pattern = dict((l, p) for l, p in scanner.KEY_PATTERNS)["NVIDIA API key"]
        self.assertIsNotNone(
            pattern.search("NVIDIA_API_KEY=nvapi-" + "a" * 40))

    def test_author_name_allowed_only_in_authorship_files(self):
        import tools.scan_secrets as scanner
        self.assertEqual(
            scanner.AUTHORSHIP_FILES, {"LICENSE", "README.md", "CONTRIBUTING.md"})

    def test_a_name_part_alone_is_caught_not_only_the_full_name(self):
        """A test docstring quoting a dashboard message put the operator's
        surname and phone fragments into a tracked file, and this scanner
        reported clean: it looked for the full name as one string."""
        import tools.scan_secrets as scanner
        never, authorship = [], scanner.personal_values()[1]
        found = scanner.scan_text(
            "test_example.py", "tests/test_example.py",
            "flagged: gmail.com, Ruthersfield, linkedin.com", never,
            ["Dana Ruthersfield", "Dana", "Ruthersfield"])
        self.assertTrue(any("Ruthersfield" in f for f in found), found)

    def test_a_phone_fragment_and_a_street_head_are_caught(self):
        """Also missed on 2026-09-24: the last four digits of the phone, and
        the house number with the street name, written without the rest."""
        import tools.scan_secrets as scanner
        never = ["7100", "42 Example Way"]
        found = scanner.scan_text(
            "test_example.py", "tests/test_example.py",
            "forbidden = ['7100', '42 Example Way']", never, [])
        self.assertEqual(len(found), 2, found)

    def test_a_short_name_part_is_not_treated_as_an_identity(self):
        """'Max' is also a builtin; 'Lee' is also somebody else's fixture."""
        import tools.scan_secrets as scanner
        self.assertGreaterEqual(scanner.NAME_PART_MIN, 4)

    def life_scan(self, profile: str, example: str = ""):
        """personal_values() against a profile written to a temporary root."""
        import tempfile
        from unittest import mock

        import tools.scan_secrets as scanner
        root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, root, True)
        (root / "profile").mkdir()
        (root / "profile" / "master_profile.yaml").write_text(profile, encoding="utf-8")
        (root / "jsa" / "resources").mkdir(parents=True)
        (root / "jsa" / "resources" / "master_profile.example.yaml").write_text(
            example or "experience: []\n", encoding="utf-8")
        self.addCleanup(lambda: (scanner.LIFE.clear(), scanner.LIFE_SHORT.clear()))
        with mock.patch.object(scanner, "ROOT", root):
            return scanner, scanner.personal_values()

    LIFE = ("experience:\n  - company: Harrowgate Instruments\n  - company: QRZ\n"
            "education:\n  - institution: Pellworth College\n")

    def test_employers_and_schools_are_personal_details(self):
        """Missed until 2026-09-28: a test named the operator's university in
        a list of false claims, and every scan said clean -- the list only
        held contact details."""
        scanner, (never, _) = self.life_scan(self.LIFE)
        self.assertIn("Harrowgate Instruments", never)
        self.assertIn("Pellworth College", never)
        found = scanner.scan_text("test_prep.py", "tests/test_prep.py",
                                  "I graduated from Pellworth College.", never, [])
        self.assertEqual(len(found), 1, found)

    def test_a_three_letter_employer_is_a_whole_word_in_any_case(self):
        """It reached an ADR inside a bullet id, lower-cased: b_<name>_sell."""
        scanner, (never, _) = self.life_scan(self.LIFE)
        hit = scanner.scan_text("0005.md", "docs/0005.md", "`b_qrz_sell` scored 1.69", never, [])
        miss = scanner.scan_text("x.py", "x.py", "the qrzx and aqrz tokens", never, [])
        self.assertEqual((len(hit), miss), (1, []))

    def test_what_the_example_ships_is_a_stand_in(self):
        """On a fresh clone and in CI the example IS the profile; its own
        invented school must not fail every test file that uses it."""
        example = "education:\n  - institution: Pellworth College\n"
        _, (never, _) = self.life_scan(self.LIFE, example)
        self.assertNotIn("Pellworth College", never)
        self.assertIn("Harrowgate Instruments", never)

    def test_the_example_profile_is_checked_for_a_real_employer(self):
        """Exempt from the contact-detail scan (its placeholders match
        themselves), but not from this: it carried the operator's employer
        for twelve days (n15)."""
        scanner, (never, auth) = self.life_scan(self.LIFE)
        found = scanner.scan_text(
            "master_profile.example.yaml", "profile/master_profile.example.yaml",
            "company: Harrowgate Instruments\nid: b_qrz_1\n", never, auth)
        self.assertEqual(len(found), 2, found)

    def test_working_tree_is_clean(self):
        """The real check, run as CI runs it."""
        result = subprocess.run(
            [sys.executable, str(self.SCANNER)], cwd=ROOT,
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         f"{result.stdout}\n{result.stderr}")


class TestReleaseFiles(unittest.TestCase):
    def test_license_exists_and_is_mit(self):
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", text)
        self.assertIn("2026", text)

    def test_contributing_states_the_four_rules(self):
        text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8").lower()
        for rule in ("no fabrication", "no identity to the api",
                     "no autonomous sending", "measure"):
            self.assertIn(rule, text)

    def test_ci_covers_three_python_versions(self):
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for version in ("3.11", "3.12", "3.13"):
            self.assertIn(f'"{version}"', ci)

    def test_ci_runners_are_pinned_and_actions_current(self):
        """n24: ubuntu-latest moves under a green build, and actions built
        for Node 20 stop running when GitHub removes it."""
        import re
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        runners = re.findall(r"runs-on:\s*(\S+)", ci)
        self.assertTrue(runners)
        self.assertNotIn("ubuntu-latest", runners)
        for action, oldest in (("checkout", 5), ("setup-python", 6)):
            for major in re.findall(rf"actions/{action}@v(\d+)", ci):
                self.assertGreaterEqual(int(major), oldest, f"{action}@v{major} is Node 20")

    def test_ci_sets_up_a_fresh_users_world(self):
        """The suite must pass with no API key and no job data."""
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        # `jsa init` starts the profile and .env from their templates
        # (plan 27; tests/test_home.py checks it does).
        self.assertIn("jsa init", ci)
        self.assertNotIn("cp ", ci.split("Set up a new user's world")[1].split("- name")[0])

    def test_dockerfile_runs_as_non_root(self):
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("USER jsa", text)
        self.assertIn("useradd", text)

    def test_dockerignore_excludes_personal_data(self):
        text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        for pattern in (".env", "master_profile.yaml", "*.db"):
            self.assertIn(pattern, text)

    def test_gitignore_excludes_local_agent_tooling(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".claude/", text)

    def test_readme_has_a_quickstart_and_limitations(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("## Quickstart", text)
        self.assertIn("## Limitations", text)

    def test_pre_commit_hook_prefers_the_venv_python(self):
        """A bare `python` may lack PyYAML, which is how the bug got in."""
        hook = (ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")
        self.assertIn(".venv", hook)
        self.assertIn("scan_secrets.py --staged", hook)



class TestProjectUrl(unittest.TestCase):
    """A repo URL is the project's address; a profile URL is personal."""

    def test_no_placeholder_urls_remain(self):
        for rel in ("README.md", "jsa/config.py"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("your-name", text,
                             f"{rel} still has the placeholder GitHub path")

    def test_badge_and_clone_url_agree_with_project_url(self):
        from jsa.config import PROJECT_URL
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"{PROJECT_URL}/actions/workflows/ci.yml/badge.svg", readme)
        self.assertIn(f"git clone {PROJECT_URL}", readme)

    def test_user_agent_advertises_the_project(self):
        from jsa.config import PROJECT_URL, user_agent
        self.assertIn(PROJECT_URL, user_agent())

    def test_one_version_number(self):
        """The User-Agent and the package read the same number (plan 27)."""
        import jsa
        from jsa.config import user_agent
        self.assertRegex(jsa.__version__, r"^\d+\.\d+\.\d+$")
        self.assertIn(f"jobsearch-assistant/{jsa.__version__} ", user_agent())
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('version = {attr = "jsa.__version__"}', pyproject)
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## [{jsa.__version__}]", changelog)

    def test_repo_url_is_allowed_in_code_but_profile_url_is_not(self):
        import tools.scan_secrets as scanner
        # A synthetic handle: embedding the real one would make this file
        # itself a scanner violation, which is how the last two bugs happened.
        profile_url = "https://github.com/someuser"
        repo_line = f"PROJECT_URL = \"{profile_url}/jobsearch-assistant\""
        bare_line = f"see {profile_url} for more"
        self.assertTrue(
            scanner.is_project_url(repo_line, repo_line.index(profile_url),
                                   profile_url),
            "a repo URL must be allowed in code")
        self.assertFalse(
            scanner.is_project_url(bare_line, bare_line.index(profile_url),
                                   profile_url),
            "a bare profile URL must still be flagged outside authorship files")


class TestHomePathScan(unittest.TestCase):
    """A committed .coverage embedded the builder's home directory 34 times.

    Every other NEVER-tier value is read from master_profile.yaml, and an OS
    account name is not a field the profile has -- so the check could not see
    it. This rule keys on the SHAPE of the path instead.

    Note how the fixtures below are assembled from pieces: writing them out
    literally would make this very file a violation, which is how the first
    draft of this test failed.
    """

    SEP = chr(92)                            # a single backslash
    WIN = "C:" + SEP + "Users" + SEP
    NIX = "/" + "home" + "/"
    MAC = "/" + "Users" + "/"

    def test_it_flags_a_real_home_path(self):
        from tools.scan_secrets import home_path_hits
        for text in (self.WIN + "dana" + self.SEP + "Desktop" + self.SEP + "a.py",
                     self.NIX + "dana/project/app.py",
                     self.MAC + "dana/code/app.py"):
            with self.subTest(text=text):
                self.assertTrue(home_path_hits(text), f"missed: {text}")

    def test_windows_backslashes_are_matched(self):
        """Regression: the character class was once forward-slash only.

        It compiled, ran, and reported clean on the exact Windows path it had
        been written to catch.
        """
        from tools.scan_secrets import home_path_hits
        self.assertTrue(home_path_hits(self.WIN + "dana" + self.SEP + "f.py"))

    def test_it_ignores_placeholders_and_ci_paths(self):
        from tools.scan_secrets import home_path_hits
        for text in (self.WIN + "you" + self.SEP + "jobsearch",
                     self.NIX + "runner/work/repo/repo",
                     "/usr/local/bin/python",
                     "see the Users table for home rows"):
            with self.subTest(text=text):
                self.assertFalse(home_path_hits(text), f"false alarm: {text}")

    def test_coverage_artifacts_are_gitignored(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".coverage", [l.strip() for l in ignored])

    def test_coverage_file_is_not_tracked(self):
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
        ).stdout.split()
        self.assertNotIn(".coverage", tracked,
                         ".coverage is tracked again -- it embeds absolute paths")

    def test_gitignored_files_are_skipped_or_kept_safely(self):
        """Both halves of drop_ignored's contract.

        In a git work tree an ignored file is dropped -- blocking on a
        regenerated .coverage would be a false alarm, and a check that cries
        wolf is one people bypass with --no-verify.

        A fresh clone (or an unpacked tarball) is NOT a git repo, so
        check-ignore cannot answer. There the file must be KEPT: scanning too
        much is the safe direction. The first version of this test asserted
        only the first half and failed the moment it ran outside a repo.
        """
        from tools.scan_secrets import drop_ignored
        in_repo = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=ROOT, capture_output=True, text=True,
        ).stdout.strip() == "true"

        probe = ROOT / ".coverage"
        created = not probe.exists()
        if created:
            probe.write_bytes(b"x")
        try:
            result = drop_ignored([probe])
        finally:
            if created:
                probe.unlink()

        if in_repo:
            self.assertEqual(result, [], "ignored file should be dropped")
        else:
            self.assertEqual(result, [probe],
                             "without git, every file must still be scanned")


class TestScanTiering(unittest.TestCase):
    """Skipping a file must not mean skipping every rule on it.

    One SKIP_FILES list was doing two different jobs. The committed templates
    belong on it for the personal scan -- their placeholders are the very
    strings that scan looks for, so a fresh clone self-reported as leaking.
    But the same entry also switched OFF key detection for .env.example: the
    single most likely place to paste a real key by accident was the one file
    exempt from looking for one.
    """

    FAKE_KEY = "nvapi-" + "FAKEKEYFORTESTINGONLY" + "0" * 30
    NEVER = ["12 Elm Street", "5551234567"]
    AUTHORSHIP = ["Dana Rivers"]

    def check(self, name, text):
        from tools.scan_secrets import scan_text
        return scan_text(name, name, text, self.NEVER, self.AUTHORSHIP)

    def test_real_key_in_env_example_is_caught(self):
        """The regression. This returned nothing before the tiers were split."""
        self.assertTrue(self.check(".env.example",
                                   f"NVIDIA_API_KEY={self.FAKE_KEY}"))

    def test_real_key_in_profile_template_is_caught(self):
        self.assertTrue(self.check("master_profile.example.yaml",
                                   f"note: {self.FAKE_KEY}"))

    def test_templates_do_not_self_report_on_placeholders(self):
        """Why the skip exists at all -- it must keep working."""
        self.assertFalse(self.check(".env.example",
                                    "NVIDIA_API_KEY=<nvapi-key-here>"))
        self.assertFalse(self.check("master_profile.example.yaml",
                                    "street: 12 Elm Street"))

    def test_personal_values_still_blocked_in_code(self):
        self.assertTrue(self.check("jsa/llm.py", "street: 12 Elm Street"))
        self.assertTrue(self.check("jsa/llm.py", "by Dana Rivers"))

    def test_author_name_still_allowed_in_authorship_files(self):
        self.assertFalse(self.check("README.md", "by Dana Rivers"))

    def test_live_files_are_never_scanned(self):
        """.env holds a real key by design and is gitignored.

        Blocking on it would make the scanner unusable on the author's own
        machine any time git could not answer the ignore query.
        """
        from tools.scan_secrets import NEVER_SCAN, SKIP_PERSONAL_FILES
        self.assertIn(".env", NEVER_SCAN)
        self.assertIn("master_profile.yaml", NEVER_SCAN)
        # The committed templates must NOT be on that list -- that was the bug.
        self.assertNotIn(".env.example", NEVER_SCAN)
        self.assertNotIn("master_profile.example.yaml", NEVER_SCAN)
        self.assertIn(".env.example", SKIP_PERSONAL_FILES)


if __name__ == "__main__":
    unittest.main()
