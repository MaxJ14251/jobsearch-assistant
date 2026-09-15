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

    def test_ci_sets_up_a_fresh_users_world(self):
        """The suite must pass with no API key and no job data."""
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("master_profile.example.yaml", ci)
        self.assertIn("jsa init", ci)

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
