"""The golden-pages helper keeps working (plan 24).

`tests/golden_pages.py` renders every dashboard page on one fictional
tracker so a refactor can be compared byte for byte. This checks that each
page still renders, and that two runs agree, so the comparison means
something.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from tests import golden_pages


class TestGoldenPages(unittest.TestCase):
    def test_every_page_renders_and_two_runs_agree(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        first = golden_pages.render_all(root / "a")
        second = golden_pages.render_all(root / "b")
        self.assertEqual(len(first), len(golden_pages.pages()))
        for a, b in zip(first, second):
            text = a.read_text(encoding="utf-8")
            status = text.split("\n", 1)[0]
            expected = "404" if a.stem == "missing_job" else "200"
            self.assertEqual(status, expected, a.name)
            self.assertNotIn("Traceback", text, a.name)
            self.assertEqual(text, b.read_text(encoding="utf-8"), a.name)


if __name__ == "__main__":
    unittest.main()
