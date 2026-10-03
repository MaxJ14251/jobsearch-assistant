"""Simulate a fresh clone and follow the README literally.

Two questions this answers, neither of which can be answered by inspection:

1. Does anything personal survive into what would actually be published?
2. Can a stranger follow the README exactly and end up with a working tool?

Any step where you have to deviate from your own written instructions is a bug
in the instructions, not a quirk of the reader.

    python tools/fresh_clone_check.py
"""

from __future__ import annotations

import fnmatch
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The personal-data scan is NOT reimplemented here. tools/scan_secrets.py is
# the single source of truth; a second copy drifted out of sync twice (it did
# not learn that a repo URL is a project address rather than a profile link),
# and a check that disagrees with the one CI runs is worse than no check.


def ignored_patterns() -> list[str]:
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


def is_ignored(rel: Path, patterns: list[str]) -> bool:
    parts = rel.as_posix()
    for pat in patterns:
        clean = pat.rstrip("/")
        if fnmatch.fnmatch(parts, clean) or fnmatch.fnmatch(rel.name, clean):
            return True
        if parts.startswith(clean + "/") or f"/{clean}/" in f"/{parts}":
            return True
    return False


def make_clone(dest: Path) -> tuple[int, int]:
    patterns = ignored_patterns()
    copied = skipped = 0
    for src in ROOT.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(ROOT)
        if is_ignored(rel, patterns) or ".git" in rel.parts:
            skipped += 1
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        copied += 1
    return copied, skipped


def scan(dest: Path) -> tuple[int, str]:
    """Run the real scanner against the clone, exactly as CI does."""
    scanner = dest / "tools" / "scan_secrets.py"
    if not scanner.exists():
        return 1, "tools/scan_secrets.py is missing from the clone"
    return run([sys.executable, str(scanner)], dest)


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    # errors="replace" because a subprocess can emit bytes the console codec
    # cannot decode, and encoding= must be set for text mode to apply it.
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output[-900:]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "jobsearch"
        copied, skipped = make_clone(dest)
        print("=" * 70)
        print("1. FRESH CLONE")
        print("=" * 70)
        print(f"   copied {copied} file(s), excluded {skipped} by .gitignore")

        for required in (".env.example", "profile/master_profile.example.yaml",
                         "README.md", "requirements.txt", ".gitignore"):
            ok = (dest / required).exists()
            print(f"   {'ok ' if ok else 'MISSING'} {required}")
        for forbidden in (".env", "profile/master_profile.yaml", "jobsearch.db",
                          "backups"):
            leaked = (dest / forbidden).exists()
            print(f"   {'LEAKED ' if leaked else 'ok     '} {forbidden} excluded")

        print()
        print("=" * 70)
        print("2. PERSONAL DATA SCAN")
        print("=" * 70)
        # The clone has no profile of its own, so the scanner is also run from
        # the real working tree: that run knows the personal values and looks
        # for them across every tracked file.
        tree_code, tree_out = run(
            [sys.executable, str(ROOT / "tools" / "scan_secrets.py")], ROOT)
        clone_code, clone_out = scan(dest)
        print(f"   working tree : {'clean' if tree_code == 0 else 'LEAKS'}")
        print(f"   fresh clone  : {'clean' if clone_code == 0 else 'LEAKS'}")
        if tree_code or clone_code:
            print((tree_out + clone_out)[-700:])
        leaks = tree_code or clone_code

        print()
        print("=" * 70)
        print("3. FOLLOW THE README LITERALLY")
        print("=" * 70)
        steps = []

        shutil.copy2(dest / ".env.example", dest / ".env")
        shutil.copy2(dest / "profile" / "master_profile.example.yaml",
                     dest / "profile" / "master_profile.yaml")
        print("   copied both example files, as the README instructs")

        code, out = run([sys.executable, "-m", "venv", ".venv"], dest)
        steps.append(("create venv", code))
        py = dest / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / "python.exe"
        if not py.exists():
            py = dest / ".venv" / "bin" / "python"

        code, out = run([str(py), "-m", "pip", "install", "-q", "-r",
                         "requirements.txt"], dest)
        steps.append(("pip install -r requirements.txt", code))
        if code:
            print(f"      {out[-300:]}")

        code, out = run([str(py), "-m", "jsa", "init"], dest)
        steps.append(("python -m jsa init", code))
        if code:
            print(f"      {out[-300:]}")

        code, out = run([str(py), "-m", "unittest", "discover", "-s", "tests",
                         "-t", "."], dest)
        steps.append(("run the test suite", code))
        if code:
            print(f"      {out[-400:]}")

        code, out = run([str(py), "-m", "jsa", "matches", "--limit", "3"], dest)
        # An empty tracker legitimately has nothing to show.
        steps.append(("python -m jsa matches", code))

        # README step 2's alternative, with no API key yet: it must refuse
        # with the key message (exit 2), not crash and not write a draft.
        run([str(py), "-c", "from docx import Document; d = Document(); "
             "d.add_paragraph('EXPERIENCE'); d.save('resume.docx')"], dest)
        code, out = run([str(py), "-m", "jsa", "import-resume", "resume.docx"], dest)
        clean = (code == 2 and "Traceback" not in out
                 and not (dest / "profile" / "master_profile.draft.yaml").exists())
        steps.append(("import-resume with no key refuses cleanly", 0 if clean else 1))
        if not clean:
            print(f"      {out[-300:]}")

        for label, rc in steps:
            print(f"   {'ok  ' if rc == 0 else 'FAIL'} {label}")

        failures = [l for l, rc in steps if rc != 0]
        print()
        print("=" * 70)
        print("VERDICT")
        print("=" * 70)
        print(f"   personal data leaks : {'none' if not leaks else 'FOUND'}")
        print(f"   README steps failing: {len(failures)} {failures if failures else ''}")
        return 1 if (leaks or failures) else 0


if __name__ == "__main__":
    raise SystemExit(main())
