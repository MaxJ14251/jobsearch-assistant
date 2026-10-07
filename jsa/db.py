"""SQLite access. Every module in the project goes through here."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import DB_PATH, SCHEMA_PATH


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Stored in UTC, shown in local time. Stamped at 6:54 pm in California, an
# application read "2026-09-22 01:54" and looked like it happened tomorrow.
def _parse(stamp: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def local_time(stamp: str | None) -> str:
    """'2026-09-21 18:54' for a stored UTC stamp; the input unchanged if unparseable."""
    when = _parse(stamp or "")
    return when.astimezone().strftime("%Y-%m-%d %H:%M") if when else (stamp or "")


def local_date(stamp: str | None):
    """The local calendar day of a stored UTC stamp, or None."""
    when = _parse(stamp or "")
    return when.astimezone().date() if when else None


# How long a connection waits for another writer before "database is locked".
# Brief overlaps (a dashboard save during discovery's commit) wait instead of
# failing.
BUSY_TIMEOUT_S = 10


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db(path: Path = DB_PATH, schema: Path = SCHEMA_PATH) -> Path:
    """Create or upgrade the database. Safe to run repeatedly."""
    upgrade(path, schema)
    return path


def upgrade(path: Path = DB_PATH, schema: Path = SCHEMA_PATH) -> list[str]:
    """Bring an existing tracker up to the current schema. Returns what changed.

    `jsa init` is not the only command that needs this. A tracker created
    before a new source kind existed rejects it with a CHECK-constraint error
    on the first discovery run, which reads as a broken feed:

        sqlite3.IntegrityError: CHECK constraint failed: kind IN (...)

    That is exactly what happened to the author's own tracker the day the
    nationwide source shipped. Anything that writes to the tracker calls this
    first, so the upgrade happens where the need arises rather than in a
    command people run once and forget.
    """
    path = Path(path)
    if path.exists():
        snapshot_before_rebuild(path, schema)
    con = connect(path)
    try:
        con.executescript(schema.read_text(encoding="utf-8"))
        applied = migrate(con, schema)
        rekeyed = rekey(con)
        if rekeyed:
            applied.append(f"re-keyed {rekeyed} posting(s) for duplicate folding")
        con.commit()
    finally:
        con.close()
    return applied


def rebuilds_pending(con: sqlite3.Connection, schema_sql: str | None = None) -> list[str]:
    """The whole-table rebuilds migrate() would run on this tracker. Reads only."""
    pending = []
    if _approvals_stale(con):
        pending.append("approvals")
    if _sources_stale(con, schema_sql):
        pending.append("sources")
    pending += _dangling_tables(con)
    return pending


def snapshot_before_rebuild(path: Path, schema: Path = SCHEMA_PATH) -> Path | None:
    """Copy the tracker before a migration rebuilds a table (ADR 0025).

    Those rebuilds' own docstrings record past damage to the author's
    tracker. If the copy can't be made, the upgrade does not run: a rebuild
    without a copy is the failure this exists to prevent.
    """
    import sys

    from . import backup
    from .config import ConfigError

    con = connect(path)
    try:
        pending = rebuilds_pending(con, schema.read_text(encoding="utf-8"))
    finally:
        con.close()
    if not pending:
        return None
    real = Path(path).resolve() == Path(DB_PATH).resolve()
    try:
        copy = backup.make(backup.default_root(path), label="pre-upgrade",
                           db_path=path,
                           output_dir=backup.OUTPUT_DIR if real else None,
                           profile_path=backup.PROFILE_PATH if real else None)
        backup.prune(backup.default_root(path))
    except Exception as exc:
        raise ConfigError(
            f"the tracker needs a rebuild ({', '.join(pending)}), and the copy "
            f"that must come first failed: {exc}. Nothing was changed.") from exc
    print(f"backed up the tracker before rebuilding {', '.join(pending)}: {copy}",
          file=sys.stderr)
    return copy


def rekey(con: sqlite3.Connection) -> int:
    """Recompute every stored dedup_key under the current rule. Returns how
    many changed; a second run returns 0.

    The key is stored when a posting is first seen, so a better rule does
    nothing for the postings already in the tracker until something rewrites
    them. n20 changed the rule after 1,500 postings were stored with the old
    one, which had folded eighteen different SpaceX jobs into one card.
    """
    from .scoring import dedup_key

    changed = 0
    rows = con.execute(
        "SELECT id, company_id, title, location, dedup_key FROM jobs").fetchall()
    for row in rows:
        key = dedup_key(row["company_id"], row["title"], row["location"])
        if key != row["dedup_key"]:
            con.execute("UPDATE jobs SET dedup_key = ? WHERE id = ?",
                        (key, row["id"]))
            changed += 1
    return changed


def migrate(con: sqlite3.Connection, schema: Path = SCHEMA_PATH) -> list[str]:
    """Add columns the schema declares but an existing database lacks.

    `CREATE TABLE IF NOT EXISTS` silently does nothing when the table already
    exists, so new columns never appear in a database created by an older
    version — and the failure shows up much later as a confusing "no such
    column". Views are dropped and recreated for the same reason: a view is
    frozen against the columns that existed when it was defined.

    Only additive changes. Anything destructive is deliberately out of scope.
    """
    import re

    text = schema.read_text(encoding="utf-8")
    applied: list[str] = []

    for table_match in re.finditer(
        r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\n\);", text, re.S
    ):
        table, body = table_match.group(1), table_match.group(2)
        existing = {
            row["name"]
            for row in con.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if not existing:
            continue  # table is brand new; the script already created it
        for line in body.splitlines():
            # Strip trailing comments BEFORE the comma, or the generated
            # `ADD COLUMN x TEXT, -- note` is invalid SQL and fails silently.
            line = line.split("--")[0].strip().rstrip(",").strip()
            if not line or line.startswith((
                "PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT",
            )):
                continue
            col = line.split()[0]
            if not col.isidentifier() or col in existing:
                continue
            # SQLite can't ALTER-ADD a column with a non-constant default,
            # so strip DEFAULT (...) expressions; rows get NULL instead.
            decl = re.sub(r"DEFAULT\s*\([^)]*\)", "", line).strip()
            try:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {decl}")
                applied.append(f"{table}.{col}")
            except sqlite3.OperationalError:
                pass

    applied += _rebuild_approvals_if_stale(con, text)
    applied += _rebuild_sources_if_stale(con, text)
    applied += _repair_dangling_references(con, text)

    # Views are checked every time, not only when a table changed:
    # `CREATE VIEW IF NOT EXISTS` leaves an old definition in place forever
    # (n20 added a column to v_new_matches and an existing tracker never got
    # it). A view whose stored SQL differs from the schema's is replaced, and
    # all of them in ONE transaction: dropping every view with autocommit made
    # the dashboard fail "no such table" while another process upgraded
    # (review R-02: 37,287 failed reads during 40 upgrades).
    _sync_views(con, text)
    con.executescript(text)
    return applied


_VIEW_RE = re.compile(r"CREATE VIEW IF NOT EXISTS\s+(\w+)\s+(AS\b.*?);[ \t]*$", re.S | re.M)


def _flat(sql: str) -> str:
    return " ".join((sql or "").split())


def _sync_views(con: sqlite3.Connection, text: str) -> None:
    wanted = {m.group(1): f"CREATE VIEW {m.group(1)} {m.group(2)}"
              for m in _VIEW_RE.finditer(text)}
    stored = {row["name"]: row["sql"] for row in con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='view'")}
    stale = [n for n in stored if _flat(stored[n]) != _flat(wanted.get(n, ""))]
    missing = [n for n in wanted if n not in stored]
    if not stale and not missing:
        return
    con.commit()
    con.execute("BEGIN IMMEDIATE")
    try:
        for name in stale:
            con.execute(f"DROP VIEW IF EXISTS {name}")
        for name in [*stale, *missing]:
            if name in wanted:
                con.execute(wanted[name])
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def _approvals_stale(con: sqlite3.Connection) -> str:
    """"unique", "check", or "" when approvals needs no rebuild."""
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='approvals'"
    ).fetchone()
    if not row:
        return ""
    sql = (row["sql"] or "")
    if "requested_at)" in sql.replace(" ", ""):
        return "unique"
    # A CHECK constraint cannot be altered either, and an old database would
    # otherwise reject 'superseded' with a confusing constraint error.
    return "check" if "superseded" not in sql else ""


_KIND_CHECK_RE = re.compile(r"CHECK\s*\(\s*kind\s+IN\s*\(([^)]*)\)", re.I)
_SOURCES_TABLE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS\s+sources\s*\(.*?\n\);", re.S)


def source_kinds(table_sql: str) -> set[str]:
    """The kinds a `sources` table's CHECK allows: {'greenhouse', 'lever', ...}."""
    match = _KIND_CHECK_RE.search(table_sql or "")
    return set(re.findall(r"'([^']+)'", match.group(1))) if match else set()


def _sources_stale(con: sqlite3.Connection, schema_sql: str | None = None) -> bool:
    """True when the live CHECK lacks any kind the schema lists.

    This once looked for one word ('themuse'), the newest kind at the time,
    so a tracker made before the next kind would never have been rebuilt and
    the first posting of that kind would fail its CHECK (plan 28)."""
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'"
    ).fetchone()
    if not row:
        return False
    if schema_sql is None:
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    table = _SOURCES_TABLE_RE.search(schema_sql)
    wanted = source_kinds(table.group(0)) if table else set()
    return bool(wanted - source_kinds(row["sql"] or ""))


def _dangling_tables(con: sqlite3.Connection) -> list[str]:
    """Tables whose REFERENCES name a table that is gone."""
    live = {r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    broken = []
    for row in con.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall():
        for target in re.findall(r'REFERENCES\s+"?([A-Za-z_][A-Za-z_0-9]*)"?',
                                 row["sql"] or ""):
            if target not in live and row["name"] not in broken:
                broken.append(row["name"])
    return broken


def _rebuild_approvals_if_stale(con: sqlite3.Connection, schema_sql: str) -> list[str]:
    """Drop a table-level UNIQUE that ALTER TABLE cannot remove.

    Early versions carried UNIQUE (subject_type, subject_id, requested_at) on
    approvals. Because requested_at has only second resolution, re-queueing a
    redraft within the same second failed — the exact reject-then-re-render
    path. SQLite cannot drop a table constraint, so the table is rebuilt.
    """
    stale = _approvals_stale(con)
    if not stale:
        return []
    reason = ("dropped stale UNIQUE" if stale == "unique"
              else "widened decision CHECK")
    import re as _re

    match = _re.search(
        r"CREATE TABLE IF NOT EXISTS\s+approvals\s*\(.*?\n\);", schema_sql, _re.S
    )
    if not match:
        # Without the new definition there is nothing to copy into: dropping
        # the old table here would lose every approval.
        return []

    cols = [r["name"] for r in con.execute("PRAGMA table_info(approvals)")]
    con.executescript(
        "PRAGMA foreign_keys=OFF;"
        # Without this, SQLite 3.25+ helpfully rewrites every REFERENCES to
        # this table in OTHER tables to say approvals_old -- and the copy is
        # about to be dropped. It cost the author's tracker a dangling
        # foreign key on jobs.source_id when sources was rebuilt this way.
        "PRAGMA legacy_alter_table=ON;"
        "ALTER TABLE approvals RENAME TO approvals_old;"
    )
    con.executescript(match.group(0))
    shared = ", ".join(
        c for c in cols
        if c in {r["name"] for r in con.execute("PRAGMA table_info(approvals)")}
    )
    con.execute(
        f"INSERT INTO approvals ({shared}) SELECT {shared} FROM approvals_old"
    )
    con.executescript("DROP TABLE approvals_old;"
                      "PRAGMA legacy_alter_table=OFF; PRAGMA foreign_keys=ON;")
    return [f"approvals(rebuilt: {reason})"]


def _repair_dangling_references(con: sqlite3.Connection, schema_sql: str) -> list[str]:
    """Rebuild any table whose REFERENCES point at a table that is gone.

    A rebuild done without `legacy_alter_table` left jobs.source_id pointing
    at "sources_old", which SQLite accepts until the first INSERT and then
    refuses with `no such table: main.sources_old`. The data is fine; the
    table definition is not. This puts the definition back from the schema
    and copies every row across.
    """
    import re as _re

    broken = _dangling_tables(con)
    if not broken:
        return []

    applied = []
    for name in broken:
        match = _re.search(
            rf"CREATE TABLE IF NOT EXISTS\s+{name}\s*\(.*?\n\);",
            schema_sql, _re.S)
        if not match:
            continue
        cols = [r["name"] for r in con.execute(f"PRAGMA table_info({name})")]
        con.executescript("PRAGMA foreign_keys=OFF;"
                          "PRAGMA legacy_alter_table=ON;"
                          f"ALTER TABLE {name} RENAME TO {name}_broken;")
        con.executescript(match.group(0))
        shared = ", ".join(
            c for c in cols
            if c in {r["name"] for r in con.execute(f"PRAGMA table_info({name})")})
        con.execute(f"INSERT INTO {name} ({shared}) SELECT {shared} FROM {name}_broken")
        con.executescript(f"DROP TABLE {name}_broken;"
                          "PRAGMA legacy_alter_table=OFF; PRAGMA foreign_keys=ON;")
        applied.append(f"{name}(repaired: reference to a table that was gone)")
    return applied


def _rebuild_sources_if_stale(con: sqlite3.Connection, schema_sql: str) -> list[str]:
    """Widen sources.kind, which a CHECK constraint pins and ALTER cannot move.

    A database created before the nationwide source existed rejects it with a
    constraint error on the first discovery run, which reads as a bug in the
    feed rather than an out-of-date table.
    """
    if not _sources_stale(con, schema_sql):
        return []

    cols = [r["name"] for r in con.execute("PRAGMA table_info(sources)")]
    match = _SOURCES_TABLE_RE.search(schema_sql)
    if not match:
        return []
    con.executescript("PRAGMA foreign_keys=OFF;"
                      "PRAGMA legacy_alter_table=ON;"
                      "ALTER TABLE sources RENAME TO sources_old;")
    con.executescript(match.group(0))
    shared = ", ".join(
        c for c in cols
        if c in {r["name"] for r in con.execute("PRAGMA table_info(sources)")})
    con.execute(f"INSERT INTO sources ({shared}) SELECT {shared} FROM sources_old")
    con.executescript("DROP TABLE sources_old;"
                      "PRAGMA legacy_alter_table=OFF; PRAGMA foreign_keys=ON;")
    return ["sources(rebuilt: widened kind CHECK)"]


# --- upserts ---------------------------------------------------------------


def upsert_company(
    con: sqlite3.Connection,
    *,
    name: str,
    slug: str,
    careers_url: str | None = None,
    priority: int | None = None,
) -> int:
    """`priority` changes only when passed: an aggregator posting that names
    an employer from companies.yaml must not reset its hand-set priority."""
    row = con.execute("SELECT id FROM companies WHERE slug = ?", (slug,)).fetchone()
    if row:
        con.execute(
            "UPDATE companies SET name = ?, priority = COALESCE(?, priority), "
            "careers_url = COALESCE(?, careers_url), updated_at = ? WHERE id = ?",
            (name, priority, careers_url, utcnow(), row["id"]),
        )
        return int(row["id"])
    cur = con.execute(
        "INSERT INTO companies (name, slug, careers_url, priority) VALUES (?,?,?,?)",
        (name, slug, careers_url, 3 if priority is None else priority),
    )
    return int(cur.lastrowid)


def upsert_source(
    con: sqlite3.Connection,
    *,
    name: str,
    kind: str,
    url: str,
    company_id: int | None,
    enabled: bool = True,
) -> int:
    row = con.execute("SELECT id FROM sources WHERE name = ?", (name,)).fetchone()
    if row:
        con.execute(
            "UPDATE sources SET kind = ?, url = ?, company_id = ?, enabled = ? WHERE id = ?",
            (kind, url, company_id, int(enabled), row["id"]),
        )
        return int(row["id"])
    cur = con.execute(
        "INSERT INTO sources (name, kind, url, company_id, enabled) VALUES (?,?,?,?,?)",
        (name, kind, url, company_id, int(enabled)),
    )
    return int(cur.lastrowid)


def mark_source_polled(
    con: sqlite3.Connection, source_id: int, status: str
) -> None:
    con.execute(
        "UPDATE sources SET last_polled_at = ?, last_status = ? WHERE id = ?",
        (utcnow(), status, source_id),
    )


def _norm(text: Any) -> str:
    """Lowercase, collapse whitespace, drop punctuation. For comparing titles."""
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower()).split())


def _first_city(location: Any) -> str:
    """The first place named, normalised. "Boise, ID; Flexible / Remote" -> "boise id"."""
    first = str(location or "").split(";")[0].split("(")[0]
    return _norm(first)


def find_duplicate(con: sqlite3.Connection, job: dict[str, Any]) -> int | None:
    """The id of the same posting stored from a DIFFERENT source, or None.

    An aggregator lists jobs this tracker already has from the employer's own
    board, under its own posting id and its own URL, so the (source, external
    id) key cannot see it. Same company, same title, same first location is
    the rule; the company is already resolved to one row by slug before this
    runs, so two employers with similar names cannot collide here.

    It deliberately does NOT merge on company and title alone: the same title
    in two cities is two jobs, and `v_new_matches` groups those by dedup_key
    for display. A wrong merge hides a job the operator would have seen,
    which is worse than showing one posting twice -- so when the location is
    missing on either side, this reports no duplicate.
    """
    title, city = _norm(job.get("title")), _first_city(job.get("location"))
    if not title or not city or not job.get("company_id"):
        return None
    rows = con.execute(
        "SELECT id, title, location, source_id FROM jobs "
        "WHERE company_id = ? AND closed_at IS NULL AND archived_at IS NULL",
        (job["company_id"],)).fetchall()
    for row in rows:
        if row["source_id"] == job.get("source_id"):
            continue                      # same feed: the normal key handles it
        if _norm(row["title"]) == title and _first_city(row["location"]) == city:
            return int(row["id"])
    return None


def upsert_job(con: sqlite3.Connection, job: dict[str, Any]) -> tuple[int, bool]:
    """Insert or update a listing. Returns (job_id, is_new)."""
    existing = con.execute(
        "SELECT id, description_hash, description_origin FROM jobs "
        "WHERE source_id IS ? AND external_id IS ?",
        (job.get("source_id"), job.get("external_id")),
    ).fetchone()
    if existing is None and find_duplicate(con, job) is not None:
        # Already here from the employer's own board. That row is the better
        # one: it came from the employer, and its URL is where you apply.
        return int(find_duplicate(con, job)), False

    payload = {
        "company_id": job["company_id"],
        "source_id": job.get("source_id"),
        "external_id": job.get("external_id"),
        "title": job["title"],
        "department": job.get("department"),
        "location": job.get("location"),
        "remote": job.get("remote") or "unknown",
        "employment_type": job.get("employment_type") or "unknown",
        "seniority": job.get("seniority") or "unknown",
        "salary_min": job.get("salary_min"),
        "salary_max": job.get("salary_max"),
        "salary_period": job.get("salary_period"),
        "salary_text": job.get("salary_text"),
        "salary_currency": job.get("salary_currency"),
        "salary_source": job.get("salary_source"),
        "url": job["url"],
        "description": job.get("description"),
        "description_hash": job.get("description_hash"),
        "posted_at": job.get("posted_at"),
        "match_score": job.get("match_score"),
        "match_reasons": json.dumps(job.get("match_reasons") or []),
        "dedup_key": job.get("dedup_key"),
        "track": job.get("track") or "engineering",
        "description_origin": job.get("description_origin"),
    }

    if (existing and existing["description_origin"] == "pasted"
            and job.get("description_origin") != "pasted"):
        # The operator pasted the real posting over a stub (`jsa fill`). The
        # feed's version is the stub, so it must not overwrite it, nor the pay,
        # score and track read from it. The listing is still live, though.
        con.execute("UPDATE jobs SET closed_at = NULL WHERE id = ?",
                    (existing["id"],))
        return int(existing["id"]), False

    if existing:
        cols = ", ".join(f"{k} = :{k}" for k in payload)
        con.execute(
            f"UPDATE jobs SET {cols}, closed_at = NULL WHERE id = :id",
            {**payload, "id": existing["id"]},
        )
        return int(existing["id"]), False

    cols = ", ".join(payload)
    binds = ", ".join(f":{k}" for k in payload)
    cur = con.execute(f"INSERT INTO jobs ({cols}) VALUES ({binds})", payload)
    return int(cur.lastrowid), True


def close_missing_jobs(
    con: sqlite3.Connection, source_id: int, seen_external_ids: Iterable[str]
) -> int:
    """Mark listings that vanished from a feed as closed."""
    seen = list(seen_external_ids)
    placeholders = ",".join("?" * len(seen)) or "NULL"
    cur = con.execute(
        f"UPDATE jobs SET closed_at = ? "
        f"WHERE source_id = ? AND closed_at IS NULL "
        f"AND external_id NOT IN ({placeholders})",
        (utcnow(), source_id, *seen),
    )
    return cur.rowcount
