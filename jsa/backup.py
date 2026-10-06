"""Copies of what can't be recreated: the tracker, drafted documents, profile.

ADR 0025. The tracker runs in WAL mode, so a file copy can miss recent
writes; the database is copied with SQLite's online backup instead, which is
safe while the dashboard is running. `.env` is never copied: keys can be
reissued, and a stray copy of a key is a leak.

A copy is a folder `<root>/<YYYY-MM-DD_HHMMSS>-<label>/` holding
`jobsearch.db`, `output/`, `master_profile.yaml` and `manifest.json` (time,
label, row counts per table, every file with its sha256).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DB_PATH, OUTPUT_DIR, PROFILE_PATH, ConfigError

LABELS = ("manual", "pre-upgrade", "pre-restore", "daily")
# Copies kept per label by prune(): manual ones are asked for, the others are
# taken automatically and only need to cover the last few risky moments.
KEEP = {"manual": 10, "pre-upgrade": 3, "pre-restore": 3,
        # `jsa daily` (plan 18): a week of them, counted apart from manual ones.
        "daily": 7}
# Only folders this tool made: a date, a time and one of its labels.
_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{6})-(%s)(?:-\d+)?$" % "|".join(LABELS))
DB_NAME = "jobsearch.db"
MANIFEST = "manifest.json"
# jsa doctor mentions a copy older than this.
BACKUP_STALE_DAYS = 7


class BackupError(ConfigError):
    """A copy could not be made or does not verify."""


def default_root(db_path: Path | None = None) -> Path:
    """Beside the tracker, so under Docker it lands on the /data volume."""
    return Path(db_path or DB_PATH).parent / "backups"


# Paths are looked up when called, not bound as default arguments, so the
# configured tracker (JSA_DB) is always the one meant. `...` means "the
# configured one"; None means "not this time".
def _or(value, default):
    return default if value is ... else value


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _counts(con: sqlite3.Connection) -> dict[str, int]:
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}


def make(root: Path | None = None, *, label: str = "manual",
         db_path: Path | None = None, output_dir: Any = ...,
         profile_path: Any = ...) -> Path:
    """Make one copy and return its folder. Raises BackupError."""
    if label not in LABELS:
        raise ValueError(f"label must be one of {LABELS}")
    db_path = Path(db_path or DB_PATH)
    output_dir = _or(output_dir, OUTPUT_DIR)
    profile_path = _or(profile_path, PROFILE_PATH)
    if not db_path.exists():
        raise BackupError(f"no tracker at {db_path}")
    root = Path(root) if root else default_root(db_path)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest, n = root / f"{stamp}-{label}", 2
    while dest.exists():
        dest, n = root / f"{stamp}-{label}-{n}", n + 1
    dest.mkdir(parents=True)
    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(dest / DB_NAME)
        try:
            src.backup(dst)
            counts = _counts(dst)
        finally:
            dst.close()
            src.close()
        files = [DB_NAME]
        if output_dir is not None and Path(output_dir).is_dir():
            shutil.copytree(output_dir, dest / "output")
            files += [p.relative_to(dest).as_posix()
                      for p in sorted((dest / "output").rglob("*")) if p.is_file()]
        if profile_path is not None and Path(profile_path).is_file():
            shutil.copy2(profile_path, dest / Path(profile_path).name)
            files.append(Path(profile_path).name)
        manifest = {
            "made_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "label": label,
            "source": str(db_path),
            "output_dir": str(output_dir) if output_dir else None,
            "tables": counts,
            "files": {f: _sha256(dest / f) for f in files},
        }
        (dest / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except Exception as exc:
        # A half-written copy must not count as a copy: it would be "fresh" to
        # doctor and push a good one out of prune's keep-N (review R-08).
        shutil.rmtree(dest, ignore_errors=True)
        raise BackupError(f"the copy failed and was removed: {exc}") from exc
    return dest


@dataclass
class Check:
    path: Path
    problems: list[str] = field(default_factory=list)
    tables: int = 0
    rows: int = 0
    files: int = 0
    submitted_checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


def _submitted(con: sqlite3.Connection) -> list[tuple[str, str]]:
    """(stored path, sha256) of every file recorded as sent."""
    try:
        rows = con.execute(
            "SELECT d.path, s.sha256 FROM submitted_documents s "
            "JOIN documents d ON d.id = s.document_id "
            "WHERE s.sha256 IS NOT NULL").fetchall()
    except sqlite3.Error:
        return []
    return [(r[0], r[1]) for r in rows]


def _in_copy(stored: str, copy: Path, output_dir: str | None) -> Path | None:
    """Where a file under the original output/ sits inside a copy."""
    if not output_dir:
        return None
    try:
        rel = Path(stored).resolve().relative_to(Path(output_dir).resolve())
    except ValueError:
        return None
    return copy / "output" / rel


def verify(path: Path) -> Check:
    """integrity_check, row counts and every file's hash against the
    manifest, and each sent document in the copy against its recorded hash.
    Reports; never fixes."""
    path = Path(path)
    check = Check(path)
    try:
        manifest = json.loads((path / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        check.problems.append(f"no readable {MANIFEST}: {exc}")
        return check
    db_file = path / DB_NAME
    if not db_file.exists():
        check.problems.append(f"{DB_NAME} is missing")
        return check
    con = sqlite3.connect(f"file:{db_file.as_posix()}?mode=ro", uri=True)
    try:
        result = con.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            check.problems.append(f"integrity_check: {result}")
        counts = _counts(con)
        sent = _submitted(con)
    finally:
        con.close()
    if counts != manifest.get("tables"):
        changed = sorted(t for t in set(counts) | set(manifest.get("tables") or {})
                         if counts.get(t) != (manifest.get("tables") or {}).get(t))
        check.problems.append(f"row counts differ from the manifest: {', '.join(changed)}")
    check.tables, check.rows = len(counts), sum(counts.values())
    for name, digest in (manifest.get("files") or {}).items():
        f = path / name
        if not f.is_file():
            check.problems.append(f"{name} is missing")
        elif _sha256(f) != digest:
            check.problems.append(f"{name} changed since the copy was made")
        else:
            check.files += 1
    for stored, digest in sent:
        f = _in_copy(stored, path, manifest.get("output_dir"))
        if f is not None and f.is_file():
            check.submitted_checked += 1
            if _sha256(f) != digest:
                check.problems.append(
                    f"{f.relative_to(path).as_posix()} differs from the file that was sent")
    return check


def check_live(db_path: Path | None = None) -> Check:
    """The live tracker's sent documents against their recorded hashes.
    Read-only: the check nothing else does."""
    db_path = Path(db_path or DB_PATH)
    check = Check(db_path)
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        sent = _submitted(con)
    finally:
        con.close()
    from .config import ROOT
    for stored, digest in sent:
        f = Path(stored)
        f = f if f.is_absolute() else ROOT / f
        check.submitted_checked += 1
        if not f.is_file():
            check.problems.append(f"{f.name}: the sent file is gone ({stored})")
        elif _sha256(f) != digest:
            check.problems.append(f"{f.name}: changed since it was sent ({stored})")
    return check


@dataclass
class Copy:
    path: Path
    label: str
    made: datetime
    bytes: int


def listing(root: Path | None = None) -> list[Copy]:
    """This tool's copies under `root`, newest first. Anything else is ignored."""
    root = Path(root) if root else default_root()
    if not root.is_dir():
        return []
    found = []
    for d in root.iterdir():
        m = _NAME.match(d.name)
        # No manifest means it never finished (or isn't ours): not a copy.
        if d.is_dir() and m and (d / MANIFEST).is_file():
            size = sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
            found.append(Copy(d, m[2], datetime.strptime(m[1], "%Y-%m-%d_%H%M%S"), size))
    return sorted(found, key=lambda c: (c.made, c.path.name), reverse=True)


def prune(root: Path | None = None, keep: dict[str, int] | None = None) -> list[Path]:
    """Remove this tool's oldest copies beyond `keep` per label. Only folders
    matching its own name pattern are ever touched."""
    keep = {**KEEP, **(keep or {})}
    removed = []
    by_label: dict[str, list[Copy]] = {}
    for c in listing(root):
        by_label.setdefault(c.label, []).append(c)
    for label, copies in by_label.items():
        for old in copies[keep.get(label, 3):]:
            shutil.rmtree(old.path)
            removed.append(old.path)
    return removed


def newest_age_days(root: Path | None = None) -> float | None:
    copies = listing(root)
    if not copies:
        return None
    return (datetime.now() - copies[0].made).total_seconds() / 86400


def restore(source: Path, *, db_path: Path | None = None, everything: bool = False,
            output_dir: Any = ..., profile_path: Any = ...) -> Path | None:
    """Put a verified copy back, after copying the current state. Returns the
    pre-restore copy, or None when there was no tracker to copy (the case a
    restore is most often for). The caller has already confirmed.

    With `everything`, output/ is copied in full beside the current one and
    swapped in before the tracker is touched, so a file held open (Windows)
    stops the restore before anything changes rather than halfway through
    (review R-04)."""
    source = Path(source)
    db_path = Path(db_path or DB_PATH)
    output_dir = Path(_or(output_dir, OUTPUT_DIR))
    profile_path = Path(_or(profile_path, PROFILE_PATH))
    check = verify(source)
    if not check.ok:
        raise BackupError("that copy does not verify: " + "; ".join(check.problems))
    wal = Path(f"{db_path}-wal")
    if wal.exists() and wal.stat().st_size > 0:
        raise BackupError(
            "the tracker is in use (its -wal file holds writes). Stop the "
            "dashboard and anything else using the tracker, then run this again.")
    safety = None
    if db_path.exists():
        safety = make(label="pre-restore", db_path=db_path,
                      output_dir=output_dir if everything else None,
                      profile_path=profile_path if everything else None,
                      root=default_root(db_path))
    kept = f" What was there before is in {safety}." if safety else ""
    staged = aside = None
    try:
        if everything and (source / "output").is_dir():
            stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            staged = output_dir.with_name(f".{output_dir.name}.restoring-{stamp}")
            shutil.copytree(source / "output", staged)
            if output_dir.exists():
                aside = output_dir.with_name(f".{output_dir.name}.replaced-{stamp}")
                os.replace(output_dir, aside)
            os.replace(staged, output_dir)
            staged = None
    except OSError as exc:
        if staged is not None:
            shutil.rmtree(staged, ignore_errors=True)
        if aside is not None and not output_dir.exists():
            os.replace(aside, output_dir)
        raise BackupError(f"nothing was restored: {output_dir} could not be "
                          f"replaced ({exc}). Close any file open from it and "
                          f"run this again.{kept}") from exc
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(source / DB_NAME)
        dst = sqlite3.connect(db_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        if everything:
            profile_copy = source / Path(profile_path).name
            if profile_copy.is_file():
                shutil.copy2(profile_copy, profile_path)
    except (OSError, sqlite3.Error) as exc:
        raise BackupError(f"the restore stopped part way: {exc}.{kept}") from exc
    if aside is not None:
        shutil.rmtree(aside, ignore_errors=True)
    return safety


def manifest(path: Path) -> dict[str, Any]:
    return json.loads((Path(path) / MANIFEST).read_text(encoding="utf-8"))
