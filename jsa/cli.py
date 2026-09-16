"""Command line entry point.

    python -m jsa init                 create the tracker database
    python -m jsa verify [--write]     probe every feed in companies.yaml
    python -m jsa discover [--min 0.35] [--all]
    python -m jsa matches [--limit 20]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

from . import approvals, db, discover
from .config import COMPANIES_PATH, DB_PATH, ConfigError, Preferences, load_profile


def load_regions() -> dict[str, list[str]]:
    """Commute regions come from the profile, not from this file.

    Defined under job_search_preferences.regions as name -> city substrings.
    Returns {} when the profile is missing or defines none, so `--near` simply
    offers no choices rather than failing.
    """
    try:
        return Preferences.from_profile(load_profile()).regions
    except Exception:  # noqa: BLE001 — argparse setup must not hard-fail
        return {}


def region_clause(regions: dict[str, list[str]], region: str) -> str:
    cities = regions.get(region) or []
    if not cities:
        return "1=1"
    # Values come from the user's own profile file, but quote them anyway
    # rather than interpolating raw text into SQL.
    escaped = [c.replace("'", "''") for c in cities]
    return " OR ".join(f"m.location LIKE '%{c}%'" for c in escaped)


def cmd_init(args: argparse.Namespace) -> int:
    path = db.init_db()
    print(f"tracker ready at {path}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    reports = discover.verify_sources()
    width = max(len(r.company) for r in reports)
    working = []
    for r in reports:
        flag = "ok " if r.status == "ok" else "   "
        print(f"{flag} {r.company:<{width}}  {r.kind:<10} {r.fetched:>4} jobs  {r.status}")
        if r.status == "ok" and r.fetched > 0:
            working.append(r.company)

    print(f"\n{len(working)}/{len(reports)} feeds returned listings.")

    if args.write:
        data = yaml.safe_load(COMPANIES_PATH.read_text(encoding="utf-8"))
        by_company = {r.company: r for r in reports}
        for entry in data["sources"]:
            r = by_company.get(entry["company"])
            entry["verified"] = bool(r and r.status == "ok" and r.fetched > 0)
        COMPANIES_PATH.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        print(f"updated `verified` flags in {COMPANIES_PATH.name}")
        print("NOTE: this rewrite drops the comments from companies.yaml.")
    else:
        print("re-run with --write to record these results in companies.yaml")
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    if not DB_PATH.exists():
        print(f"no tracker at {DB_PATH} — run `python -m jsa init` first", file=sys.stderr)
        return 1
    reports = discover.discover(min_score=args.min, only_verified=not args.all)
    width = max(len(r.company) for r in reports)
    total_new = 0
    for r in reports:
        print(
            f"  {r.company:<{width}}  fetched {r.fetched:>4}  kept {r.kept:>3}  "
            f"new {r.new:>3}  filtered {r.rejected:>4}  {r.status}"
        )
        total_new += r.new
    print(f"\n{total_new} new listing(s) above score {args.min}. "
          f"See them with `python -m jsa matches`.")

    # A transient failure on one large feed can halve the result set, and the
    # FAIL line scrolls past unnoticed among 45 sources. Repeat them at the end
    # with the listings they would have contributed, so a short run is
    # obviously a short run rather than a quiet one.
    failed = [r for r in reports if r.status.startswith("FAIL")]
    if failed:
        print(f"\n!! {len(failed)} feed(s) FAILED this run — results are incomplete:")
        for r in failed:
            print(f"     {r.company}: {r.status}")
        print("   Re-run `discover`, or `verify` to check whether the feed moved.")
        return 1
    return 0


def cmd_matches(args: argparse.Namespace) -> int:
    # One large board (SpaceX posts 2,300 reqs) otherwise fills the whole page
    # and buries every other company. Cap per company, then take the top N.
    clauses = []
    if args.near:
        clauses.append(f"({region_clause(load_regions(), args.near)})")
    if args.remote:
        clauses.append("m.remote = 'remote'")
    if args.track:
        clauses.append(f"m.track = '{args.track}'")
    where = " AND ".join(clauses) or "1=1"

    sql = f"""
        WITH capped AS (
            SELECT m.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY m.company ORDER BY m.match_score DESC
                   ) AS company_rank
              FROM v_new_matches m
             WHERE {where}
        )
        SELECT * FROM capped
         WHERE company_rank <= :per_company
         ORDER BY match_score DESC
         LIMIT :limit
    """

    con = db.connect()
    try:
        rows = con.execute(
            sql, {"per_company": args.per_company, "limit": args.limit}
        ).fetchall()
    finally:
        con.close()

    if not rows:
        print("no unreviewed matches")
        return 0

    for row in rows:
        score = row["match_score"] or 0.0
        variants = row["variant_count"] or 1
        extra = f"  (+{variants - 1} more location{'s' if variants > 2 else ''})" if variants > 1 else ""
        tag = "  [sales track]" if row["track"] == "sales" else ""
        print(f"\n[{score:.2f}] {row['company']} — {row['title']}{extra}{tag}")
        print(f"       {row['location'] or 'location not stated'} ({row['remote']})")
        print(f"       {row['url']}")
        flags = []
        if row["degree_required"] == 1:
            flags.append("DEGREE REQUIRED")
        if row["clearance_required"] == 1:
            flags.append("CLEARANCE REQUIRED")
        if row["years_required"] is not None:
            flags.append(f"{row['years_required']}+ yrs")
        if flags:
            print(f"       ⚑ {'  |  '.join(flags)}")
        stack = json.loads(row["tech_stack"] or "[]")
        if stack:
            print(f"       stack: {', '.join(stack)}")
        if row["enrichment_note"]:
            print(f"       note: {row['enrichment_note']}")
        for reason in json.loads(row["match_reasons"] or "[]"):
            print(f"       · {reason}")
    print(f"\n{len(rows)} match(es).")
    return 0


def cmd_enrich(args: argparse.Namespace) -> int:
    from . import enrich, llm

    if not DB_PATH.exists():
        print(f"no tracker at {DB_PATH} — run `python -m jsa init` first", file=sys.stderr)
        return 1
    try:
        llm.api_key()
    except llm.LLMError as exc:
        print(exc, file=sys.stderr)
        return 2

    print(f"enriching up to {args.limit} listing(s) scoring >= {args.min}"
          f"{' (forced re-run)' if args.force else ''}...")
    from .log import RunMetrics
    metrics = RunMetrics()
    report = enrich.run(min_score=args.min, limit=args.limit,
                        force=args.force, workers=args.workers,
                        metrics=metrics)

    if not report.attempted:
        print("nothing pending — everything above that score is already enriched.")
        return 0

    print(f"\n  attempted           : {report.attempted}")
    print(f"  enriched            : {report.succeeded}")
    print(f"  failed              : {report.failed}")
    for e in report.errors:
        print(f"      {e}")
    if report.succeeded:
        print(f"\n  require a degree    : {report.degree_required}"
              f"  ({100*report.degree_required/report.succeeded:.0f}%)")
        print(f"  require a clearance : {report.clearance_required}")
        print("\nThese are flagged, not filtered — a degree line is often boilerplate.")
        print("See them with `python -m jsa matches`.")
    return 0 if report.succeeded or not report.failed else 1


def cmd_serve(args: argparse.Namespace) -> int:
    from . import web

    if not DB_PATH.exists():
        print(f"no tracker at {DB_PATH} - run `python -m jsa init` first", file=sys.stderr)
        return 1
    web.serve(host=args.host, port=args.port)
    return 0


def cmd_env(args: argparse.Namespace) -> int:
    """Report config state without ever printing a secret."""
    from .config import ENV_PATH
    from . import llm

    print(f".env file:  {ENV_PATH}")
    print(f"            {'exists' if ENV_PATH.exists() else 'NOT FOUND'}")

    key = os.environ.get(llm.API_KEY_ENV, "")
    if not key:
        print(f"\n{llm.API_KEY_ENV}: NOT SET")
        print(f"  Put it in {ENV_PATH.name}, or set it in your shell.")
        return 1
    if key.startswith("<"):
        print(f"\n{llm.API_KEY_ENV}: still the placeholder — replace "
              f"<paste-your-nvapi-key-here> with the real key.")
        return 1

    # Show only enough to tell two keys apart. Never the whole value.
    masked = f"{key[:9]}...{key[-4:]}" if len(key) > 16 else "(short)"
    shape = "looks like an nvapi key" if key.startswith("nvapi-") else "UNEXPECTED FORMAT"
    print(f"\n{llm.API_KEY_ENV}: set  [{masked}]  {len(key)} chars, {shape}")
    print(f"base URL:   {llm.BASE_URL}")
    print("\nCheck it works with: python -m jsa models")
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    from . import llm

    try:
        results = llm.probe_models()
    except llm.LLMError as exc:
        print(exc, file=sys.stderr)
        return 2

    working = [m for m, ok, _ in results if ok]
    for model, ok, detail in results:
        print(f"  {'ok  ' if ok else '--  '} {model:<44} {detail[:60]}")
    print(f"\n{len(working)}/{len(results)} callable on this account.")
    if not working:
        print("No models available — check the key, or the account's model access.")
        return 1
    print(f"Chain will use: {working[0]}")
    return 0


# --- tailoring ---------------------------------------------------------------
# See docs/decisions/0003-application-lifecycle.md. The document is the unit
# that gets approved, per version, so every redraft is a new row, a new file
# and a new pending decision.

KIND_ARG = {"resume": "resume", "cover-letter": "cover_letter"}


def _cover_body(draft) -> str:
    """Compose the letter from the ALREADY-VERIFIED draft.

    Deliberately no second model call. Every sentence below has passed
    verify_draft; asking a model for fresh prose here would open a fabrication
    surface that nothing downstream checks.
    """
    parts = [draft.summary] + [b.text for b in draft.bullets[:3]]
    return "\n\n".join(part for part in parts if part and part.strip())


def cmd_tailor(args: argparse.Namespace) -> int:
    from . import render
    from .tailor import FabricationError, IdentityLeakError, UndecidedPreferenceError
    from .tailor import tailor as build_draft

    kind = KIND_ARG[args.kind]
    con = db.connect()
    try:
        job = con.execute(
            "SELECT j.*, c.name AS company FROM jobs j "
            "LEFT JOIN companies c ON c.id = j.company_id WHERE j.id = ?",
            (args.job_id,),
        ).fetchone()
        if job is None:
            print(f"error: no job with id {args.job_id}", file=sys.stderr)
            return 1
        job = dict(job)

        # ADR 0003 decision 1: an application is created explicitly.
        application_id = approvals.require_application(con, args.job_id)

        existing = render.next_version(con, args.job_id, kind) - 1
        if existing and not args.force:
            print(f"error: {kind} v{existing} already exists for job "
                  f"{args.job_id}. Re-run with --force to draft v{existing + 1}.",
                  file=sys.stderr)
            return 1

        profile = load_profile()
        full = len(job.get("description") or "")
        # Tag rarity is learned from the postings already in the tracker, so a
        # word appearing in 87% of them cannot outweigh one appearing in 3%.
        from .tailor import collect_bullets, tag_weights
        weights = tag_weights(
            con, {t for b in collect_bullets(profile).values() for t in b.tags})
        draft = build_draft(job, profile, weights=weights)

        version = render.next_version(con, args.job_id, kind)
        out = render.output_path(job.get("company") or "unknown",
                                 job.get("title") or "role", kind, version)
        out.parent.mkdir(parents=True, exist_ok=True)
        if kind == "resume":
            render.render_resume(draft, profile, job, out)
        else:
            render.render_cover_letter(draft, profile, job, _cover_body(draft), out)

        document_id = render.record(
            con, job_id=args.job_id, kind=kind, path=out, draft=draft,
            prompt_hash=draft.prompt_hash,
        )
        approvals.set_document_pointer(con, application_id, kind, document_id)
        approvals.record_event(
            con, application_id, "ready", actor="agent",
            note=f"{kind} v{version} drafted, awaiting approval")
        approval_id = approvals.queue(
            con, "document", document_id,
            f"{kind} v{version} for {job.get('title')} at {job.get('company')}")
        con.commit()
    except (approvals.ApprovalError, UndecidedPreferenceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (FabricationError, IdentityLeakError) as exc:
        # The guard fired. That is the system working, not a crash.
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()

    from .tailor import MAX_DESCRIPTION_CHARS
    if full > MAX_DESCRIPTION_CHARS:
        # Silent truncation is how a draft ends up ignoring a requirement that
        # was stated in the part the model never saw.
        print(f"note: posting is {full:,} chars; the model saw the first "
              f"{MAX_DESCRIPTION_CHARS:,}", file=sys.stderr)
    print(f"wrote {out}")
    print(f"  document {document_id} v{version}  model {draft.model}")
    print(f"  bullets  {', '.join(b.source_id for b in draft.bullets)}")
    if draft.keywords_missing:
        print(f"  gaps     {', '.join(draft.keywords_missing)}")
    if draft.reverted:
        # The rewrite drifted far enough to be making a different claim, so the
        # profile text was used instead. Said out loud, never silently.
        print(f"  reverted {', '.join(draft.reverted)} "
              f"(rewrite drifted; profile text used)")
    print(f"  approve  jsa approve {approval_id}")
    return 0


# --- application lifecycle and approval -------------------------------------
# See docs/decisions/0003-application-lifecycle.md. These commands, and the web
# routes that call the same approvals functions, are the only ways a decision
# reaches decided_by='human'.


def _job_line(con, job_id: int) -> str:
    row = con.execute(
        "SELECT j.title, c.name FROM jobs j "
        "LEFT JOIN companies c ON c.id = j.company_id WHERE j.id = ?",
        (int(job_id),),
    ).fetchone()
    if row is None:
        return f"job {job_id}"
    return f"{row['title']} at {row['name'] or 'unknown company'}"


def cmd_save(args: argparse.Namespace) -> int:
    con = db.connect()
    try:
        application_id, created = approvals.save_application(
            con, args.job_id, note=args.note)
        con.commit()
        line = _job_line(con, args.job_id)
    except approvals.ApprovalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()
    verb = "saved" if created else "already saved"
    print(f"{verb}: application {application_id} -- {line}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    con = db.connect()
    try:
        items = approvals.pending(con)
        if args.approval_id:
            items = [i for i in items if i.approval_id == args.approval_id]
            if not items:
                print(f"no pending approval with id {args.approval_id}",
                      file=sys.stderr)
                return 1
        if not items:
            print("nothing awaiting approval")
            return 0
        for item in items:
            print(f"[{item.approval_id}] {item.subject_type} {item.subject_id}"
                  f"  requested {item.requested_at}")
            print(f"     {item.summary}")
            if args.approval_id:
                print(f"     approve:  jsa approve {item.approval_id}")
                print(f"     reject :  jsa reject {item.approval_id} "
                      '--feedback "what to change"')
    finally:
        con.close()
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    con = db.connect()
    try:
        approvals.approve(con, args.approval_id, args.note)
        con.commit()
    except approvals.ApprovalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()
    print(f"approved {args.approval_id}")
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    con = db.connect()
    try:
        approvals.reject(con, args.approval_id, args.feedback)
        con.commit()
    except approvals.ApprovalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()
    print(f"rejected {args.approval_id}")
    return 0


def cmd_applied(args: argparse.Namespace) -> int:
    con = db.connect()
    try:
        application_id, approved = approvals.mark_applied(
            con, args.job_id, when=args.date)
        con.commit()
        line = _job_line(con, args.job_id)
    except approvals.ApprovalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()
    print(f"marked applied: application {application_id} -- {line}")
    if not approved:
        # Not a refusal. The tracker records what happened; see ADR 0003 #4.
        print("note: no approved document on file for this job.",
              file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jsa", description=__doc__)
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="log per-call model, token and latency detail to stderr")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the tracker database").set_defaults(func=cmd_init)

    p_verify = sub.add_parser("verify", help="probe every feed in companies.yaml")
    p_verify.add_argument(
        "--write", action="store_true", help="record results back into companies.yaml"
    )
    p_verify.set_defaults(func=cmd_verify)

    p_disc = sub.add_parser("discover", help="poll feeds and score listings")
    p_disc.add_argument("--min", type=float, default=0.35, help="minimum match score")
    p_disc.add_argument(
        "--all", action="store_true", help="include feeds not yet verified"
    )
    p_disc.set_defaults(func=cmd_discover)

    p_match = sub.add_parser("matches", help="show unreviewed matches")
    p_match.add_argument("--limit", type=int, default=20)
    p_match.add_argument(
        "--per-company",
        type=int,
        default=3,
        help="max rows per company, so one large board can't fill the page",
    )
    _regions = load_regions()
    p_match.add_argument(
        "--near",
        choices=sorted(_regions) or None,
        metavar="REGION",
        help=(
            "only roles within commuting range of a region defined in your "
            "profile under job_search_preferences.regions"
            + (f" (available: {', '.join(sorted(_regions))})" if _regions else
               " (none defined yet)")
        ),
    )
    p_match.add_argument(
        "--remote", action="store_true", help="only fully remote roles"
    )
    p_match.add_argument(
        "--track",
        choices=["engineering", "sales"],
        help="engineering (tier 1) or sales (tier 2, ranked below by default)",
    )
    p_match.set_defaults(func=cmd_matches)

    p_enrich = sub.add_parser(
        "enrich", help="use an LLM to extract degree/clearance/stack facts")
    p_enrich.add_argument("--min", type=float, default=0.5,
                          help="only enrich listings scoring at least this")
    p_enrich.add_argument("--limit", type=int, default=200)
    p_enrich.add_argument("--workers", type=int, default=4)
    p_enrich.add_argument("--force", action="store_true",
                          help="re-enrich even if already done")
    p_enrich.set_defaults(func=cmd_enrich)

    p_serve = sub.add_parser("serve", help="local review dashboard")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.set_defaults(func=cmd_serve)

    p_tail = sub.add_parser("tailor", help="draft a resume or cover letter")
    p_tail.add_argument("job_id", type=int)
    p_tail.add_argument("--kind", choices=sorted(KIND_ARG), default="resume")
    p_tail.add_argument(
        "--force", action="store_true",
        help="draft a new version when one already exists")
    p_tail.set_defaults(func=cmd_tailor)

    p_save = sub.add_parser("save", help="track a match as an application")
    p_save.add_argument("job_id", type=int)
    p_save.add_argument("--note")
    p_save.set_defaults(func=cmd_save)

    p_rev = sub.add_parser("review", help="list what is awaiting your decision")
    p_rev.add_argument("approval_id", type=int, nargs="?")
    p_rev.set_defaults(func=cmd_review)

    p_app = sub.add_parser("approve", help="record YOUR approval")
    p_app.add_argument("approval_id", type=int)
    p_app.add_argument("--note")
    p_app.set_defaults(func=cmd_approve)

    p_rej = sub.add_parser("reject", help="record YOUR rejection, with feedback")
    p_rej.add_argument("approval_id", type=int)
    p_rej.add_argument(
        "--feedback", required=True,
        help="what to change; a redraft has nothing to work from without it")
    p_rej.set_defaults(func=cmd_reject)

    p_done = sub.add_parser("applied", help="record that YOU submitted it")
    p_done.add_argument("job_id", type=int)
    p_done.add_argument("--date", help="ISO timestamp; defaults to now")
    p_done.set_defaults(func=cmd_applied)

    sub.add_parser(
        "env", help="show where config and the API key are coming from"
    ).set_defaults(func=cmd_env)

    sub.add_parser(
        "models", help="check which LLM models this account can actually call"
    ).set_defaults(func=cmd_models)

    args = parser.parse_args(argv)
    from .log import configure
    configure(getattr(args, "verbose", False))
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
