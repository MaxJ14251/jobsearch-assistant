"""Measure the subject-aware check on real drafts (plan 37). Run by hand.

    .venv/Scripts/python tools/measure_verifier.py --questions FILE
    .venv/Scripts/python tools/measure_verifier.py --recheck SAVED.json

`--questions` makes PAID model calls: one per question, plus one retry when
a draft fails the new check. FILE has one `job_id | question` per line, for
jobs in your tracker. Each call drafts three answers (about the role, about
a project, about the company) from the same prompt the extension's Suggest
uses, with the company angle added. The drafts are your material: they are
saved to logs/verifier-measure-<date>.json beside the tracker (gitignored)
and never committed.

Every draft is checked four ways: the old whole-text check, and the new
sentence-by-sentence check with the attitude frame on and off, plus the new
check after one retry. Then the drafts that pass ONLY under the new check
are printed in full, for you to read: any that says something about you
that isn't in your profile is a leak, and the switch stays off.

`--recheck` re-runs the checks on a saved file, with no model call (after a
fix to the sorter).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jsa import answers, config, db, ledger, llm, suggest  # noqa: E402
from jsa.config import load_profile  # noqa: E402
from jsa.posting import OUTREACH_CHARS, visible  # noqa: E402

ANGLES = ("role", "project", "company")

THREE = '''Write three different answers to the question, each {min_words}-{max_words} words:
- "role": from what the notes show they do, tied to this role.
- "project": built around one specific project or job from the notes, what it
  does and what part they did.
- "company": why this employer, from what the posting says about it, and what
  in the notes fits it.

Return JSON: {{"role": "...", "project": "...", "company": "..."}}'''


def prompt_for(profile, job, bullets, question):
    """The Suggest prompt, asking for the company angle too."""
    base = suggest.PROMPT.split("Write two different answers")[0]
    excerpt = visible(job.get("description"), budget=OUTREACH_CHARS).text
    text = (base + THREE).format(
        title=job.get("title") or "the role", company=job.get("company") or "the company",
        question=suggest._fence(question), posting=suggest._fence(excerpt) or "(none)",
        summary=answers._summary(profile, job) or "(none)",
        bullets="\n".join(f"- {b.text}" for b in bullets) or "(none)",
        min_words=answers.MIN_WORDS, max_words=answers.MAX_WORDS)
    return text, excerpt


def verdicts(text, profile, job, excerpt):
    from jsa import subject
    out = {"old": answers.check(text, profile, job)}
    for frame in (True, False):
        before = subject.ATTITUDE_FRAME
        subject.ATTITUDE_FRAME = frame
        try:
            out["new" if frame else "new_no_frame"] = answers.check(
                text, profile, job, posting_text=excerpt)
        finally:
            subject.ATTITUDE_FRAME = before
    return out


def draft(profile, con, rows):
    from jsa.letter import refused_words
    from jsa.tailor import scrub_prompt
    saved = []
    for job_id, question in rows:
        job = answers.job_row(con, job_id)
        if job is None:
            print(f"no job {job_id}; skipped")
            continue
        job = {k: job.get(k) for k in ("id", "title", "company", "location", "track",
                                       "description")}
        bullets = answers.evidence(con, job, profile)
        prompt, excerpt = prompt_for(profile, job, bullets, suggest.clean_question(question))
        scrub_prompt(prompt, profile)
        data, _ = llm.complete_json(prompt, system=suggest.SYSTEM, purpose="suggest",
                                    max_tokens=1200, temperature=0.4, thinking=False,
                                    attempts=2)
        first = {a: " ".join(str((data or {}).get(a) or "").split()) for a in ANGLES}
        retried = dict(first)
        failing = {a: answers.check(t, profile, job, posting_text=excerpt)
                   for a, t in first.items()}
        failing = {a: p for a, p in failing.items() if p}
        if failing:
            banned = sorted({w for a in failing
                             for w in refused_words(first[a], profile, job, excerpt)})
            retry = (prompt + "\n\nYour previous answers were refused: "
                     + "; ".join(f"{a}: {', '.join(p[:2])}" for a, p in failing.items())
                     + ".\nRewrite only those keys" + (", without these words: "
                     + ", ".join(banned[:30]) if banned else "")
                     + ". Say only what the notes say.")
            again, _ = llm.complete_json(retry, system=suggest.SYSTEM, purpose="suggest",
                                         max_tokens=1200, temperature=0.3, thinking=False,
                                         attempts=2)
            for a in failing:
                text = " ".join(str((again or {}).get(a) or "").split())
                if text:
                    retried[a] = text
        saved.append({"job_id": job_id, "question": question, "job": job,
                      "excerpt": excerpt, "first": first, "retried": retried})
        print(f"drafted {job_id}: {question[:50]}")
    return saved


def report(profile, saved):
    counts = {a: Counter() for a in ANGLES}
    newly = []
    for item in saved:
        job, excerpt = item["job"], item["excerpt"]
        for a in ANGLES:
            first = item["first"][a]
            v = verdicts(first, profile, job, excerpt)
            counts[a]["drafts"] += 1
            for name, problems in v.items():
                counts[a][name] += int(not problems)
            after = item["retried"][a]
            counts[a]["new_after_retry"] += int(
                not answers.check(after, profile, job, posting_text=excerpt))
            for label, text, verdict in (("first", first, v),
                                         ("retry", after, verdicts(after, profile, job,
                                                                   excerpt))):
                if verdict["old"] and not verdict["new"]:
                    newly.append((item["job_id"], a, label, text))
    print("\nPer angle (drafts passing):")
    for a in ANGLES:
        c = counts[a]
        print(f"  {a:8} old {c['old']}/{c['drafts']}  new {c['new']}/{c['drafts']}  "
              f"new without the frame {c['new_no_frame']}/{c['drafts']}  "
              f"new after one retry {c['new_after_retry']}/{c['drafts']}")
    print(f"\nPass ONLY under the new check: {len(newly)}. Read each; any that says "
          "something about you that isn't in your profile is a leak.\n")
    for job_id, a, label, text in newly:
        print(f"--- job {job_id}, {a}, {label} ---\n{text}\n")
    return counts, newly


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--questions", help="file of `job_id | question` lines (paid calls)")
    group.add_argument("--recheck", help="a saved measure file; no model call")
    args = parser.parse_args()
    config.load_dotenv()
    profile = load_profile()
    if args.recheck:
        saved = json.loads(Path(args.recheck).read_text(encoding="utf-8"))
        report(profile, saved)
        return 0
    rows = []
    for line in Path(args.questions).read_text(encoding="utf-8").splitlines():
        if "|" in line and not line.lstrip().startswith("#"):
            job_id, question = line.split("|", 1)
            rows.append((int(job_id.strip()), question.strip()))
    con = db.connect(config.DB_PATH)
    previous = ledger.install(config.DB_PATH)
    try:
        saved = draft(profile, con, rows)
    finally:
        llm.recorder = previous
        con.close()
    out = config.HOME / "logs" / f"verifier-measure-{dt.date.today().isoformat()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(saved, indent=1), encoding="utf-8")
    print(f"saved the drafts to {out} (gitignored; yours, never committed)")
    report(profile, saved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
