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

        for required in ("jsa/resources/.env.example",
                         "jsa/resources/master_profile.example.yaml",
                         "README.md", "requirements.txt", "pyproject.toml", ".gitignore"):
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

        # `jsa init` starts .env and the profile from their templates.
        code, out = run([str(py), "-m", "jsa", "init"], dest)
        made = (dest / ".env").exists() and (dest / "profile" / "master_profile.yaml").exists()
        steps.append(("python -m jsa init", code or (0 if made else 1)))
        if code or not made:
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

        print()
        print("=" * 70)
        print("4. THE INSTALLED FORM (plan 27)")
        print("=" * 70)
        installed = installed_form(dest, Path(tmp))
        for label, rc in installed:
            print(f"   {'ok  ' if rc == 0 else 'FAIL'} {label}")
        steps += installed

        failures = [l for l, rc in steps if rc != 0]
        print()
        print("=" * 70)
        print("VERDICT")
        print("=" * 70)
        print(f"   personal data leaks : {'none' if not leaks else 'FOUND'}")
        print(f"   README steps failing: {len(failures)} {failures if failures else ''}")
        return 1 if (leaks or failures) else 0


def installed_form(source: Path, tmp: Path) -> list[tuple[str, int]]:
    """Build a wheel from the clone, install it into its own venv, and run it
    with JSA_HOME pointing at an empty folder. Everything it writes must land
    in that folder; the schema and map data must come from the package."""
    import os

    steps: list[tuple[str, int]] = []
    wheels, venv, home, cwd = (tmp / "wheels", tmp / "installed", tmp / "home",
                               tmp / "elsewhere")
    cwd.mkdir()
    code, out = run([sys.executable, "-m", "pip", "wheel", str(source), "--no-deps",
                     "-q", "-w", str(wheels)], tmp)
    steps.append(("build a wheel", code))
    if code:
        print(f"      {out[-300:]}")
        return steps
    run([sys.executable, "-m", "venv", str(venv)], tmp)
    bindir = venv / ("Scripts" if sys.platform == "win32" else "bin")
    py = bindir / ("python.exe" if sys.platform == "win32" else "python")
    jsa = bindir / ("jsa.exe" if sys.platform == "win32" else "jsa")
    wheel = next(wheels.glob("*.whl"))
    code, out = run([str(py), "-m", "pip", "install", "-q", str(wheel)], tmp)
    steps.append(("pip install the wheel", code))
    if code:
        print(f"      {out[-300:]}")
        return steps
    package = Path(run([str(py), "-c", "import jsa, pathlib; "
                        "print(pathlib.Path(jsa.__file__).parent)"], cwd)[1].strip())
    before = sorted(p.relative_to(package) for p in package.rglob("*") if p.is_file())
    env = {**os.environ, "JSA_HOME": str(home)}
    env.pop("JSA_DB", None)

    def jsa_run(*args: str) -> tuple[int, str]:
        proc = subprocess.run([str(jsa), *args], cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=600)
        return proc.returncode, proc.stdout + proc.stderr

    code, out = jsa_run("init")
    steps.append(("jsa init (the command, not python -m)", code))
    code, out = jsa_run("matches", "--limit", "3")
    steps.append(("jsa matches --limit 3", code))
    code, out = jsa_run("doctor")
    steps.append(("jsa doctor runs (findings are expected)",
                  0 if code in (0, 1) and "Traceback" not in out else 1))
    proc = subprocess.run(
        [str(py), "-c", "from jsa import basemap, config, places, roles; "
         "print(places.data_is_present(), basemap.available(), config.SCHEMA_PATH.exists(), "
         "roles.occupation_for('Software Engineer') is not None)"],
        cwd=cwd, env=env, capture_output=True, text=True)
    steps.append(("schema, map and role data load from the package",
                  0 if proc.stdout.strip() == "True True True True" else 1))
    after = sorted(p.relative_to(package) for p in package.rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts)
    before = [p for p in before if "__pycache__" not in p.parts]
    stray = [p for p in cwd.rglob("*")] + [p for p in after if p not in before]
    steps.append(("nothing written outside JSA_HOME", 0 if not stray else 1))
    if stray:
        print(f"      written outside JSA_HOME: {stray[:5]}")
    for name in ("jobsearch.db", ".env", "profile/master_profile.yaml"):
        steps.append((f"{name} is in JSA_HOME", 0 if (home / name).exists() else 1))
    return steps


if __name__ == "__main__":
    raise SystemExit(main())
