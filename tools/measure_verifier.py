"""Measure the subject-aware check on real drafts (plans 37 and 39). Run by hand.

    .venv/Scripts/python tools/measure_verifier.py --questions FILE [--prompt split|old]
    .venv/Scripts/python tools/measure_verifier.py --questions-from SAVED.json [...]
    .venv/Scripts/python tools/measure_verifier.py --recheck SAVED.json
    add --written 421,484 to also draft those jobs' written answers
    .venv/Scripts/python tools/measure_verifier.py --letters 421,484 (cover letters, G7)

`--questions` / `--questions-from` make PAID model calls: one per question
(three angles: about the role, about a project, about the company), plus one
retry when a draft fails the new check; `--written` one per job plus one
retry. FILE has one `job_id | question` per line; `--questions-from` takes
the pairs from an earlier saved run. The prompts are production's own
(`suggest.build`, `answers.build`): `--prompt split` (the default) asks for
sentences about the employer kept apart from sentences about you (plan 39),
`--prompt old` is the plan 37 prompt. The drafts are your material: they are
saved to logs/verifier-measure-<date>.json beside the tracker (gitignored)
and never committed.

Every draft is checked the old way (whole text) and the new way (sentence by
sentence), with the attitude frame on and off, before and after the retry;
length-only refusals are counted apart; and every sentence of every first
draft is counted by shape (about the employer, about you, mixed, unclear).
Then the drafts that pass ONLY under the new check are printed in full, for
you to read: any that says something about you that isn't in your profile is
a leak, and the switch stays off.

`--letters` drafts a cover letter for each job with the sentence-split prompt
(letter.build, switch on): one PAID call per job plus one retry when the new
check refuses it. Each letter is checked the old way (whole text) and the new
way (sentence by sentence, against the excerpt the drafter saw), and the
letters that pass ONLY under the new check are printed for you to read.
The bullets are the job's approved resume's (else selection's, no model).

`--recheck` re-runs the checks on a saved file, with no model call.
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

from jsa import answers, config, db, ledger, letter, llm, suggest  # noqa: E402
from jsa.config import load_profile  # noqa: E402

ANGLES = ("role", "project", "company")
WRITTEN = ("why_role", "relevant_project", "why_company")


def prompt_for(profile, job, bullets, question, split=False):
    """The Suggest prompt with all three angles (old: plan 37's text)."""
    prompt, system, excerpt = suggest.build(job, profile, bullets, question, angles=ANGLES,
                                            split=split)
    return prompt, excerpt, system


def _check(text, profile, job, excerpt, frame=True):
    from jsa import subject
    before = subject.ATTITUDE_FRAME
    subject.ATTITUDE_FRAME = frame
    try:
        return answers.check(text, profile, job, posting_text=excerpt)
    finally:
        subject.ATTITUDE_FRAME = before


def verdicts(text, profile, job, excerpt):
    return {"old": answers.check(text, profile, job),
            "new": _check(text, profile, job, excerpt, True),
            "new_no_frame": _check(text, profile, job, excerpt, False)}


def length_only(problems):
    return bool(problems) and all("words, outside" in p or "characters, over" in p
                                  for p in problems)


def shapes(text, profile, job, excerpt):
    """Each sentence: employer, about you, mixed, or unclear."""
    company = job.get("company") or ""
    out = Counter()
    for s in letter.checked_sentences(text, profile, job, excerpt):
        if s["kind"] == "employer":
            out["employer"] += 1
        elif suggest._mixed(s, company):
            out["mixed"] += 1
        elif s["why"].startswith("unclear"):
            out["unclear"] += 1
        else:
            out["about you"] += 1
    return out


def _call(prompt, system, profile, job, excerpt, keys, split):
    """One draft and at most one retry, production's rule."""
    from jsa.tailor import scrub_prompt
    scrub_prompt(prompt, profile)
    data, _ = llm.complete_json(prompt, system=system, purpose="suggest", max_tokens=1200,
                                temperature=0.4, thinking=False, attempts=2)
    first = {k: " ".join(str((data or {}).get(k) or "").split()) for k in keys}
    retried = dict(first)
    failing = {k: _check(t, profile, job, excerpt) for k, t in first.items()}
    failing = {k: p for k, p in failing.items() if p}
    if failing:
        retry = suggest.retry_text(prompt, first, failing, profile, job, excerpt, split)
        scrub_prompt(retry, profile)
        again, _ = llm.complete_json(retry, system=system, purpose="suggest", max_tokens=1200,
                                     temperature=0.3, thinking=False, attempts=2)
        for k in failing:
            text = " ".join(str((again or {}).get(k) or "").split())
            if text:
                retried[k] = text
    return first, retried


def _job(con, job_id):
    job = answers.job_row(con, job_id)
    if job is None:
        return None
    return {k: job.get(k) for k in ("id", "title", "company", "location", "track",
                                    "description")}


def draft(profile, con, rows, split=True):
    saved = []
    for job_id, question in rows:
        job = _job(con, job_id)
        if job is None:
            print(f"no job {job_id}; skipped")
            continue
        bullets = answers.evidence(con, job, profile)
        prompt, excerpt, system = prompt_for(profile, job, bullets,
                                             suggest.clean_question(question), split)
        first, retried = _call(prompt, system, profile, job, excerpt, ANGLES, split)
        saved.append({"job_id": job_id, "question": question, "job": job,
                      "excerpt": excerpt, "first": first, "retried": retried,
                      "prompt": "split" if split else "old"})
        print(f"drafted {job_id}: {question[:50]}")
    return saved


def draft_written(profile, con, job_ids):
    saved = []
    for job_id in job_ids:
        job = _job(con, job_id)
        if job is None:
            print(f"no job {job_id}; skipped")
            continue
        bullets = answers.evidence(con, job, profile)
        prompt, system, excerpt = answers.build(job, profile, bullets, keys=WRITTEN,
                                                split=True)
        first, retried = _call(prompt, system, profile, job, excerpt, WRITTEN, True)
        saved.append({"job_id": job_id, "question": "(written answers)", "job": job,
                      "excerpt": excerpt, "first": first, "retried": retried,
                      "prompt": "split", "written": True})
        print(f"drafted the written answers of {job_id}")
    return saved


def report(profile, saved):
    keys = sorted({k for item in saved for k in item["first"]},
                  key=lambda k: (ANGLES + WRITTEN).index(k) if k in ANGLES + WRITTEN else 99)
    counts = {k: Counter() for k in keys}
    shape = Counter()
    newly = []
    for item in saved:
        job, excerpt = item["job"], item["excerpt"]
        for k in item["first"]:
            first, after = item["first"][k], item["retried"][k]
            v, w = verdicts(first, profile, job, excerpt), verdicts(after, profile, job, excerpt)
            c = counts[k]
            c["drafts"] += 1
            for name in ("old", "new", "new_no_frame"):
                c[name] += int(not v[name])
                c[name + "_after"] += int(not w[name])
            c["length_only"] += int(length_only(v["new"]))
            shape += shapes(first, profile, job, excerpt)
            for label, text, verdict in (("first", first, v), ("retry", after, w)):
                if verdict["old"] and not verdict["new"]:
                    newly.append((item["job_id"], k, label, text))
    print("\nPer angle (drafts passing): old / new / new without the frame, "
          "first draft -> after one retry")
    for k, c in counts.items():
        print(f"  {k:16} old {c['old']}->{c['old_after']}/{c['drafts']}  "
              f"new {c['new']}->{c['new_after']}/{c['drafts']}  "
              f"no frame {c['new_no_frame']}->{c['new_no_frame_after']}/{c['drafts']}  "
              f"refused only for length {c['length_only']}")
    total = sum(shape.values())
    print(f"\nSentences in the first drafts ({total}): "
          + ", ".join(f"{name} {shape[name]}" for name in
                      ("employer", "about you", "mixed", "unclear")))
    print(f"\nPass ONLY under the new check: {len(newly)}. Read each; any that says "
          "something about you that isn't in your profile is a leak.\n")
    for job_id, k, label, text in newly:
        print(f"--- job {job_id}, {k}, {label} ---\n{text}\n")
    return counts, newly, shape


def draft_letters(profile, con, job_ids):
    """One letter per job, split prompt, production's retry (G7)."""
    from types import SimpleNamespace

    from jsa.tailor import pick_summary, scrub_prompt
    saved = []
    for job_id in job_ids:
        job = _job(con, job_id)
        if job is None:
            print(f"no job {job_id}; skipped")
            continue
        summary = pick_summary(profile, job.get("track") or "", job.get("description") or "",
                               job.get("title") or "")
        draft = SimpleNamespace(summary=(summary or {}).get("text") or "",
                                bullets=answers.evidence(con, job, profile))
        prompt, system, excerpt = letter.build(job, profile, draft, split=True)
        scrub_prompt(prompt, profile)

        def ask(text, temperature):
            done = llm.complete(text, system=system, max_tokens=700, temperature=temperature,
                                thinking=False, purpose="letter")
            return "\n".join(line.strip() for line in (done.text or "").strip().splitlines())

        first = ask(prompt, 0.4)
        after = first
        problems = letter.check(first, profile, job, excerpt)
        if problems:
            retry = letter.retry_prompt(prompt, first, problems, profile, job, excerpt)
            scrub_prompt(retry, profile)
            after = ask(retry, 0.2)
        saved.append({"job_id": job_id, "question": "(cover letter)", "job": job,
                      "excerpt": excerpt, "first": {"letter": first},
                      "retried": {"letter": after}, "prompt": "split", "letter": True})
        print(f"drafted the letter of {job_id}")
    return saved


def report_letters(profile, saved):
    """Old vs new check, first draft and after the retry; the newly passing."""
    items = [i for i in saved if i.get("letter")]
    if not items:
        return None
    c = Counter()
    shape = Counter()
    newly = []
    for item in items:
        job, excerpt = item["job"], item["excerpt"]
        for label, text in (("first", item["first"]["letter"]),
                            ("after", item["retried"]["letter"])):
            old = letter.check(text, profile, job)
            new = letter.check(text, profile, job, excerpt)
            c[f"old_{label}"] += int(not old)
            c[f"new_{label}"] += int(not new)
            if label == "first":
                c["length_only"] += int(length_only(new))
                shape += shapes(text, profile, job, excerpt)
            if old and not new:
                newly.append((item["job_id"], label, text))
    n = len(items)
    print(f"\nCover letters ({n}): old check {c['old_first']}->{c['old_after']}/{n}, "
          f"new check {c['new_first']}->{c['new_after']}/{n} (first draft -> after one "
          f"retry); refused only for length {c['length_only']}")
    print("Sentences in the first drafts: " + ", ".join(
        f"{name} {shape[name]}" for name in ("employer", "about you", "mixed", "unclear")))
    print(f"\nLetters passing ONLY under the new check: {len(newly)}. Read each; any "
          "sentence that says something about you that isn't in your profile is a leak.\n")
    for job_id, label, text in newly:
        print(f"--- job {job_id}, {label} ---\n{text}\n")
    return c, newly, shape


def _rows_from(path):
    saved = json.loads(Path(path).read_text(encoding="utf-8"))
    seen, rows = set(), []
    for item in saved:
        if item.get("written"):
            continue
        pair = (int(item["job_id"]), item["question"])
        if pair not in seen:
            seen.add(pair)
            rows.append(pair)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--questions", help="file of `job_id | question` lines (paid calls)")
    group.add_argument("--questions-from", help="the questions of a saved run (paid calls)")
    group.add_argument("--recheck", help="a saved measure file; no model call")
    parser.add_argument("--prompt", choices=["split", "old"], default="split")
    parser.add_argument("--written", default="", help="job ids for written answers, e.g. 421,484")
    parser.add_argument("--letters", default="", help="job ids for cover letters, e.g. 421,484")
    args = parser.parse_args()
    if not (args.questions or args.questions_from or args.recheck or args.letters):
        parser.error("give --questions, --questions-from, --recheck or --letters")
    config.load_dotenv()
    profile = load_profile()
    if args.recheck:
        saved = json.loads(Path(args.recheck).read_text(encoding="utf-8"))
        if any(not i.get("letter") for i in saved):
            report(profile, [i for i in saved if not i.get("letter")])
        report_letters(profile, saved)
        return 0
    if args.questions_from:
        rows = _rows_from(args.questions_from)
    elif not args.questions:
        rows = []
    else:
        rows = []
        for line in Path(args.questions).read_text(encoding="utf-8").splitlines():
            if "|" in line and not line.lstrip().startswith("#"):
                job_id, question = line.split("|", 1)
                rows.append((int(job_id.strip()), question.strip()))
    written = [int(x) for x in args.written.split(",") if x.strip()]
    con = db.connect(config.DB_PATH)
    previous = ledger.install(config.DB_PATH)
    try:
        saved = draft(profile, con, rows, split=args.prompt == "split")
        saved += draft_written(profile, con, written)
        saved += draft_letters(profile, con,
                               [int(x) for x in args.letters.split(",") if x.strip()])
    finally:
        llm.recorder = previous
        con.close()
    out = config.HOME / "logs" / f"verifier-measure-{dt.date.today().isoformat()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out = out.with_name(out.stem + f"-{dt.datetime.now():%H%M%S}.json")
    out.write_text(json.dumps(saved, indent=1), encoding="utf-8")
    print(f"saved the drafts to {out} (gitignored; yours, never committed)")
    if any(not i.get("letter") for i in saved):
        report(profile, [i for i in saved if not i.get("letter")])
    report_letters(profile, saved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
