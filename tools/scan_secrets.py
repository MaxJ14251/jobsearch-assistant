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

# A name part shorter than this is a word: "Max" is also a builtin, "Lee" is
# also a surname in a test fixture. Four is where a name stops being generic.
NAME_PART_MIN = 4

SKIP_DIRS = {".git", ".venv", "__pycache__", "output", "documents", ".claude"}

# Compressed and binary files, read as text with errors ignored, are random
# letters -- and random letters eventually spell something. The shipped
# Census data reported an email address that existed only in the gzip
# stream. A scanner that cries wolf gets ignored, which is the actual risk
# here. Anything listed is DECOMPRESSED or skipped, never scanned as bytes.
BINARY_SUFFIXES = {".zip", ".docx", ".pdf", ".png", ".jpg", ".jpeg", ".gif",
                   ".ico", ".woff", ".woff2", ".ttf", ".db", ".sqlite"}
TEXT_IN_A_WRAPPER = {".gz"}

# Two tiers, because "skip this file" was previously one list doing two jobs.
#
# NEVER_SCAN: local files that hold real values BY DESIGN. Both are gitignored
# and so cannot reach a commit. Scanning them would block on the user's own key
# every time git could not answer the ignore query.
NEVER_SCAN = {
    ".env",
    "master_profile.yaml",
}

# SKIP_PERSONAL_FILES: committed templates. Their placeholders are the very
# strings the personal scan looks for, so a fresh clone would self-report as
# leaking -- the profile there *is* the example, and every placeholder matches
# itself. Personal matching is therefore skipped for these.
#
# Key and home-path detection still runs on them. Folding these into one skip
# list had switched key detection off for .env.example, which is the single
# most likely place for somebody to paste a real key by accident: the one file
# that most needed the check was the one file exempt from it.
#
# The shipped Census data is the same shape of problem from the other end: a
# public list of every US town and ZIP code necessarily contains the
# operator's own ZIP, a town sharing a name with somebody's family name, and
# digit runs that match part of anybody's phone number. Those are facts
# about the United States,
# not about the operator. Key and home-path detection still run on them, so a
# key pasted into one is still caught. The basemap (n23) is a few million
# coordinate integers; some of them are bound to spell anybody's ZIP.
SKIP_PERSONAL_FILES = {
    ".env.example",
    "master_profile.example.yaml",
    "us_places.csv.gz",
    "us_zips.csv.gz",
    "us_basemap_coarse.json.gz",
    "us_basemap_medium.json.gz",
    "us_basemap_fine.json.gz",
}

# Credential shapes that are never acceptable, regardless of whose they are.
KEY_PATTERNS = [
    ("NVIDIA API key", re.compile(r"nvapi-[A-Za-z0-9_\-]{20,}")),
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OpenAI API key", re.compile(r"\bsk-[A-Za-z0-9]{32,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Generic bearer token", re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{30,}")),
]

# Generic stand-ins that carry no identity. A README showing C:\Users\you\... or
# a CI log under /home/runner/ is documentation, not a leak.
PLACEHOLDER_USERS = {
    "you", "your-name", "yourname", "user", "username", "me", "example",
    "runner", "root", "appuser", "home", "someone", "alice", "bob",
}

# Absolute home directories. A published file must not carry the path layout of
# the machine that built it: it leaks the OS account name, which on Windows is
# usually a real first name.
#
# This rule is deliberately NOT driven by master_profile.yaml. Everything else
# in the NEVER tier is a value read from the profile, and that is exactly why
# a committed .coverage went unnoticed for two commits -- it embedded
# C:\Users\<account>\... 34 times, and an OS account name is not a field the
# profile has. A check that can only find what it was told to look for cannot
# find this. The shape of the path is the signal.
HOME_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]Users[\\/]|(?:^|[\s\"'=(\[])[\\/](?:home|Users)[\\/])"
    r"([A-Za-z0-9_.\-]{2,})",
    re.IGNORECASE | re.MULTILINE,
)


# A US street address, by shape rather than by value.
#
# Every other personal rule is driven by master_profile.yaml, which means it can
# only find the operator's own details. The history audit's decoy carried a
# street address and a phone number belonging to nobody, and not one rule fired:
# a recruiter's address pasted into a note would have been just as invisible.
# It also missed a real one: the README named a home town and its postal code
# in prose, and neither is a field the profile has.
#
# Requiring the state code in address position (after a comma or an opening
# paren, uppercase) is what makes this usable: measured over all 77 tracked
# files it produced exactly one hit, the real one. The looser version also
# matched `pa: 85000` in three files.
US_ADDRESS_RE = re.compile(
    r"[,(]\s*(?:A[LKZR]|C[AOT]|DE|FL|GA|HI|I[DLNA]|K[SY]|LA"
    r"|M[EDAINSOT]|N[EVHJMYCD]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[TA]|W[AVIY])"
    r"\b[^\n]{0,8}?\b\d{5}(?:-\d{4})?\b")


def home_path_hits(text: str) -> list[str]:
    """Absolute home paths in `text`, minus generic placeholders."""
    hits = []
    for match in HOME_PATH_RE.finditer(text):
        account = match.group(1)
        if account.lower() in PLACEHOLDER_USERS:
            continue
        if account.startswith("<") or account.startswith("$"):
            continue
        hits.append(match.group(0).strip())
    return hits


# Third-party contact data. Every other rule in this file protects the operator;
# this one protects someone else, and the asymmetry is the point -- the operator
# chose to run this tool, the contact in their `contacts` table did not.
#
# The database is gitignored, so the real risk is a test fixture or an example
# containing a real person. Measured before adding this: the only addresses in
# 57 tracked files are someone@example.org and you@example.com, both on domains
# RFC 2606 reserves for exactly this purpose. So the rule lands with zero false
# positives today.
EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")

RESERVED_EMAIL_DOMAINS = (
    "example.com", "example.org", "example.net",
    ".test", ".invalid", ".localhost", ".example",
)

# Addresses that exist so that nobody can be reached at them. The rule above
# protects a contact; a no-reply mailbox is not a contact, and flagging the
# attribution trailer on every commit buried the one real finding of the
# history audit under eighty copies of a non-finding.
NOREPLY_LOCAL_PARTS = ("noreply", "no-reply", "donotreply", "do-not-reply")


def is_noreply(address: str) -> bool:
    local = address.rsplit("@", 1)[0].lower()
    domain = address.rsplit("@", 1)[-1].lower()
    return (local in NOREPLY_LOCAL_PARTS
            or domain.endswith("users.noreply.github.com"))


def real_email_addresses(text: str) -> list[str]:
    """Addresses that are not obviously placeholders, and can be replied to."""
    found = []
    for match in EMAIL_RE.finditer(text):
        domain = match.group(1).lower()
        if any(domain == d or domain.endswith(d) for d in RESERVED_EMAIL_DOMAINS):
            continue
        if is_noreply(match.group(0)):
            continue
        found.append(match.group(0))
    return found


class ScannerUnavailable(RuntimeError):
    """The scanner could not do its job. Never treated as 'clean'."""


def looks_like_a_placeholder(value: str) -> bool:
    """True for the example profile's stand-ins, which are not secrets.

    On a fresh clone `master_profile.yaml` IS the example -- fresh_clone_check
    copies it verbatim, as the README instructs -- so identity.email reads
    `you@example.com`. Without this, that placeholder became a blocked term and
    the scanner failed on any file that mentioned it, including its own comment
    explaining why example.com addresses are safe.

    The check is about the shape of the value, not a list of known examples,
    so it works for whatever placeholders a future template uses.
    """
    text = value.strip().lower()
    if not text:
        return True
    if "<" in text or ">" in text:              # <your street>
        return True
    if text.startswith(("your ", "your-", "you@", "example")):
        return True
    # "123 Example St", the example profile's street: a placeholder in the
    # middle rather than at the start.
    if "example" in re.split(r"[^a-z]+", text):
        return True
    if "@" in text:
        domain = text.rsplit("@", 1)[-1]
        if any(domain == d or domain.endswith(d)
               for d in RESERVED_EMAIL_DOMAINS):
            return True
    # "00000" and "11111" are not postal codes anybody lives in. The example
    # profile uses the first, so on a fresh clone every file mentioning five
    # zeros -- including the tests for the ZIP lookup -- read as a leak.
    if re.fullmatch(r"(\d)\1{4}", text):
        return True
    # 555 numbers are reserved for fiction for exactly this reason.
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 10 and digits[-7:].startswith("555"):
        return True
    return False


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
        if (isinstance(value, str) and len(value.strip()) >= 5
                and not looks_like_a_placeholder(value)):
            never.append(value.strip())
    digits = re.sub(r"\D", "", ident.get("phone") or "")
    if len(digits) >= 10:
        never.append(digits[-10:])
    # And each four-or-more digit group of it on its own: the same test
    # docstring carried the last four digits without the rest of the number.
    # Three-digit groups (an area code) are too common to match on.
    if not looks_like_a_placeholder(str(ident.get("phone") or "")):
        for group in re.findall(r"\d{4,}", ident.get("phone") or ""):
            never.append(group)

    # The house number and street name without the rest of the address: the
    # first release's tests listed the address in fragments, as a forbidden
    # list to check against, and the full string never appeared.
    street = str(loc.get("street") or "").strip()
    if street and not looks_like_a_placeholder(street):
        head = re.match(r"\d+\s+\S+", street)
        if head:
            never.append(head.group(0))

    for value in (ident.get("full_name"), links.get("linkedin"),
                  links.get("github")):
        if (isinstance(value, str) and len(value.strip()) >= 5
                and not looks_like_a_placeholder(value)):
            authorship.append(value.strip())

    # Each NAME PART on its own, not only the full name. A test docstring
    # quoting a dashboard message -- which listed the operator's surname,
    # given name and phone fragments -- was committed and pushed while this
    # scanner reported clean, because the full name never appeared as one
    # string. A surname identifies on its own.
    full_name = str(ident.get("full_name") or "")
    if not looks_like_a_placeholder(full_name):
        # A part of the EXAMPLE name ("Your Full Name" -> "Full", "Name") is an
        # ordinary English word, and on a fresh clone the example IS the
        # profile, so the whole name is judged before it is split.
        for part in re.split(r"[^A-Za-z]+", full_name):
            if len(part) >= NAME_PART_MIN and not looks_like_a_placeholder(part):
                authorship.append(part)
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
    return drop_ignored(files)


def drop_ignored(paths: list[Path]) -> list[Path]:
    """Remove files git already ignores.

    A .gitignore'd file cannot reach a commit, so blocking on one is a false
    alarm -- and a check that cries wolf on a regenerated .coverage is a check
    people start passing with --no-verify. If git cannot answer, every file is
    kept: scanning too much is the safe direction.
    """
    if not paths:
        return paths
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "-z", "--stdin"],
            cwd=ROOT, capture_output=True, text=True,
            # -z on BOTH sides. Without it git applies core.quotePath and
            # returns "C:\\Users\\..." -- quoted, backslashes doubled -- so every
            # membership test below silently missed and nothing was skipped.
            input="\0".join(str(p) for p in paths),
        )
    except OSError:
        return paths
    if proc.returncode not in (0, 1):   # 0 = some ignored, 1 = none ignored
        return paths
    ignored = {part for part in proc.stdout.split("\0") if part}
    if not ignored:
        return paths
    return [p for p in paths if str(p) not in ignored]


def scan_text(name: str, rel: str, text: str,
              never: list[str], authorship: list[str]) -> list[str]:
    """Every rule that applies to one file, as a list of problem strings.

    Split out of main() so the tiering is testable without writing a fake key
    into a real repository file. `name` is the bare filename (it decides which
    tier applies); `rel` is only used for the message.
    """
    found: list[str] = []

    # Credential and home-path rules apply to EVERY scanned file, templates
    # included. This is the part that .env.example used to be exempt from.
    for label, pattern in KEY_PATTERNS:
        if pattern.search(text):
            found.append(f"{rel}: {label}")

    for hit in real_email_addresses(text)[:1]:
        found.append(
            f"{rel}: email address {hit[:28]!r} "
            f"(use an example.com address in committed files)"
        )

    for hit in home_path_hits(text)[:1]:
        found.append(
            f"{rel}: absolute home path {hit[:40]!r} "
            f"(leaks the OS account name of the machine that built it)"
        )

    match = US_ADDRESS_RE.search(text)
    if match:
        found.append(
            f"{rel}: US address {match.group(0).strip()[:24]!r} "
            f"(a postal code identifies a household, whosever it is)"
        )

    if name in SKIP_PERSONAL_FILES:
        return found        # its "personal" values are placeholders by design

    lowered = text.lower()
    for value in never:
        if value.lower() in lowered:
            found.append(f"{rel}: personal detail {value[:16]!r}")
    if name not in AUTHORSHIP_FILES:
        for value in authorship:
            index = lowered.find(value.lower())
            if index == -1:
                continue
            if is_project_url(lowered, index, value.lower()):
                continue        # a repo URL, not a profile link
            found.append(
                f"{rel}: author identity {value[:22]!r} "
                f"(allowed only in {', '.join(sorted(AUTHORSHIP_FILES))})"
            )
    return found


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
        if not path.exists() or path.name in NEVER_SCAN:
            continue
        rel = path.relative_to(ROOT).as_posix()
        suffix = path.suffix.lower()
        if suffix in BINARY_SUFFIXES:
            continue
        try:
            if suffix in TEXT_IN_A_WRAPPER:
                # Scan what is INSIDE it: a key pasted into a compressed file
                # is still a committed key.
                import gzip

                with gzip.open(path, "rt", encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            else:
                text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        problems.extend(scan_text(path.name, rel, text, never, authorship))

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
