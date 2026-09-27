"""Fail if a secret or personal detail is anywhere in git HISTORY.

tools/scan_secrets.py checks the working tree. That is the wrong scope for the
question "is this safe to publish": publishing a repository publishes every
commit, and a file deleted in commit 12 is still readable in commit 11 by
anyone with the URL. Deleting a leak is not removing it.

This tool runs the same rules over:

    every blob reachable from every ref   (`git rev-list --objects --all`)
    every commit message and author line  (a message carries an address as
                                           easily as a file does)
    every branch and tag name

Scope is deliberately `--all` and not the reflog: `git push` transmits what is
reachable from refs, and that is exactly what a reader of the published
repository can fetch. Unreachable objects in a local .git are not published.

    python tools/scan_history.py [--quiet]

Exit 0 clean, 1 findings, 2 the scanner could not run (never "clean").

The personal tier is read from profile/master_profile.yaml, which is
gitignored. On a fresh clone or in CI there is no profile, so that tier is
empty and the tool says so rather than implying it checked.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.scan_secrets import (  # noqa: E402
    NEVER_SCAN,
    ScannerUnavailable,
    personal_values,
    real_email_addresses,
    BINARY_SUFFIXES,
    TEXT_IN_A_WRAPPER,
    scan_text,
)

ROOT = Path(__file__).resolve().parent.parent

# Paths that must never appear in a commit at all, whatever they contain.
# Content rules cannot be relied on here: a SQLite tracker or a .docx is mostly
# bytes, and the one string that matters may not survive decoding. The path
# itself is the finding.
#
# Each entry is (label, predicate over the posix path).
FORBIDDEN_PATHS = [
    ("the local environment file, which holds the API key",
     lambda p: p == ".env" or p.endswith("/.env")),
    ("the operator's filled-in profile",
     lambda p: p.endswith("profile/master_profile.yaml")),
    ("a tracker database",
     lambda p: p.endswith((".db", ".sqlite", ".sqlite3"))),
    ("a coverage database, which embeds absolute paths",
     lambda p: Path(p).name == ".coverage" or p.endswith("/.coverage")),
    ("a generated document",
     lambda p: p.endswith((".docx", ".pdf")) or p.startswith("documents/")),
    ("generated output",
     lambda p: p.startswith("output/")),
    ("agent session state",
     lambda p: p.startswith(".claude/")),
]


def git(*args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True, errors="replace")
    if proc.returncode != 0:
        raise ScannerUnavailable(
            f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def objects() -> list[tuple[str, str]]:
    """(sha, path) for every named object reachable from any ref."""
    out = git("rev-list", "--objects", "--all")
    found = []
    for line in out.splitlines():
        sha, _, path = line.partition(" ")
        if path.strip():
            found.append((sha, path.strip()))
    return found


def blob_types(shas: list[str]) -> dict[str, tuple[str, int]]:
    """sha -> (type, size), in one git call rather than one per object."""
    if not shas:
        return {}
    proc = subprocess.run(
        ["git", "cat-file", "--batch-check"], cwd=ROOT,
        input="\n".join(shas), capture_output=True, text=True)
    info: dict[str, tuple[str, int]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] != "missing":
            info[parts[0]] = (parts[1], int(parts[2]))
    return info


def blob_text(sha: str) -> str:
    """A blob's bytes decoded loosely. Binary is scanned too, on purpose.

    A SQLite tracker is binary, and the email address inside it is still plain
    ASCII. Skipping binary would skip the single worst thing that could be in
    this history.
    """
    proc = subprocess.run(["git", "cat-file", "blob", sha], cwd=ROOT,
                          capture_output=True)
    return proc.stdout.decode("utf-8", errors="ignore")


def commits_touching(sha: str) -> list[str]:
    """Which commits introduced or carry this blob. Only called on a hit."""
    proc = subprocess.run(
        ["git", "log", "--all", "--format=%h %ad %s", "--date=short",
         "--find-object", sha], cwd=ROOT, capture_output=True, text=True,
        errors="replace")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def scan_blobs(never: list[str], authorship: list[str]) -> list[str]:
    found: list[str] = []
    named = objects()
    info = blob_types([sha for sha, _ in named])
    seen: set[str] = set()
    for sha, path in named:
        kind, _size = info.get(sha, ("", 0))
        if kind != "blob":
            continue                      # trees are named too
        name = Path(path).name

        for label, matches in FORBIDDEN_PATHS:
            if matches(path):
                found.append(f"{path} @ {sha[:8]}: {label} was committed")

        if sha in seen:
            continue                      # same blob under two paths
        seen.add(sha)
        text = blob_text(sha)
        if not text:
            continue
        suffix = Path(path).suffix.lower()
        if suffix in BINARY_SUFFIXES:
            continue                      # bytes, not text: see scan_secrets
        if suffix in TEXT_IN_A_WRAPPER:
            import gzip
            import io

            try:
                raw = subprocess.run(["git", "cat-file", "blob", sha],
                                     capture_output=True, cwd=ROOT).stdout
                text = gzip.GzipFile(fileobj=io.BytesIO(raw)).read().decode(
                    "utf-8", "ignore")
            except Exception:  # noqa: BLE001 - unreadable is not a finding
                continue
        # NEVER_SCAN exists because those files hold real values by design and
        # are gitignored. In history their presence is the finding, and their
        # contents must be scanned, not exempted.
        where = f"{path} @ {sha[:8]}"
        found.extend(scan_text(name, where, text, never, authorship))
        if name in NEVER_SCAN and not any(
                m(path) for _lbl, m in FORBIDDEN_PATHS):
            found.append(f"{where}: a file that holds real values by design")
    return found


def scan_identities() -> list[str]:
    """Author and committer identities, reported once each.

    Every commit carries one, so scanning them per-commit turns a single fact
    -- "this history is signed with a personal address" -- into one line per
    commit. The name is meant to be there; `git config user.name` is what it
    is for. The address is the finding, and only when somebody could write to
    it: GitHub's users.noreply.github.com address exists precisely so that a
    public history need not carry a private mailbox.
    """
    out = git("log", "--all", "--format=%an <%ae>%x00%cn <%ce>")
    seen: dict[str, int] = {}
    total = 0
    for line in out.splitlines():
        if not line.strip():
            continue
        total += 1
        # Author and committer are usually the same person; counting both
        # would report an identity on twice as many commits as there are.
        for identity in {part.strip() for part in line.split("\x00")
                         if part.strip()}:
            seen[identity] = seen.get(identity, 0) + 1
    found = []
    for identity, count in sorted(seen.items()):
        address = identity.rsplit("<", 1)[-1].rstrip(">")
        if not real_email_addresses(address):
            continue
        found.append(
            f"commit identity {identity!r} on {count} of {total} commits: a "
            f"mailbox that can be written to. Publishing the repository "
            f"publishes it, and only rewriting every commit removes it")
    return found


def scan_messages(never: list[str], authorship: list[str]) -> list[str]:
    """Commit messages, authors and committers, one commit at a time."""
    # The separators are git's own %x00 escape, not real NUL bytes: a NUL in a
    # subprocess argument is a ValueError on Windows before git ever runs.
    out = git("log", "--all",
              "--format=%h%x00%an <%ae>%x00%cn <%ce>%x00%B%x00---commit---%x00")
    found: list[str] = []
    for record in out.split("\x00---commit---\x00"):
        record = record.strip("\n")
        if not record.strip():
            continue
        parts = record.split("\x00")
        if len(parts) < 4:
            continue
        short, body = parts[0].strip(), parts[3]
        # Only the message body. The author and committer trailers are one
        # fact about the whole history, not a fact about each commit, and
        # scan_identities reports them once.
        found.extend(scan_text("COMMIT_MESSAGE", f"commit {short} message",
                               body, never, authorship))
    return found


def scan_refs(never: list[str], authorship: list[str]) -> list[str]:
    names = git("for-each-ref", "--format=%(refname)").splitlines()
    text = "\n".join(names)
    return scan_text("REF_NAMES", "ref names", text, never, authorship)


ALLOWLIST = ROOT / "tools" / "history_allowlist.txt"


def accepted_patterns() -> list[re.Pattern[str]]:
    """Findings the operator has already seen and decided about.

    A working-tree scanner can say "remove it". This one often cannot: the
    commit is already written. Without somewhere to record a decision, the only
    way to get a green build is to weaken a rule, and then the rule stops
    finding the next thing. The allowlist keeps the finding visible -- it is
    still printed, every run -- while letting a NEW one fail.
    """
    if not ALLOWLIST.exists():
        return []
    patterns = []
    for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(re.compile(line))
    return patterns


def split_accepted(problems: list[str]) -> tuple[list[str], list[str]]:
    patterns = accepted_patterns()
    blocking, accepted = [], []
    for problem in problems:
        if any(p.search(problem) for p in patterns):
            accepted.append(problem)
        else:
            blocking.append(problem)
    return blocking, accepted


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv
    try:
        never, authorship = personal_values()
        problems = scan_blobs(never, authorship)
        problems += scan_messages(never, authorship)
        notices = scan_identities()
        problems += scan_refs(never, authorship)
    except ScannerUnavailable as exc:
        print(f"SCANNER UNAVAILABLE: {exc}", file=sys.stderr)
        print("Refusing to report 'clean' when the check could not run.",
              file=sys.stderr)
        return 2

    if not quiet:
        tiers = "keys, emails, home paths"
        if never or authorship:
            tiers += ", and this machine's profile values"
        else:
            tiers += " (no profile/master_profile.yaml here, so the "
            tiers += "operator-specific tier is empty)"
        print(f"scanned every blob, commit message and ref for: {tiers}")

    # Not a leak, and not something a rule can settle. Every commit in every
    # git repository carries an author; whether yours should carry a mailbox
    # strangers can write to is a choice about your own name, so this prints
    # every run and never fails the build. Deciding it is a step on the
    # publishing checklist in CONTRIBUTING.md.
    for notice in notices:
        print("")
        print(f"NOTE  {notice}")

    blocking, accepted = split_accepted(sorted(set(problems)))

    if accepted and not quiet:
        print(f"\n{len(accepted)} finding(s) already reported and accepted "
              f"(see {ALLOWLIST.name}):\n")
        for problem in accepted:
            print(f"  {problem}")

    if blocking:
        print("\nBLOCKED — secrets or personal data in git history:\n",
              file=sys.stderr)
        for problem in blocking:
            print(f"  {problem}", file=sys.stderr)
            sha = problem.split("@")[-1].split(":")[0].strip()
            if len(sha) == 8:
                for line in commits_touching(sha)[:4]:
                    print(f"      in commit {line}", file=sys.stderr)
        print("\nHistory cannot be cleaned by deleting the file: the commit is "
              "already written. Take this to the repository owner before "
              "anything is made public.", file=sys.stderr)
        return 1
    print("\nclean: nothing in any commit beyond what has been reported")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
