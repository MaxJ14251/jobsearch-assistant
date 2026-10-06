"""Every dashboard page, rendered on one fictional tracker (plan 24).

`render_all(folder)` writes each page's HTML with the CSRF token masked, so
two runs of the same code are byte-identical and a refactor that must change
nothing can be checked file by file:

    python -m tests.golden_pages before/      # on the old code
    python -m tests.golden_pages after/       # on the new code
    (then compare the two folders)

`tests/test_golden_pages.py` keeps it working: every page renders, twice
the same. Nothing here touches the network or the real tracker.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765"
NOW = "2026-10-01T15:00:00Z"


def profile() -> dict:
    data = yaml.safe_load((ROOT / "profile" / "master_profile.example.yaml")
                          .read_text(encoding="utf-8"))
    data = copy.deepcopy(data)
    prefs = data.setdefault("job_search_preferences", {})
    prefs["work_authorization"] = "US citizen - no sponsorship required"
    return data


def build(folder: Path) -> tuple[Path, Path]:
    """A tracker with a job in every state the pages show."""
    from docx import Document

    from jsa import approvals, db
    path, out = folder / "t.db", folder / "output"
    out.mkdir(parents=True)
    db.init_db(path)
    con = db.connect(path)
    co = [(1, "Northwind Supply", "northwind"), (2, "Riverton Analytics", "riverton"),
          (3, "Globex", "globex")]
    con.executemany("INSERT INTO companies (id,name,slug) VALUES (?,?,?)", co)
    jobs = [
        (1, 1, "Support Engineer", "Austin, TX", "onsite", 0.82, "engineering",
         "https://boards.greenhouse.io/northwind/jobs/1"),
        (2, 2, "Customer Support Engineer", "Remote (US)", "remote", 0.77, "engineering",
         "https://www.linkedin.com/jobs/view/4000000001"),
        (3, 3, "Account Executive", "Denver, CO", "hybrid", 0.61, "sales",
         "https://jobs.lever.co/globex/abc"),
        (4, 1, "Data Analyst", "Austin, TX", "onsite", 0.55, "engineering",
         "https://boards.greenhouse.io/northwind/jobs/4"),
        (5, 2, "Solutions Engineer", "Seattle, WA", "onsite", 0.70, "engineering",
         "https://jobs.ashbyhq.com/riverton/x"),
        (6, 3, "Technical Support Specialist", "Austin, TX", "onsite", 0.66, "engineering",
         "https://jobs.lever.co/globex/def"),
        (7, 1, "Implementation Engineer", "Remote (US)", "remote", 0.74, "engineering",
         "https://boards.greenhouse.io/northwind/jobs/7"),
    ]
    text = ("Help customers use the product. Answer technical questions by email "
            "and chat, reproduce issues, and write clear bug reports. Python and "
            "SQL. Salary range: $90,000 - $120,000 per year. ") * 4
    for jid, cid, title, loc, remote, score, track, url in jobs:
        con.execute(
            "INSERT INTO jobs (id,company_id,title,location,remote,match_score,"
            "match_reasons,track,url,description,discovered_at,dedup_key,"
            "salary_min,salary_max,salary_period) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (jid, cid, title, loc, remote, score,
             json.dumps([f"title matches '{title}'"]), track, url, text, NOW,
             f"{cid}:{title.lower()}", 90000, 120000, "year"))
    con.execute("UPDATE jobs SET passed_at = ? WHERE id = 6", (NOW,))
    apps = {}
    for jid, stage in ((1, "ready"), (2, "saved"), (3, "applied"), (4, "phone_screen"),
                       (5, "rejected")):
        apps[jid], _ = approvals.save_application(con, jid)
        if stage == "ready":
            approvals.record_event(con, apps[jid], "ready", actor="agent", note="drafted")
        elif stage != "saved":
            approvals.set_stage(con, jid, stage)
    docs = []
    for jid, kind, version, decide in ((1, "resume", 1, "approve"),
                                       (1, "cover_letter", 1, None),
                                       (2, "resume", 1, "reject")):
        file = out / f"{kind}-{jid}-v{version}.docx"
        d = Document()
        d.add_paragraph(f"Fictional {kind} {jid} v{version}")
        d.save(file)
        doc = con.execute(
            "INSERT INTO documents (job_id,kind,path,version,bullet_ids,model,"
            "generated_at) VALUES (?,?,?,?,?,?,?)",
            (jid, kind, str(file), version, json.dumps(["b_ex_1"]), "test/model",
             NOW)).lastrowid
        approvals.set_document_pointer(con, apps[jid], kind, doc)
        approval = approvals.queue(con, "document", doc, f"{kind} v{version}")
        if decide == "approve":
            approvals.approve(con, approval)
        elif decide == "reject":
            approvals.reject(con, approval, "Wrong bullets for this role.")
        docs.append(doc)
    con.execute(
        "INSERT INTO interview_prep (id,application_id,round,questions,company_brief,"
        "generated_at) VALUES (1,?,?,?,?,?)",
        (apps[4], "phone_screen",
         json.dumps([{"question": "Tell me about a hard ticket.", "why": "support",
                      "answer_notes": "I traced a data issue to its source."}]),
         "A small analytics team.", NOW))
    con.execute(
        "INSERT INTO inbox_replies (message_id,application_id,received_at,"
        "sender_domain,subject,kind,suggested_stage,rule,state,created_at) "
        "VALUES ('m1',?,?,?,?,?,?,?,'pending',?)",
        (apps[3], NOW, "globex.example", "Next steps", "interview", "phone_screen",
         "interview", NOW))
    con.execute(
        "INSERT INTO daily_runs (started_at,finished_at,ok,steps_json,summary_json) "
        "VALUES (?,?,1,?,?)",
        (NOW, NOW, json.dumps([{"name": "backup", "state": "ok", "detail": "verified"}]),
         json.dumps({"new_matches": 2, "top": [], "replies": 1, "due": 0,
                     "review": 1, "failed_feeds": []})))
    con.commit()
    con.close()
    return path, out


def pages(job_ids=range(1, 7)) -> list[tuple[str, str, dict | None]]:
    """(name, path, POST form or None)."""
    found = [("matches", "/", None), ("matches_search", "/?q=support", None),
             ("matches_near", "/?home=Austin%2C+TX&radius=50", None),
             ("matches_nationwide", "/?anywhere=1", None),
             ("turbo", "/turbo", None), ("turbo_status", "/turbo/status", None),
             ("setup", "/setup", None), ("setup_status", "/setup/discover/status", None),
             ("add", "/add", None), ("add_prefill", "/add?company=Globex&title=Analyst", None),
             ("import", "/import", None), ("prep", "/prep/1", None),
             ("pipeline", "/pipeline", None), ("review", "/review", None),
             ("preview", "/document/1/preview", None), ("missing_job", "/job/9999", None),
             ("find", "/find", {"company": "Northwind Supply", "title": "Support Engineer",
                                "city": "Austin, TX"})]
    found += [(f"job_{j}", f"/job/{j}", None) for j in job_ids]
    return found


def render_all(folder: Path) -> list[Path]:
    from fastapi.testclient import TestClient

    from jsa import sources, web
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp())
    written = []
    try:
        path, out = build(work)
        prof = profile()
        app = web.create_app(db_path=path, output_dir=out, profile_loader=lambda: prof)
        token = app.state.csrf_token
        with TestClient(app, base_url=BASE) as client, \
             mock.patch("jsa.sources.fetch",
                        return_value=sources.FetchResult(False, [], "offline")), \
             mock.patch("jsa.sources._get_json", side_effect=OSError("offline")), \
             mock.patch("jsa.llm.api_key", return_value=""):
            for name, url, form in pages():
                r = (client.post(url, data={"csrf": token, **form}) if form
                     else client.get(url))
                body = f"{r.status_code}\n{r.text}".replace(token, "CSRF-TOKEN")
                body = re.sub(re.escape(str(work)).replace("\\\\", "[\\\\/]+"),
                              "WORK", body)
                target = folder / f"{name}.html"
                target.write_text(body, encoding="utf-8", newline="\n")
                written.append(target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return written


if __name__ == "__main__":
    for page in render_all(Path(sys.argv[1] if len(sys.argv) > 1 else "golden")):
        print(page)
