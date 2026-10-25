"""tools/measure_inbox.py reads the mailbox only, and keeps no email text (G6)."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "tools" / "measure_inbox.py").read_text(encoding="utf-8")


class TestReadOnly(unittest.TestCase):
    def test_it_opens_the_tracker_read_only_and_writes_nothing(self):
        self.assertIn("?mode=ro", SOURCE)
        for word in ("INSERT", "UPDATE ", "DELETE", ".commit(", "write_text", "open("):
            self.assertNotIn(word, SOURCE.replace("connect(f\"file:", ""), word)

    def test_the_mailbox_is_read_the_way_jsa_inbox_reads_it(self):
        # inbox.connect selects read-only; the fetches are inbox's PEEK specs.
        self.assertIn("inbox.connect(cfg)", SOURCE)
        self.assertIn("inbox.HEADER_SPEC", SOURCE)
        self.assertIn("inbox.TEXT_SPEC", SOURCE)
        calls = {n.args[0].value for n in ast.walk(ast.parse(SOURCE))
                 if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "uid"
                 and n.args and isinstance(n.args[0], ast.Constant)}
        self.assertEqual(calls, {"SEARCH"})

    def test_no_subject_or_text_is_printed(self):
        printed = [n for n in ast.walk(ast.parse(SOURCE))
                   if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "print"]
        text = "".join(ast.unparse(p) for p in printed)
        for word in ("subject", "text_of", "body", "head["):
            self.assertNotIn(word, text)


class TestTheTable(unittest.TestCase):
    def test_counts_by_label_and_reading(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("measure_inbox",
                                                      ROOT / "tools" / "measure_inbox.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        table = tool.confusion([("received", "received"), ("received", "interview")])
        row = next(line for line in table.splitlines() if line.startswith("received"))
        self.assertEqual(row.split()[1:], ["0", "1", "0", "1", "0"])


if __name__ == "__main__":
    unittest.main()
