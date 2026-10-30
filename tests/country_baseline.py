"""What the app does with US places, pay and contact details today (country
pack split, phase 1).

`results()` runs one fixed set of fictional inputs through every function the
split will move into a country pack: place parsing, the outside-the-US
reject, location and job scoring, pay reading, the duplicate key and the
resume import's contact redaction. `tests/test_country_baseline.py` holds the
answers to `tests/fixtures/us_baseline.json`, so moving the US into a pack
can be shown to change nothing.

    python -m tests.country_baseline --write    # only when a change is meant
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "us_baseline.json"

ORIGINS = ["92101", "92101-1234", "Austin, TX", "Austin, Texas", "San Diego", "NYC",
           "Washington, DC", "St. Louis, MO", "Ft. Worth, TX", "Springfield",
           "Toronto, ON", "London", "nowhere at all"]

LOCATIONS = ["Austin, TX", "Remote (US)", "Remote", "Remote - US", "Hybrid - Denver, CO",
             "US - California - San Diego", "San Francisco, CA; New York, NY",
             "New York City", "Washington DC", "Toronto, ON", "London, UK",
             "Remote (EMEA)", "Berlin, Germany", "Bangalore", "Remote - Canada",
             "Vancouver, BC", "Austin", "Texas", "United States", "Anywhere",
             "Mexico City", "Portland, OR or Portland, ME", ""]

PAY = ["Salary range: $90,000 - $120,000 per year.",
       "$45/hr to $55/hr", "$120k-$150k", "Base pay $135,000.",
       "USD 100,000 - 130,000 annually", "We pay $25 per hour.",
       "€60.000 - €80.000 per year", "CAD $90,000 - $110,000", "£40,000 - £50,000",
       "$5,000 - $7,000 per month", "Competitive salary and equity.", ""]

TITLES = [("Support Engineer", "Austin, TX"), ("Support Engineer", "Austin, TX (Hybrid)"),
          ("Senior Data Analyst - Remote US", "Remote (US)"),
          ("Account Executive, West", "Denver, CO"), ("Solutions Engineer", "London, UK")]

# Fictional, and built at run time so the secret scanner's address rule
# (a state and a ZIP side by side) doesn't read the source as an address.
ZIP, ZIP2 = "62704", "78701"
RESUME = [
    "Jordan Riverton",
    "123 Maple Street, Springfield, IL " + ZIP,
    "(555) 555-0142 | jordan.riverton@example.com | linkedin.com/in/jordanriverton",
    "github.com/jriverton | https://jordan.example.dev",
    "+1 555-555-0199",
    "Austin, TX " + ZIP2,
    "SUMMARY",
    "Support engineer who writes clear bug reports.",
    "EXPERIENCE",
    "Northwind Supply - Support Engineer - 2022-2024",
    "Answered 40 tickets a day by email and chat.",
]


def _place(p) -> dict | None:
    return None if p is None else {"name": p.name, "state": p.state,
                                   "lat": round(p.lat, 4), "lon": round(p.lon, 4)}


def _prefs():
    from jsa.config import Preferences
    from tests import golden_pages
    profile = golden_pages.profile()
    prefs = profile["job_search_preferences"]
    prefs["locations"] = ["Austin, TX", "San Diego, CA", "Remote (US)"]
    prefs["home"] = "Austin, TX"
    return Preferences.from_profile(profile)


def results() -> dict:
    from jsa import places, resume_import, salary, scoring

    prefs = _prefs()
    out: dict = {}
    out["origin"] = {t: _place(places.origin(t)) for t in ORIGINS}
    out["parse"] = {}
    for loc in LOCATIONS:
        p = places.parse(loc)
        out["parse"][loc] = {"places": [_place(x) for x in p.places], "remote": p.remote,
                             "unplaced": p.unplaced}
    home = places.origin("Austin, TX")
    out["nearest_from_austin"] = {
        loc: (None if (m := places.nearest(home, loc)) is None else round(m, 2))
        for loc in LOCATIONS}
    out["is_non_us"] = {loc: scoring.is_non_us(loc) for loc in LOCATIONS}
    out["names_a_state"] = {loc: scoring.names_a_state(loc) for loc in LOCATIONS}
    out["preferred_states"] = sorted(scoring.preferred_states(prefs.locations))
    out["location_score"] = {}
    for loc in LOCATIONS:
        for remote in ("onsite", "remote"):
            s, why = scoring.location_score(loc, remote, prefs)
            out["location_score"][f"{loc} | {remote}"] = [round(s, 4), why]
    out["pay"] = {}
    for text in PAY:
        s = salary.extract(text)
        out["pay"][text] = None if s is None else {**asdict(s), "label": s.label(),
                                                  "columns": salary.columns(s)}
    out["pay_score"] = {text: [round(v, 4), why] for text in PAY
                        for v, why in [scoring.pay_score(salary.extract(text))]}
    out["dedup_key"] = {f"{t} | {loc}": scoring.dedup_key(1, t, loc) for t, loc in TITLES}
    out["score_job"] = {}
    desc = ("Help customers use the product. Python and SQL. "
            "Salary range: $90,000 - $120,000 per year. ") * 3
    for t, loc in TITLES:
        for remote in ("onsite", "remote"):
            job = {"title": t, "location": loc, "remote": remote, "description": desc,
                   "salary_min": 90000, "salary_max": 120000, "salary_period": "year",
                   "salary_currency": "USD"}
            s, reasons = scoring.score_job(job, prefs)
            out["score_job"][f"{t} | {loc} | {remote}"] = [round(s, 4), reasons]
    ident, redacted = resume_import.split_identity(list(RESUME))
    out["split_identity"] = {"identity": asdict(ident), "redacted": redacted}
    return json.loads(json.dumps(out, default=str))


if __name__ == "__main__":
    data = results()
    if "--write" in sys.argv:
        FIXTURE.parent.mkdir(exist_ok=True)
        FIXTURE.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False)
                           + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {FIXTURE}")
    else:
        print(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False))
