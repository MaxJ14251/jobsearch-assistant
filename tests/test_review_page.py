"""The Review page with 40 drafts waiting (found 2026-10-27): 103 of 125
compared rows were "your words, unchanged", the profile advice repeated on
every resume card (20 times, 5 distinct), and the 9 flagged drafts were
mixed in with the rest. Unchanged lines fold, the advice shows once, and
flagged drafts come first. Every line is still one click away."""

import json
import re

from tests.test_web_documents import SandboxCase, queue_document, render_real_docx


class TestTheReviewPage(SandboxCase):
    def bullets(self, n):
        from jsa.tailor import collect_bullets
        from tests.test_web_documents import tailor_profile
        return [(b.id, b.text) for b in list(collect_bullets(tailor_profile()).values())[:n]]

    def test_unchanged_lines_fold_and_stay_one_click_away(self):
        pairs = self.bullets(2)
        doc_id, _ = queue_document(self.box, render_real_docx(self.box, pairs),
                                   [i for i, _ in pairs])
        page = self.client.get("/review").text
        card = page[page.index(f'id="doc-{doc_id}"'):]
        self.assertIn("2 lines are your profile's words, unchanged", card)
        fold = card[card.index("2 lines are your profile"):]
        fold = fold[:fold.index("</details>")]
        for _, text in pairs:
            self.assertIn(text, fold)                    # still there, inside the fold

    def test_a_flagged_line_is_never_folded(self):
        (bid, text), = self.bullets(1)
        padded = text.rstrip(".") + " under strict turnaround requirements and SLA adherence."
        queue_document(self.box, render_real_docx(self.box, [(bid, padded)]), [bid])
        page = self.client.get("/review").text
        self.assertNotIn("your words, unchanged</summary>", page)
        self.assertIn("<mark>turnaround</mark>", page)
        self.assertNotIn("lines are your profile's words, unchanged", page)

    def test_the_profile_advice_shows_once_at_the_top(self):
        pairs = self.bullets(1)
        finding = {"id": "thin_entry", "message": "Acme shows 1 bullet.",
                   "where": "experience[0].bullets"}
        ids = []
        for _ in range(2):
            doc_id, _ = queue_document(self.box, render_real_docx(self.box, pairs),
                                       [i for i, _ in pairs])
            ids.append(doc_id)
        con = self.box.connect()
        con.execute("UPDATE documents SET coach_findings = ?", (json.dumps([finding]),))
        con.commit()
        con.close()
        page = self.client.get("/review").text
        self.assertEqual(page.count("Acme shows 1 bullet."), 1)
        self.assertLess(page.index("Resume report"), page.index(f'id="doc-{ids[0]}"'))

    def test_flagged_drafts_come_first_and_are_counted(self):
        pairs = self.bullets(1)
        faithful, _ = queue_document(self.box, render_real_docx(self.box, pairs),
                                     [i for i, _ in pairs])
        (bid, text), = pairs
        padded = text.rstrip(".") + " under strict turnaround requirements and SLA adherence."
        flagged, _ = queue_document(self.box, render_real_docx(self.box, [(bid, padded)]), [bid])
        page = self.client.get("/review").text
        order = [int(x) for x in re.findall(r'id="doc-(\d+)"', page)]
        self.assertEqual(order, [flagged, faithful])
        self.assertIn("1 draft says something your profile doesn't", page)
        self.assertIn(f'href="#doc-{flagged}"', page)

    def test_nothing_flagged_says_nothing_about_it(self):
        pairs = self.bullets(1)
        queue_document(self.box, render_real_docx(self.box, pairs), [i for i, _ in pairs])
        self.assertNotIn("says something your profile doesn't", self.client.get("/review").text)


class TestWhatCountsAsFlagged(SandboxCase):
    def test_a_fallback_note_alone_is_not_a_flag(self):
        """All 9 "flagged" letters on 2026-10-27 were the note that the
        drafted prose was refused and the letter built from your sentences."""
        from jsa.web.routes_review import _flagged
        letter = {"kind": "cover_letter", "note": "written from your own sentences",
                  "letter": {"problems": []}, "problem": None, "compare": None}
        self.assertFalse(_flagged({"doc": letter}))
        self.assertTrue(_flagged({"doc": {**letter, "letter": {"problems": ["a degree claim"]}}}))
        self.assertTrue(_flagged({"doc": {**letter, "problem": "file missing"}}))
        self.assertTrue(_flagged({"doc": {"kind": "resume", "compare": {"flagged": 1}}}))
        self.assertFalse(_flagged({"body": "an outreach message"}))
