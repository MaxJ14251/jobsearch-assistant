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
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Values that must never appear in a published file. Read from the live profile
# so the check cannot drift out of date as the profile changes.
def secrets_to_scan() -> list[str]:
    import yaml

    found: list[str] = []
    profile = ROOT / "profile" / "master_profile.yaml"
    if profile.exists():
        data = yaml.safe_load(profile.read_text(encoding="utf-8")) or {}
        ident = data.get("identity") or {}
        loc = ident.get("location") or {}
        links = data.get("links") or {}
        for value in (ident.get("full_name"), ident.get("email"),
                      ident.get("phone"), loc.get("street"), loc.get("postal_code"),
                      links.get("linkedin"), links.get("github")):
            if isinstance(value, str) and len(value.strip()) >= 5:
                found.append(value.strip())
        # Digits-only phone, since formatting varies.
        digits = re.sub(r"\D", "", ident.get("phone") or "")
        if len(digits) >= 10:
            found.append(digits[-10:])
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value and not value.startswith("<") and len(value) > 12:
                    found.append(value)
    return found


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


def scan(dest: Path, needles: list[str]) -> list[str]:
    hits = []
    for path in dest.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for needle in needles:
            if needle.lower() in text.lower():
                hits.append(f"{path.relative_to(dest)}: {needle[:24]!r}")
    return hits


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
    needles = secrets_to_scan()
    print(f"scanning for {len(needles)} personal value(s)\n")

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
        for forbidden in (".env", "profile/master_profile.yaml", "jobsearch.db"):
            leaked = (dest / forbidden).exists()
            print(f"   {'LEAKED ' if leaked else 'ok     '} {forbidden} excluded")

        print()
        print("=" * 70)
        print("2. PERSONAL DATA SCAN")
        print("=" * 70)
        hits = scan(dest, needles)
        if hits:
            for h in hits[:20]:
                print(f"   LEAK  {h}")
            print(f"\n   {len(hits)} leak(s) — do not publish")
        else:
            print("   clean: no personal value appears in any published file")

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

        for label, rc in steps:
            print(f"   {'ok  ' if rc == 0 else 'FAIL'} {label}")

        failures = [l for l, rc in steps if rc != 0]
        print()
        print("=" * 70)
        print("VERDICT")
        print("=" * 70)
        print(f"   personal data leaks : {len(hits)}")
        print(f"   README steps failing: {len(failures)} {failures if failures else ''}")
        return 1 if (hits or failures) else 0


if __name__ == "__main__":
    raise SystemExit(main())
