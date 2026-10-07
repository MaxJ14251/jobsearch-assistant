"""Backups of the tracker (Plan 12, ADR 0025). Temp trackers only."""

import io
import os
import json
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from jsa import backup, db
from jsa.config import ROOT, SCHEMA_PATH


def tracker(folder: Path) -> Path:
    path = folder / "jobsearch.db"
    db.init_db(path)
    con = db.connect(path)
    con.execute("INSERT INTO companies (id,name,slug) VALUES (1,'Acme','acme')")
    con.execute("INSERT INTO jobs (id,company_id,title,url) "
                "VALUES (5,1,'Support Engineer','https://acme.test/5')")
    con.commit()
    con.close()
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = tracker(self.tmp)
        self.out = self.tmp / "output"
        (self.out / "acme" / "support-engineer").mkdir(parents=True)
        self.resume = self.out / "acme" / "support-engineer" / "resume.docx"
        self.resume.write_bytes(b"PK fictional resume bytes")
        self.profile = self.tmp / "master_profile.yaml"
        self.profile.write_text("identity: {full_name: Pat Example}\n", encoding="utf-8")
        (self.tmp / ".env").write_text("NVIDIA_API_KEY=not-a-real-key\n", encoding="utf-8")
        self.root = self.tmp / "backups"

    def make(self, label="manual"):
        return backup.make(self.root, label=label, db_path=self.db,
                           output_dir=self.out, profile_path=self.profile)

    def mark_sent(self):
        """A submitted resume with its recorded hash, as mark_applied writes it."""
        from jsa import approvals
        con = db.connect(self.db)
        con.execute("INSERT INTO documents (id,job_id,kind,path,version) "
                    "VALUES (1,5,'resume',?,1)", (str(self.resume),))
        app, _new = approvals.save_application(con, 5)
        con.execute("INSERT INTO submitted_documents (application_id,kind,document_id,"
                    "version,approved,sha256,submitted_at) VALUES (?,?,?,?,?,?,?)",
                    (app, "resume", 1, 1, 1, backup._sha256(self.resume), db.utcnow()))
        con.commit()
        con.close()


class TestMake(Base):
    def test_a_copy_has_everything_and_verifies(self):
        path = self.make()
        self.assertTrue((path / "jobsearch.db").exists())
        self.assertTrue((path / "output/acme/support-engineer/resume.docx").exists())
        self.assertTrue((path / "master_profile.yaml").exists())
        check = backup.verify(path)
        self.assertTrue(check.ok, check.problems)
        manifest = backup.manifest(path)
        self.assertEqual(manifest["tables"]["jobs"], 1)
        self.assertEqual(manifest["label"], "manual")

    def test_env_is_never_copied(self):
        path = self.make()
        self.assertEqual([p for p in path.rglob("*") if p.name == ".env"], [])
        self.assertNotIn("not-a-real-key", "".join(
            p.read_text(errors="ignore") for p in path.rglob("*") if p.is_file()))

    def test_a_copy_taken_mid_write_has_every_committed_row(self):
        writer = sqlite3.connect(self.db)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO companies (id,name,slug) VALUES (2,'Beta','beta')")
        writer.commit()                      # committed: in the -wal file
        writer.execute("INSERT INTO companies (id,name,slug) VALUES (3,'Gamma','gamma')")
        try:                                 # not committed while the copy runs
            path = self.make()
        finally:
            writer.rollback()
            writer.close()
        copy = sqlite3.connect(path / "jobsearch.db")
        names = {r[0] for r in copy.execute("SELECT name FROM companies")}
        copy.close()
        self.assertEqual(names, {"Acme", "Beta"})
        self.assertTrue(backup.verify(path).ok)

    def test_a_tampered_file_is_reported_not_fixed(self):
        path = self.make()
        (path / "output/acme/support-engineer/resume.docx").write_bytes(b"changed")
        check = backup.verify(path)
        self.assertFalse(check.ok)
        self.assertTrue(any("changed since the copy" in p for p in check.problems))

    def test_a_sent_file_is_checked_against_its_recorded_hash(self):
        self.mark_sent()
        path = self.make()
        self.assertEqual(backup.verify(path).submitted_checked, 1)
        # The live file drifts after sending; the live check says so.
        self.assertTrue(backup.check_live(self.db).ok)
        self.resume.write_bytes(b"edited after it was sent")
        live = backup.check_live(self.db)
        self.assertFalse(live.ok)
        self.assertIn("changed since it was sent", live.problems[0])

    def test_row_counts_must_match(self):
        path = self.make()
        m = backup.manifest(path)
        m["tables"]["jobs"] = 99
        (path / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
        self.assertIn("row counts differ", " ".join(backup.verify(path).problems))


class TestPrune(Base):
    def test_only_its_own_oldest_folders_go(self):
        made = []
        for n in range(4):
            folder = self.root / f"2026-01-0{n + 1}_120000-manual"
            folder.mkdir(parents=True)
            (folder / backup.MANIFEST).write_text("{}", encoding="utf-8")
            made.append(folder)
        foreign = self.root / "my-notes"
        foreign.mkdir()
        (self.root / "readme.txt").write_text("keep me", encoding="utf-8")
        removed = backup.prune(self.root, {"manual": 2})
        self.assertEqual(sorted(removed), made[:2])
        self.assertTrue(foreign.exists())
        self.assertTrue((self.root / "readme.txt").exists())
        self.assertTrue(made[3].exists())


class TestPreUpgradeSnapshot(unittest.TestCase):
    def old_tracker(self):
        """A tracker whose sources table predates the nationwide kind."""
        folder = Path(tempfile.mkdtemp())
        path = folder / "old.db"
        schema = SCHEMA_PATH.read_text(encoding="utf-8").replace("'themuse'", "'workday'")
        con = sqlite3.connect(path)
        con.executescript(schema)
        con.close()
        return folder, path

    def test_a_rebuild_is_preceded_by_a_copy(self):
        folder, path = self.old_tracker()
        con = db.connect(path)
        self.assertEqual(db.rebuilds_pending(con), ["sources"])
        con.close()
        with redirect_stderr(io.StringIO()) as err:
            db.upgrade(path)
        copies = backup.listing(folder / "backups")
        self.assertEqual([c.label for c in copies], ["pre-upgrade"])
        self.assertIn("backed up the tracker before rebuilding sources", err.getvalue())
        con = db.connect(path)
        self.assertEqual(db.rebuilds_pending(con), [])
        con.close()

    def test_a_tracker_missing_any_one_kind_is_stale(self):
        """Not just the newest kind (plan 28): the check once looked for the
        word 'themuse' alone, so a tracker missing a later kind was never
        rebuilt and its first posting of that kind failed the CHECK."""
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        table = db._SOURCES_TABLE_RE.search(schema).group(0)
        kinds = sorted(db.source_kinds(table))
        self.assertIn("themuse", kinds)
        for kind in kinds:
            with self.subTest(kind=kind):
                con = sqlite3.connect(":memory:")
                con.row_factory = sqlite3.Row
                con.executescript(table.replace(f"'{kind}',", "").replace(f",'{kind}'", ""))
                self.assertTrue(db._sources_stale(con, schema))
                self.assertIn("sources", db.rebuilds_pending(con, schema))
                con.close()
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(table)
        self.assertFalse(db._sources_stale(con, schema))
        con.close()

    def test_a_rebuilt_tracker_accepts_every_kind(self):
        folder, path = self.old_tracker()
        with redirect_stderr(io.StringIO()):
            db.upgrade(path)
        con = db.connect(path)
        table = db._SOURCES_TABLE_RE.search(SCHEMA_PATH.read_text(encoding="utf-8")).group(0)
        for i, kind in enumerate(sorted(db.source_kinds(table))):
            db.upsert_source(con, name=f"s{i}", kind=kind, url="https://x", company_id=None)
        con.close()

    def test_an_up_to_date_tracker_gets_no_copy(self):
        folder = Path(tempfile.mkdtemp())
        path = tracker(folder)
        db.upgrade(path)
        self.assertEqual(backup.listing(folder / "backups"), [])

    def test_a_failed_copy_stops_the_upgrade(self):
        from jsa.config import ConfigError
        folder, path = self.old_tracker()
        before = path.read_bytes()
        with mock.patch("jsa.backup.make", side_effect=OSError("disk full")), \
             self.assertRaises(ConfigError) as ctx:
            db.upgrade(path)
        self.assertIn("Nothing was changed", str(ctx.exception))
        self.assertEqual(path.read_bytes(), before)


class TestCommands(Base):
    def run_cli(self, *argv, stdin=""):
        from jsa import cli
        out = io.StringIO()
        with mock.patch("jsa.cli.DB_PATH", self.db), \
             mock.patch("jsa.backup.OUTPUT_DIR", self.out), \
             mock.patch("jsa.backup.PROFILE_PATH", self.profile), \
             mock.patch("jsa.backup.DB_PATH", self.db), \
             mock.patch("builtins.input", return_value=stdin), \
             redirect_stdout(out), redirect_stderr(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_backup_and_list(self):
        code, said = self.run_cli("backup")
        self.assertEqual(code, 0, said)
        self.assertIn("verified", said)
        code, said = self.run_cli("backup", "--list")
        self.assertIn("manual", said)

    def test_check_writes_nothing(self):
        self.mark_sent()
        before = self.db.read_bytes()
        code, said = self.run_cli("backup", "--check")
        self.assertEqual(code, 0, said)
        self.assertIn("checked 1 sent document", said)
        self.assertEqual(self.db.read_bytes(), before)

    def test_restore_copies_the_current_state_first(self):
        path = self.make()
        con = db.connect(self.db)
        con.execute("INSERT INTO companies (id,name,slug) VALUES (9,'Later','later')")
        con.commit()
        con.close()
        code, said = self.run_cli("restore", str(path), stdin="restore")
        self.assertEqual(code, 0, said)
        labels = [c.label for c in backup.listing(self.root)]
        self.assertIn("pre-restore", labels)
        con = db.connect(self.db)
        names = {r[0] for r in con.execute("SELECT name FROM companies")}
        con.close()
        self.assertEqual(names, {"Acme"})

    def test_restore_needs_the_typed_word(self):
        path = self.make()
        code, said = self.run_cli("restore", str(path), stdin="yes")
        self.assertEqual(code, 1)
        self.assertIn("nothing changed", said)
        self.assertNotIn("pre-restore", [c.label for c in backup.listing(self.root)])

    def test_restore_refuses_while_the_tracker_is_in_use(self):
        path = self.make()
        Path(f"{self.db}-wal").write_bytes(b"pending writes")
        code, said = self.run_cli("restore", str(path), "--yes")
        self.assertEqual(code, 1)
        self.assertIn("Stop the dashboard", said)
        self.assertNotIn("pre-restore", [c.label for c in backup.listing(self.root)])


class TestDoctor(Base):
    def test_no_copy_or_an_old_one_is_mentioned(self):
        from jsa import doctor
        with mock.patch("jsa.backup.DB_PATH", self.db):
            report = doctor.Report()
            con = db.connect(self.db)
            doctor.check_tracker(con, {}, report)
            self.assertTrue(any("never been backed up" in f.what for f in report.findings))
            self.make()
            report = doctor.Report()
            doctor.check_tracker(con, {}, report)
            con.close()
        self.assertFalse(any("backed up" in f.what or "backup" in f.what
                             for f in report.findings))


class TestKeptPrivate(unittest.TestCase):
    def test_backups_are_ignored_everywhere(self):
        self.assertIn("backups/", (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
        self.assertIn("**/backups/", (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())
        from tools.scan_secrets import SKIP_DIRS
        self.assertIn("backups", SKIP_DIRS)


if __name__ == "__main__":
    unittest.main()


class TestReviewFixes(Base):
    """Review R-04, R-08 and R-23."""

    def jobs(self, path=None):
        con = sqlite3.connect(path or self.db)
        try:
            return con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        finally:
            con.close()

    def restore(self, source, **kw):
        return backup.restore(source, db_path=self.db, everything=True,
                              output_dir=self.out, profile_path=self.profile, **kw)

    def test_a_failed_copy_leaves_no_folder(self):
        with mock.patch("jsa.backup.shutil.copytree", side_effect=OSError("disk full")):
            with self.assertRaises(backup.BackupError):
                self.make()
        self.assertEqual(list(self.root.iterdir()) if self.root.exists() else [], [])

    def test_a_folder_without_a_manifest_is_not_a_copy(self):
        good = self.make()
        half = self.root / "2099-01-01_000000-manual"
        half.mkdir()
        self.assertEqual([c.path for c in backup.listing(self.root)], [good])
        backup.prune(self.root, keep={"manual": 0})
        self.assertTrue(half.exists(), "prune never touches what isn't a copy")

    def test_daily_does_not_prune_when_its_copy_fails_to_verify(self):
        from jsa import daily
        bad = backup.Check(self.root)
        bad.problems.append("x")
        with mock.patch("jsa.backup.verify", return_value=bad), \
             mock.patch("jsa.backup.prune") as prune, \
             mock.patch("jsa.backup.make", return_value=self.root):
            step = daily._backup(self.db)
        self.assertEqual(step.state, "failed")
        prune.assert_not_called()

    def test_a_file_that_cannot_be_moved_stops_the_restore_before_anything_changes(self):
        copy = self.make()
        con = db.connect(self.db)
        con.execute("INSERT INTO jobs (id,company_id,title,url) VALUES (6,1,'New','u')")
        con.commit()
        con.close()
        (self.out / "new.docx").write_bytes(b"made after the copy")
        real = os.replace

        def locked(src, dst):
            if Path(src) == self.out:
                raise PermissionError("in use")
            return real(src, dst)
        with mock.patch("jsa.backup.os.replace", side_effect=locked):
            with self.assertRaises(backup.BackupError) as ctx:
                self.restore(copy)
        self.assertIn("nothing was restored", str(ctx.exception))
        self.assertIn("pre-restore", str(ctx.exception))
        self.assertEqual(self.jobs(), 2, "the tracker was not touched")
        self.assertTrue((self.out / "new.docx").exists())
        self.assertTrue(self.resume.exists())
        self.assertEqual([p.name for p in self.tmp.iterdir() if ".restoring" in p.name], [])

    def test_a_full_restore_swaps_output_in(self):
        copy = self.make()
        (self.out / "new.docx").write_bytes(b"made after the copy")
        self.restore(copy)
        self.assertFalse((self.out / "new.docx").exists())
        self.assertTrue(self.resume.exists())
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.startswith(".output")], [])

    def test_a_missing_tracker_can_still_be_restored(self):
        copy = self.make()
        self.db.unlink()
        safety = backup.restore(copy, db_path=self.db)
        self.assertIsNone(safety)
        self.assertEqual(self.jobs(), 1)
