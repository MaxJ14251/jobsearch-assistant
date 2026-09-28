"""A Lever posting is stored as its hosted page shows it.

`descriptionPlain` is only the top of a Lever page: the requirement lists live
in `lists` and pay and benefits in `additional`. Measured 2026-09-28 on the
five verified Lever boards, storing the description alone dropped a median
3,400 characters per posting, including the degree and years lines that
enrichment and tailoring read. The fixture copies the shape the public
postings API returned that day, trimmed.
"""

import unittest
from unittest import mock

from jsa import posting, sources

POSTING = {
    "id": "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
    "text": "Software Engineer, Autonomy",
    "categories": {"commitment": "Full-time", "location": "San Diego, California",
                   "team": "Engineering"},
    "hostedUrl": "https://jobs.lever.co/example/0f1e2d3c",
    "createdAt": 1758000000000,
    "opening": "<div>Founded in 2015, we build autonomy software.</div>",
    "openingPlain": "Founded in 2015, we build autonomy software.",
    "description": ("<div>Founded in 2015, we build autonomy software.</div>"
                    "<div>You will write the planner.</div>"),
    "descriptionPlain": ("Founded in 2015, we build autonomy software.\n"
                         "You will write the planner.\n"),
    "lists": [
        {"text": "What you'll do: ",
         "content": "<li>Design motion planning algorithms</li>"
                    "<li>Ship C++ to flight hardware</li>"},
        {"text": "Required qualifications:",
         "content": "<li>BS in Computer Science or a related field</li>"
                    "<li>3+ years of C++ experience</li>"
                    "<li>Ability to obtain a SECRET clearance</li>"},
        {"text": "Empty section", "content": ""},
    ],
    "additional": "<div>The salary range for this role is $140,000 - $180,000.</div>",
    "additionalPlain": "The salary range for this role is $140,000 - $180,000.\n",
}

EXPECTED = (
    "Founded in 2015, we build autonomy software.\n"
    "You will write the planner.\n"
    "\n"
    "What you'll do:\n"
    "Design motion planning algorithms\n"
    "Ship C++ to flight hardware\n"
    "\n"
    "Required qualifications:\n"
    "BS in Computer Science or a related field\n"
    "3+ years of C++ experience\n"
    "Ability to obtain a SECRET clearance\n"
    "\n"
    "Empty section\n"
    "\n"
    "The salary range for this role is $140,000 - $180,000."
)


def fetch(payload):
    with mock.patch.object(sources, "_get_json", lambda url, *a, **k: payload):
        result = sources.fetch_lever({"board": "example"})
    assert result.ok, result.status
    return result.jobs


class TestLeverText(unittest.TestCase):
    def test_description_lists_and_additional_in_page_order(self):
        self.assertEqual(sources.lever_text(POSTING), EXPECTED)

    def test_fetch_stores_and_hashes_the_whole_page(self):
        (job,) = fetch([POSTING])
        self.assertEqual(job["description"], EXPECTED)
        self.assertEqual(job["description_hash"], sources.content_hash(EXPECTED))

    def test_opening_is_not_repeated(self):
        # descriptionPlain already starts with the opening.
        self.assertEqual(sources.lever_text(POSTING).count("Founded in 2015"), 1)

    def test_requirements_reach_what_the_model_reads(self):
        window = posting.visible(fetch([POSTING])[0]["description"])
        for line in ("BS in Computer Science", "3+ years of C++", "SECRET clearance"):
            self.assertIn(line, window.text)

    def test_html_fields_are_the_fallback(self):
        item = {**POSTING, "descriptionPlain": "", "additionalPlain": None}
        text = sources.lever_text(item)
        self.assertTrue(text.startswith("Founded in 2015, we build autonomy software.\n"
                                        "You will write the planner."))
        self.assertTrue(text.endswith("The salary range for this role is "
                                      "$140,000 - $180,000."))
        self.assertNotIn("<", text)

    def test_a_posting_without_lists_keeps_its_old_text(self):
        # Byte for byte what was stored before, trailing newline included, so
        # its description_hash does not move and it is not re-enriched.
        item = {"id": "1", "text": "Analyst", "categories": {},
                "descriptionPlain": "Just a paragraph.\n",
                "lists": [{"text": "", "content": ""}], "additionalPlain": ""}
        (job,) = fetch([item])
        self.assertEqual(job["description"], "Just a paragraph.\n")
        self.assertEqual(job["description_hash"],
                         sources.content_hash("Just a paragraph.\n"))


if __name__ == "__main__":
    unittest.main()
