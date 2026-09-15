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


if __name__ == "__main__":
    unittest.main()
