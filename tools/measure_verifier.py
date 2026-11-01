"""Measure the subject-aware check on real drafts (plans 37 and 39). Run by hand.

    .venv/Scripts/python tools/measure_verifier.py --questions FILE [--prompt split|old]
    .venv/Scripts/python tools/measure_verifier.py --questions-from SAVED.json [...]
    .venv/Scripts/python tools/measure_verifier.py --recheck SAVED.json
    add --written 421,484 to also draft those jobs' written answers
    .venv/Scripts/python tools/measure_verifier.py --letters 421,484 (cover letters, G7)
    .venv/Scripts/python tools/measure_verifier.py --outreach 421,452,484 (G8)

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

`--outreach` drafts a referral ask for each job on each channel (LinkedIn
connection note, LinkedIn message, email) to a fictional contact, with the
split prompt (outreach.build, switch on) and production's loop for one retry
(outreach.attempts): PAID calls, one draft per message plus shortening and
the retry. Each is held to the full check (verify_message), and the passing
messages are printed for you to read.

`--presses FILE` runs real Suggest presses, production's own code from the
question to the options, and reports how alike the answers are: PAID, one call
per press plus one retry when a draft is refused, stopping at `--max-calls`
(default 24). FILE has one `job_id | question` or `job_id | question | your own
reason` per line. Nothing is written to the tracker's suggestions, so no daily
suggestion is used. `--presses-report SAVED.json` re-reads a saved run, no call.

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


CHANNELS = ("linkedin_connect", "linkedin_dm", "email")
# A fictional recipient: no real person's name goes to the model (G8).
RECIPIENT = {"name": "Jordan Example", "title": "Engineering Manager"}


def draft_outreach(profile, con, job_ids):
    """Each job x each channel: the split prompt, a first draft and at most
    one retry (outreach.attempts, production's loop), to a fictional contact."""
    from jsa import outreach
    from jsa.tailor import scrub_prompt
    saved = []
    for job_id in job_ids:
        job = answers.job_row(con, job_id)
        if job is None:
            print(f"no job {job_id}; skipped")
            continue
        contact = dict(RECIPIENT, company_name=job.get("company"))
        for channel in CHANNELS:
            prompt, system = outreach.build(profile, job, contact, channel=channel,
                                            purpose="referral_ask", split=True)
            scrub_prompt(prompt, profile)
            limit = outreach.CHANNEL_LIMITS.get(channel, 2000)
            _, _, _, bodies = outreach.attempts(prompt, system, profile, job, contact,
                                                limit=limit, tries=2, split=True)
            keep = {k: job.get(k) for k in ("id", "title", "company", "track",
                                             "description", "tech_stack")}
            saved.append({"job_id": job_id, "channel": channel, "job": keep,
                          "contact": contact, "first": bodies[0], "after": bodies[-1],
                          "outreach": True})
            print(f"drafted {channel} for {job_id}")
    return saved


def report_outreach(profile, saved):
    """Passing the full check (verify_message, signed as production signs),
    first draft and after one retry; sentence shapes; the passing ones."""
    from jsa import outreach
    from jsa.tailor import FabricationError
    items = [i for i in saved if i.get("outreach")]
    if not items:
        return None
    c = Counter()
    shape = Counter()
    passing = []
    for item in items:
        job, contact = item["job"], item["contact"]
        limit = outreach.CHANNEL_LIMITS.get(item["channel"], 2000)
        for label in ("first", "after"):
            body = outreach.sign(item[label], profile)
            try:
                outreach.verify_message(body, profile, job=job, contact=contact)
                ok = len(body) <= limit
            except FabricationError:
                ok = False
            c[label] += int(ok)
            if ok:
                passing.append((item["job_id"], item["channel"], label, item[label]))
        for s in outreach.checked_sentences(item["first"], profile, job, contact):
            shape[s["kind"]] += 1
    n = len(items)
    print(f"\nOutreach ({n} messages): passing the full check {c['first']}->{c['after']}/{n} "
          "(first draft -> after one retry)")
    print("Sentences in the first drafts: " + ", ".join(
        f"{k} {shape[k]}" for k in ("recipient", "strict")))
    print(f"\nPassing messages: {len(passing)}. Read each; any sentence that says "
          "something about you that isn't in your profile is a leak.\n")
    for job_id, channel, label, body in passing:
        print(f"--- job {job_id}, {channel}, {label} ---\n{body}\n")
    return c, passing, shape


def run_presses(profile, con, rows, max_calls):
    """Production's path, one press at a time, until the call budget is spent."""
    calls = [0]
    inner = llm.recorder

    def counted(*a, **k):
        calls[0] += 1
        return inner(*a, **k) if inner else None

    llm.recorder = counted
    saved = []
    try:
        for job_id, question, reason in rows:
            if calls[0] >= max_calls:
                print(f"stopped at {calls[0]} model calls; {len(rows) - len(saved)} presses not run")
                break
            job = _job(con, job_id)
            if job is None:
                print(f"no job {job_id}; skipped")
                continue
            question = suggest.clean_question(question)
            bullets = answers.evidence(con, job, profile)
            options, _ = suggest.options(profile, job, bullets, question, reason=reason)
            excerpt = suggest.build(job, profile, bullets, question, split=True, reason=reason)[2]
            saved.append({"job_id": job_id, "company": job.get("company"), "question": question,
                          "reason": reason, "job": job, "excerpt": excerpt, "options": options,
                          "press": True})
            print(f"pressed {job_id} ({calls[0]} calls so far): {question[:50]}")
    finally:
        llm.recorder = inner
    return saved, calls[0]


def _own_words(sentence, excerpt, others):
    """Words of a sentence that this listing's excerpt has and no other job's does."""
    from jsa.tailor import _stem, _words
    stems = {_stem(w) for w in _words(sentence)}
    mine = {_stem(w) for w in _words(excerpt)}
    theirs = {_stem(w) for text in others for w in _words(text)}
    return sorted(stems & mine - theirs)


def report_presses(profile, saved):
    """How alike are the answers: within a press, across jobs; is each tied to
    its listing; and every answer in full, for you to read."""
    from difflib import SequenceMatcher

    from jsa import subject
    items = [i for i in saved if i.get("press")]
    if not items:
        return None
    options = [(i, o) for i in items for o in i["options"]]
    drafted = [(i, o) for i, o in options if o["source"] == "model"]
    print(f"\nPresses {len(items)}, options {len(options)}, drafted and checked {len(drafted)}, "
          f"not answers {len(options) - len(drafted)}, presses with no draft "
          f"{sum(1 for i in items if not any(o['source'] == 'model' for o in i['options']))}")
    counts = Counter(sentence for _, o in drafted for sentence in set(subject.split(o["text"])))
    total = sum(counts.values())
    repeated = sum(n for n in counts.values() if n > 1)
    print(f"Sentences: {len(counts)} distinct of {total}; {repeated} sit in a repeated sentence "
          f"({100 * repeated // max(total, 1)}%; the 21 presses before this were 60%)")
    for sentence, n in counts.most_common(5):
        if n > 1:
            print(f"  {n}x {sentence[:110]}")
    same_opening = 0
    for item in items:
        firsts = [" ".join(subject.tokens((subject.split(o["text"]) or [""])[0]))
                  for o in item["options"] if o["source"] == "model"]
        for a in range(len(firsts)):
            for b in range(a + 1, len(firsts)):
                same_opening += int(suggest.overlap(firsts[a], firsts[b]) >= 0.7)
    print(f"Options in one press opening alike: {same_opening}")
    alike = total_pairs = 0
    for a in range(len(drafted)):
        for b in range(a + 1, len(drafted)):
            if drafted[a][0]["job_id"] != drafted[b][0]["job_id"]:
                total_pairs += 1
                alike += int(SequenceMatcher(None, drafted[a][1]["text"], drafted[b][1]["text"])
                             .ratio() >= 0.6)
    print(f"Answers to different jobs at least 60% alike: {alike} of {total_pairs} pairs")
    excerpts = {i["job_id"]: i["excerpt"] for i in items}
    tied = 0
    lines = []
    for item, option in drafted:
        others = [t for j, t in excerpts.items() if j != item["job_id"]]
        sentences = letter.checked_sentences(option["text"], profile, item["job"], item["excerpt"])
        own = sorted({w for s in sentences if s["kind"] == "employer"
                      for w in _own_words(s["text"], item["excerpt"], others)})
        tied += int(bool(own))
        lines.append((item, option, own))
    print(f"Tied to this listing (a sentence about the employer using words only its posting "
          f"has): {tied} of {len(drafted)}")
    print("\nEvery answer, for you to read: any claim about you that isn't in your profile is "
          "a leak.\n")
    for item, option, own in lines:
        print(f"--- {item['company']} | {item['question'][:60]} | {option['angle']} | "
              f"{option['note'][:70]}\n{option['text']}\n    only this listing: {own[:8]}\n")
    for item, option in options:
        if option["source"] != "model":
            print(f"--- NOT AN ANSWER: {item['company']} | {item['question'][:60]}\n"
                  f"    {option['note'][:200]}\n")
    return {"drafted": len(drafted), "same_opening": same_opening, "tied": tied,
            "repeated": repeated, "total": total}


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
    group.add_argument("--presses", help="file of `job_id | question | reason?` (paid calls)")
    group.add_argument("--presses-report", help="a saved --presses file; no model call")
    parser.add_argument("--max-calls", type=int, default=24, help="stop --presses here")
    parser.add_argument("--prompt", choices=["split", "old"], default="split")
    parser.add_argument("--written", default="", help="job ids for written answers, e.g. 421,484")
    parser.add_argument("--letters", default="", help="job ids for cover letters, e.g. 421,484")
    parser.add_argument("--outreach", default="", help="job ids for outreach, e.g. 421,452")
    args = parser.parse_args()
    if not (args.questions or args.questions_from or args.recheck or args.letters
            or args.outreach or args.presses or args.presses_report):
        parser.error("give --questions, --questions-from, --recheck, --letters, --outreach, "
                     "--presses or --presses-report")
    config.load_dotenv()
    profile = load_profile()
    if args.presses_report:
        report_presses(profile, json.loads(Path(args.presses_report).read_text(encoding="utf-8")))
        return 0
    if args.presses:
        rows = []
        for line in Path(args.presses).read_text(encoding="utf-8").splitlines():
            if "|" in line and not line.lstrip().startswith("#"):
                parts = [x.strip() for x in line.split("|")]
                rows.append((int(parts[0]), parts[1], parts[2] if len(parts) > 2 else ""))
        con = db.connect(config.DB_PATH)
        previous = ledger.install(config.DB_PATH)
        try:
            saved, used = run_presses(profile, con, rows, args.max_calls)
        finally:
            llm.recorder = previous
            con.close()
        out = config.HOME / "logs" / f"suggest-presses-{dt.datetime.now():%Y-%m-%d-%H%M%S}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(saved, indent=1), encoding="utf-8")
        print(f"{used} model calls. Saved to {out} (gitignored; yours, never committed)")
        report_presses(profile, saved)
        return 0
    if args.recheck:
        saved = json.loads(Path(args.recheck).read_text(encoding="utf-8"))
        plain = [i for i in saved if not i.get("letter") and not i.get("outreach")]
        if plain:
            report(profile, plain)
        report_letters(profile, saved)
        report_outreach(profile, saved)
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
        saved += draft_outreach(profile, con,
                                [int(x) for x in args.outreach.split(",") if x.strip()])
    finally:
        llm.recorder = previous
        con.close()
    out = config.HOME / "logs" / f"verifier-measure-{dt.date.today().isoformat()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out = out.with_name(out.stem + f"-{dt.datetime.now():%H%M%S}.json")
    out.write_text(json.dumps(saved, indent=1), encoding="utf-8")
    print(f"saved the drafts to {out} (gitignored; yours, never committed)")
    plain = [i for i in saved if not i.get("letter") and not i.get("outreach")]
    if plain:
        report(profile, plain)
    report_letters(profile, saved)
    report_outreach(profile, saved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
