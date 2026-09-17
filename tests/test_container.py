"""The container check exists, runs in CI, and cannot publish anything.

Docker cannot run on the development machine, so tools/docker_check.sh is
executed only by the CI "docker" job. These tests guard the parts of that
arrangement a local run CAN see.
"""

import re
import unittest

import yaml

from jsa.config import ROOT

SCRIPT = ROOT / "tools" / "docker_check.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


class TestContainerCheck(unittest.TestCase):
    def test_ci_runs_the_script(self):
        jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
        self.assertIn("docker", jobs)
        steps = " ".join(str(s.get("run", "")) for s in jobs["docker"]["steps"])
        self.assertIn("tools/docker_check.sh", steps)

    def test_branches_under_ci_are_tested(self):
        """A ci/ branch must run the workflow before anything is merged."""
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        # PyYAML reads the bare key `on` as True.
        triggers = workflow.get("on", workflow.get(True))
        self.assertIn("ci/**", triggers["push"]["branches"])

    def test_nothing_is_published(self):
        text = (SCRIPT.read_text(encoding="utf-8")
                + WORKFLOW.read_text(encoding="utf-8"))
        for needle in ("docker push", "docker login", "registry",
                       "build-push-action", "${{ secrets"):
            if needle == "registry":
                # Allowed only in the comment that says nothing goes to one.
                self.assertNotRegex(text, r"(?m)^\s*[^#\s].*registry", needle)
                continue
            self.assertNotIn(needle, text)

    def test_every_claim_is_checked(self):
        script = SCRIPT.read_text(encoding="utf-8")
        for needle in ("--entrypoint id", "refusing to bind", "JSA_ALLOW_PUBLIC_BIND=1",
                       "docker restart", "docker save", "matches", "127.0.0.1:"):
            self.assertIn(needle, script)

    def test_decoys_never_overwrite_real_files(self):
        """The script plants a fake .env and profile. Run by hand in a real
        checkout, it must refuse rather than replace yours."""
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("refusing to overwrite", script)
        self.assertLess(script.index("refusing to overwrite"),
                        script.index("printf 'NVIDIA_API_KEY"))
        # Cleanup deletes only files carrying this run's marker.
        self.assertRegex(script, r'grep -q "\$MARK" "\$f"')

    def test_personal_patterns_apply_at_every_depth(self):
        """.dockerignore matches from the context root: "*.db" missed
        config/decoy.db, and the first CI run caught it in an image layer."""
        lines = [line.strip() for line in
                 (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
                 if line.strip() and not line.startswith("#")]
        for name in (".env", "master_profile.yaml", "*.db", "*.db-wal",
                     "*.db-shm", "*.log", "output/", "documents/"):
            with self.subTest(pattern=name):
                self.assertIn(f"**/{name}", lines)
                self.assertNotIn(name, lines, "a root-only copy invites confusion")

    def test_the_script_is_stored_with_unix_line_endings(self):
        self.assertIn("*.sh text eol=lf",
                      (ROOT / ".gitattributes").read_text(encoding="utf-8"))

    def test_the_leak_scan_is_not_defeated_by_pipefail(self):
        """`tar | grep -q` under pipefail reports a found marker as failure."""
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertNotRegex(script, r"\|\s*grep -a?q[a]? \"\$MARK\"")


if __name__ == "__main__":
    unittest.main()
