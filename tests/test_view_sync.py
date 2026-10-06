"""Views are replaced only when they changed, and atomically (review R-02).

`migrate()` used to drop every view with autocommit and recreate them later,
so a dashboard reading `v_new_matches` while another process upgraded got
"no such table" (37,287 failed reads during 40 upgrades in the probe).
"""

import shutil
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from jsa import db


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = self.dir / "t.db"
        db.init_db(self.path)

    def schema_version(self):
        con = sqlite3.connect(self.path)
        try:
            return con.execute("PRAGMA schema_version").fetchone()[0]
        finally:
            con.close()


class TestViews(Case):
    def test_an_up_to_date_tracker_is_left_alone(self):
        db.upgrade(self.path)
        before = self.schema_version()
        db.upgrade(self.path)
        self.assertEqual(self.schema_version(), before)

    def test_an_old_definition_is_replaced(self):
        con = db.connect(self.path)
        con.execute("DROP VIEW v_pipeline")
        con.execute("CREATE VIEW v_pipeline AS SELECT 1 AS old")
        con.commit()
        con.close()
        db.upgrade(self.path)
        con = db.connect(self.path)
        self.addCleanup(con.close)
        cols = [r[1] for r in con.execute("PRAGMA table_info(v_pipeline)")]
        self.assertNotIn("old", cols)
        self.assertIn("status", cols)

    def test_a_missing_view_is_created(self):
        con = db.connect(self.path)
        con.execute("DROP VIEW v_awaiting_approval")
        con.commit()
        con.close()
        db.upgrade(self.path)
        con = db.connect(self.path)
        self.addCleanup(con.close)
        con.execute("SELECT * FROM v_awaiting_approval").fetchall()

    def test_a_reader_never_sees_a_view_missing_during_upgrades(self):
        stop, errors, reads = threading.Event(), [], [0]

        def read():
            con = sqlite3.connect(self.path, timeout=10)
            try:
                while not stop.is_set():
                    try:
                        con.execute("SELECT COUNT(*) FROM v_new_matches").fetchone()
                        reads[0] += 1
                    except sqlite3.OperationalError as exc:
                        errors.append(str(exc))
            finally:
                con.close()

        thread = threading.Thread(target=read)
        thread.start()
        try:
            for i in range(15):
                # Force a real rebuild each time: change the stored view.
                con = db.connect(self.path)
                con.execute("DROP VIEW v_pipeline")
                con.execute(f"CREATE VIEW v_pipeline AS SELECT {i} AS old")
                con.commit()
                con.close()
                db.upgrade(self.path)
        finally:
            stop.set()
            thread.join()
        self.assertGreater(reads[0], 0)
        self.assertEqual([e for e in errors if "no such table" in e], [])


if __name__ == "__main__":
    unittest.main()
