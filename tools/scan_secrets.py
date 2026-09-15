"""Fail if a secret or personal detail reached a tracked file.

Runs in CI and from the pre-commit hook. Two tiers, because they are genuinely
different problems:

  NEVER ANYWHERE   API keys, street address, phone, postal code, private email.
                   These have no legitimate place in a published file.

  AUTHORSHIP ONLY  The author's name and profile URLs. A LICENSE says who holds
                   the copyright and a README says who wrote it; that is the
                   point of those files. Anywhere else is a leak.

    python tools/scan_secrets.py [--staged]
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Where the author's own name may legitimately appear.
AUTHORSHIP_FILES = {"LICENSE", "README.md", "CONTRIBUTING.md"}

SKIP_DIRS = {".git", ".venv", "__pycache__", "output", "documents", ".claude"}

# The files values are READ FROM, plus the templates they are copied from.
# Without the .example entries a fresh clone self-reports as leaking: the
# profile there *is* the example, so every placeholder trivially matches
# itself inside the committed template.
SKIP_FILES = {
    ".env",
    ".env.example",
    "master_profile.yaml",
    "master_profile.example.yaml",
}

# Credential shapes that are never acceptable, regardless of whose they are.
KEY_PATTERNS = [
    ("NVIDIA API key", re.compile(r"nvapi-[A-Za-z0-9_\-]{20,}")),
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OpenAI API key", re.compile(r"\bsk-[A-Za-z0-9]{32,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Generic bearer token", re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{30,}")),
]


class ScannerUnavailable(RuntimeError):
    """The scanner could not do its job. Never treated as 'clean'."""


def personal_values() -> tuple[list[str], list[str]]:
    """(never_anywhere, authorship_only), read from the live profile.

    Raises rather than returning empty lists when the profile cannot be read.
    Swallowing that error made the scanner silently degrade to key-patterns
    only: a commit containing the home address reported "clean" and went
    through, because the hook's `python` happened to lack PyYAML. A security
    check that fails open is worse than no check, because it is trusted.
    """
    never: list[str] = []
    authorship: list[str] = []
    path = ROOT / "profile" / "master_profile.yaml"
    if not path.exists():
        # A fresh clone genuinely has no profile; there is nothing to leak.
        return never, authorship
    try:
        import yaml
    except ImportError as exc:
        raise ScannerUnavailable(
            "PyYAML is not importable, so the profile cannot be read and "
            "personal data cannot be checked. Install it "
            "(pip install PyYAML) or point the hook at your venv python."
        ) from exc
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        raise ScannerUnavailable(
            f"{path.name} could not be parsed, so personal data cannot be "
            f"checked: {exc}"
        ) from exc

    ident = data.get("identity") or {}
    loc = ident.get("location") or {}
    links = data.get("links") or {}

    for value in (loc.get("street"), loc.get("postal_code"),
                  ident.get("phone"), ident.get("email")):
        if isinstance(value, str) and len(value.strip()) >= 5:
            never.append(value.strip())
    digits = re.sub(r"\D", "", ident.get("phone") or "")
    if len(digits) >= 10:
        never.append(digits[-10:])

    for value in (ident.get("full_name"), links.get("linkedin"),
                  links.get("github")):
        if isinstance(value, str) and len(value.strip()) >= 5:
            authorship.append(value.strip())
    return never, authorship


def is_project_url(text: str, index: int, value: str) -> bool:
    """True when this hit is a repository URL rather than a profile link.

    `https://github.com/<user>` is a personal profile and belongs only in
    authorship files. `https://github.com/<user>/<repo>` is the project's own
    address — it belongs in the README badge and in the User-Agent the tool
    sends to job boards, which is code. Blocking the second because it
    contains the first would force the tool to announce itself anonymously.
    """
    if "github.com/" not in value.lower():
        return False
    tail = text[index + len(value): index + len(value) + 2]
    return tail.startswith("/") and len(tail) > 1 and tail[1] not in " \t\n\"'"


def candidate_files(staged_only: bool) -> list[Path]:
    if staged_only:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=ROOT, capture_output=True, text=True,
        ).stdout
        return [ROOT / line.strip() for line in out.splitlines() if line.strip()]
    files = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        files.append(path)
    return files


def main(argv: list[str]) -> int:
    staged_only = "--staged" in argv
    try:
        never, authorship = personal_values()
    except ScannerUnavailable as exc:
        print(f"SCANNER UNAVAILABLE: {exc}", file=sys.stderr)
        print("Refusing to report 'clean' when the check could not run.",
              file=sys.stderr)
        return 2
    problems: list[str] = []

    for path in candidate_files(staged_only):
        if not path.exists() or path.name in SKIP_FILES:
            continue
        rel = path.relative_to(ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        for label, pattern in KEY_PATTERNS:
            if pattern.search(text):
                problems.append(f"{rel}: {label}")

        lowered = text.lower()
        for value in never:
            if value.lower() in lowered:
                problems.append(f"{rel}: personal detail {value[:16]!r}")
        if path.name not in AUTHORSHIP_FILES:
            for value in authorship:
                index = lowered.find(value.lower())
                if index == -1:
                    continue
                if is_project_url(lowered, index, value.lower()):
                    continue        # a repo URL, not a profile link
                problems.append(
                    f"{rel}: author identity {value[:22]!r} "
                    f"(allowed only in {', '.join(sorted(AUTHORSHIP_FILES))})"
                )

    scope = "staged files" if staged_only else "the working tree"
    if problems:
        print(f"BLOCKED — secrets or personal data in {scope}:\n", file=sys.stderr)
        for p in sorted(set(problems)):
            print(f"  {p}", file=sys.stderr)
        print("\nRemove them, or add the file to .gitignore.", file=sys.stderr)
        return 1
    print(f"clean: no secrets or personal data in {scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
