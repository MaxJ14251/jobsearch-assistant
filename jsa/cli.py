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


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report what will not work yet. Reads only; changes nothing."""
    from . import doctor

    try:
        profile = load_profile()
    except ConfigError:
        profile = None

    con = db.connect() if DB_PATH.exists() else None
    try:
        report = doctor.run(profile, con)
    finally:
        if con is not None:
            con.close()

    if report.blocking:
        print(f"{len(report.blocking)} thing(s) to fix before this works:\n")
        for finding in report.blocking:
            print(f"  x {finding.what}")
            print(f"      {finding.fix}")
            if finding.where:
                print(f"      in {finding.where}")
            print()
    if report.advisory:
        print(f"{len(report.advisory)} thing(s) worth knowing:\n")
        for finding in report.advisory:
            print(f"  - {finding.what}")
            print(f"      {finding.fix}")
            print()
    print("checked: " + ", ".join(report.checked))
    if report.ok:
        print("\nNothing is stopping you. Next: jsa discover, then jsa matches.")
    else:
        print("\nFix the items marked x, then run this again. "
              "This command changed nothing.")
    return 0 if report.ok else 1


def cmd_import_resume(args: argparse.Namespace) -> int:
    """Resume (.docx) -> a DRAFT profile, plus what it would match. Never
    writes master_profile.yaml (ADR 0022)."""
    from . import config, doctor
    from . import resume_import as ri

    live = config.PROFILE_PATH
    out = Path(args.out) if args.out else live.with_name("master_profile.draft.yaml")
    if out.name == live.name or out.resolve() == live.resolve():
        print(f"refused: {out} is your live profile. The import only ever "
              "writes a draft; copying it over is yours to do.", file=sys.stderr)
        return 2
    if out.exists() and not args.force:
        print(f"refused: {out} already exists. Review it, or run again with "
              "--force to replace the draft.", file=sys.stderr)
        return 2

    lines = ri.read_docx(Path(args.file))
    ident, redacted = ri.split_identity(lines)
    extracted = ri.extract(redacted, ident)
    verified = ri.verify(extracted, "\n".join(redacted))
    ri.write_draft(out, ident, verified, Path(args.file).name)

    print(f"wrote {out}")
    print(f"  imported {len(verified.experience)} job(s), "
          f"{len(verified.projects)} project(s), {verified.bullet_count} bullet(s), "
          f"{len(verified.certifications)} certification(s), "
          f"{len(verified.education)} school(s), {len(verified.skills)} skill(s)")
    if verified.dropped:
        print(f"  dropped {len(verified.dropped)} item(s) the model did not "
              "copy exactly (nothing is reworded into your profile):")
        for d in verified.dropped:
            print(f"    - {d.where}: {d.what[:70]!r} -- {d.why}")

    draft = yaml.safe_load(out.read_text(encoding="utf-8"))
    con = db.connect() if DB_PATH.exists() else None
    try:
        report = doctor.run(draft, con)
        if report.blocking:
            print(f"\nfill these in, in the draft ({len(report.blocking)}):")
            for finding in report.blocking:
                print(f"  x {finding.what}")
                print(f"      {finding.fix}")
        print("\nwhat the draft would match:")
        if con is None:
            print("  no tracker yet: run `jsa init` and `jsa discover` first.")
        else:
            try:
                top = ri.preview(con, draft)
            except ConfigError as exc:
                print(f"  no preview yet: {exc}")
            else:
                print("  (from jobs already in your tracker, which were found "
                      "using your current profile;\n   run `jsa discover` after "
                      "adopting for a full search)")
                for m in top:
                    print(f"  {m.score:.2f}  #{m.job_id} {m.company} -- {m.title}")
                if not top:
                    print("  nothing in the tracker scores above 0 for this draft.")
    finally:
        if con is not None:
            con.close()

    print("\nnext:")
    print(f"  1. review {out} (TODOs and `# suggested: check` lines)")
    print(f"  2. copy it to {live}")
    print("  3. jsa doctor")
    print("  4. jsa discover")
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
        dupes = f"  dup {r.duplicate:>3}" if r.duplicate else ""
        print(
            f"  {r.company:<{width}}  fetched {r.fetched:>4}  kept {r.kept:>3}  "
            f"new {r.new:>3}  filtered {r.rejected:>4}{dupes}  {r.status}"
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


def cmd_rescore(args: argparse.Namespace) -> int:
    """Re-read pay and re-score stored listings, without polling any feed."""
    if not DB_PATH.exists():
        print(f"no tracker at {DB_PATH} - run `python -m jsa init` first", file=sys.stderr)
        return 1
    prefs = Preferences.from_profile(load_profile())
    con = db.connect()
    try:
        report = discover.rescore(con, prefs)
        con.commit()
    finally:
        con.close()
    print(f"rescored {report.jobs} listing(s): pay found in {report.with_pay}, "
          f"score changed for {report.changed}")
    if report.rejected_by_floor:
        print(f"  {report.rejected_by_floor} now score 0: pay below your floor")
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
    search = (getattr(args, "search", None) or "").strip()
    if search:
        # The dashboard's search box, same rules (Plan 7).
        clauses.append("title_matches(m.title, :q) = 1")
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
    if search:
        from .scoring import title_matches
        con.create_function("title_matches", 2, title_matches, deterministic=True)
    try:
        rows = con.execute(
            sql, {"per_company": args.per_company, "limit": args.limit, "q": search}
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
        # The job number is what `save`, `tailor` and `applied` take.
        print(f"\n[{score:.2f}] #{row['job_id']} {row['company']} — {row['title']}{extra}{tag}")
        print(f"       {row['location'] or 'location not stated'} ({row['remote']})")
        print(f"       {row['url']}")
        flags = []
        if row["degree_required"] == 1:
            flags.append("asks for a degree")
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
    print(f"\n{len(rows)} match(es). Save one by its number: jsa save <#>")
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

    if args.job:
        print(f"re-enriching job(s) {', '.join(map(str, args.job))}...")
    else:
        print(f"enriching up to {args.limit} listing(s) scoring >= {args.min}"
              f"{' (forced re-run)' if args.force else ''}...")
    from .log import RunMetrics
    metrics = RunMetrics()
    report = enrich.run(min_score=args.min, limit=args.limit,
                        force=args.force, workers=args.workers,
                        metrics=metrics, job_ids=args.job or None)

    if not report.attempted:
        if args.job:
            print("no such posting, or it has no description to read: "
                  f"{', '.join(map(str, args.job))}", file=sys.stderr)
            return 1
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


def cmd_tags(args: argparse.Namespace) -> int:
    """Report which of your tags match real postings. Never edits the profile."""
    from .tailor import tag_report, tag_weights, vocabulary

    profile = load_profile()
    con = db.connect()
    try:
        corpus_size = con.execute(
            "SELECT COUNT(*) FROM jobs WHERE description IS NOT NULL"
        ).fetchone()[0]
        weights = tag_weights(con, vocabulary(profile))
    finally:
        con.close()
    if not weights:
        print("not enough postings to measure tags yet -- run `jsa discover` first")
        return 0

    rows = tag_report(profile, weights, corpus_size)
    dead = [r for r in rows if r["dead"]]
    print(f"{len(rows)} tags measured against {corpus_size} postings\n")
    for row in rows:
        reach = row["postings"]
        shown = f"{reach:>4}" if reach >= 0 else "85%+"
        mark = "DEAD" if row["dead"] else "    "
        line = f"  {mark} {shown}  {row['tag']:<22} {', '.join(row['bullets'])}"
        if row["revived_by"]:
            parts = ", ".join(f"{w} ({n})" for w, n in row["revived_by"])
            line += f"\n                 matched instead through: {parts}"
        print(line)

    still_dead = [r["tag"] for r in dead if not r["revived_by"]]
    print(f"\n{len(dead)} dead as written; {len(still_dead)} still unmatched "
          "after component fallback.")
    if still_dead:
        print("No posting uses these words. Consider rewording them in "
              "profile/master_profile.yaml in the language postings use:")
        for tag in still_dead:
            print(f"  - {tag}")
    return 0


# --- tailoring ---------------------------------------------------------------
# See docs/decisions/0003-application-lifecycle.md. The document is the unit
# that gets approved, per version, so every redraft is a new row, a new file
# and a new pending decision.

KIND_ARG = {"resume": "resume", "cover-letter": "cover_letter"}


def cmd_tailor(args: argparse.Namespace) -> int:
    from .drafting import DraftError, draft_document

    kind = KIND_ARG[args.kind]
    con = db.connect()
    try:
        result = draft_document(con, args.job_id, kind, force=args.force,
                                profile=load_profile())
    except DraftError as exc:
        # A refusal is a guard firing. That is the system working, not a crash.
        print(f"{'refused' if exc.refused else 'error'}: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()

    # Silent truncation is how a draft ends up ignoring a requirement that was
    # stated in the part the model never saw. Since n14 this fires on 0.3% of
    # postings rather than 92% of them, which is what makes it worth reading.
    if result.description_note:
        print(f"note: {result.description_note}", file=sys.stderr)
    if result.thin_note:
        print(f"warning: {result.thin_note}", file=sys.stderr)
    print(f"wrote {result.path}")
    print(f"  document {result.document_id} v{result.version}  model {result.model}")
    # Printed so a misclassified title is visible on every run. ADR 0005.
    print(f"  role     {result.role}")
    print(f"  bullets  {', '.join(result.bullet_ids)}")
    if result.gaps:
        print(f"  gaps     {', '.join(result.gaps)}")
    if result.note:
        # A letter that fell back to composed sentences says so, here and on
        # the review page. Never let a template read as drafted prose.
        print(f"  letter   {result.note}")
    for bullet_id, why in result.revert_reasons.items():
        # The rewrite said something the profile does not, so the profile's own
        # words were used instead. Said out loud, never silently.
        print(f"  reverted {bullet_id}: {why}")
    if result.superseded:
        # Closed by the tool, not by you. Said plainly so the queue shrinking
        # is never mistaken for someone having decided something.
        ids = ", ".join(str(i) for i in result.superseded)
        print(f"  closed   approval(s) {ids} — superseded by v{result.version}")
    if result.prior_feedback:
        # The point of the whole exercise. This was written, stored, shown on a
        # page nobody had open, and read by nothing at the moment it mattered.
        print("  you rejected earlier versions of this document:")
        for version, note in result.prior_feedback:
            print(f"    v{version}: {note}")
        print("  (read as a person, not fed to the model — this tool does not "
              "tune itself on your notes)")
    print(f"  approve  jsa approve {result.approval_id}")
    return 0


# --- contacts and outreach ---------------------------------------------------
# See docs/decisions/0004-outreach.md. Nothing here transmits anything. The
# command that records a send is called mark-sent precisely so no reader can
# mistake it for one that sends.


def cmd_contact_add(args: argparse.Namespace) -> int:
    from . import outreach as out
    con = db.connect()
    try:
        company_id = None
        if args.company:
            row = con.execute(
                "SELECT id FROM companies WHERE name = ? COLLATE NOCASE "
                "OR slug = ? COLLATE NOCASE", (args.company, args.company),
            ).fetchone()
            if row is None:
                print(f"error: no company matching {args.company!r}. "
                      "Use a name or slug already in the tracker.",
                      file=sys.stderr)
                return 1
            company_id = int(row["id"])
        contact_id = out.add_contact(
            con, name=args.name, company_id=company_id, title=args.title,
            linkedin_url=args.linkedin, email=args.email,
            relationship=args.relationship, notes=args.notes,
        )
        con.commit()
    except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()
    print(f"contact {contact_id}: {args.name}")
    return 0


def cmd_contacts(args: argparse.Namespace) -> int:
    con = db.connect()
    try:
        rows = con.execute(
            "SELECT c.*, co.name AS company FROM contacts c "
            "LEFT JOIN companies co ON co.id = c.company_id "
            "WHERE c.archived_at IS NULL ORDER BY c.id"
        ).fetchall()
    finally:
        con.close()
    if not rows:
        print("no contacts yet")
        return 0
    for row in rows:
        print(f"[{row['id']}] {row['name']}  {row['title'] or '-'}  "
              f"@ {row['company'] or '-'}  ({row['relationship'] or 'cold'})")
    return 0


def cmd_outreach_draft(args: argparse.Namespace) -> int:
    from . import outreach as out
    from .tailor import FabricationError
    con = db.connect()
    try:
        profile = load_profile()
        result = out.draft(
            con, profile, contact_id=args.contact, channel=args.channel,
            purpose=args.purpose, job_id=args.job,
        )
        outreach_id = con.execute(
            "SELECT MAX(id) FROM outreach WHERE contact_id = ?", (args.contact,)
        ).fetchone()[0]
        con.commit()
    except FabricationError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    except out.OutreachError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()
    if result.thin_note:
        print(f"warning: {result.thin_note}", file=sys.stderr)
    print(f"outreach {outreach_id} drafted ({args.channel}, {args.purpose}, "
          f"{len(result.body)} chars)")
    print("  NOT SENT. This tool never transmits. Read it, approve it, then")
    print("  send it yourself and record that with mark-sent.")
    print(f"  read:     jsa outreach show {outreach_id}")
    return 0


def cmd_outreach_show(args: argparse.Namespace) -> int:
    con = db.connect()
    try:
        row = con.execute(
            "SELECT o.*, c.name AS contact FROM outreach o "
            "JOIN contacts c ON c.id = o.contact_id WHERE o.id = ?",
            (args.outreach_id,),
        ).fetchone()
        approval = con.execute(
            "SELECT id, decision FROM approvals WHERE subject_type = 'outreach' "
            "AND subject_id = ? ORDER BY id DESC LIMIT 1", (args.outreach_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        print(f"error: no outreach with id {args.outreach_id}", file=sys.stderr)
        return 1
    print(f"to {row['contact']} via {row['channel']} "
          f"({row['purpose']}, {row['status']}, {len(row['draft_body'])} chars)")
    print()
    print(row["draft_body"])
    print()
    if approval and approval["decision"] == "pending":
        print(f"approve:  jsa approve {approval['id']}")
    return 0


def cmd_outreach_mark_sent(args: argparse.Namespace) -> int:
    from . import outreach as out
    con = db.connect()
    try:
        out.mark_sent(con, args.outreach_id)
        con.commit()
    except out.OutreachError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()
    print(f"recorded: you sent outreach {args.outreach_id}")
    return 0


# --- interview prep ----------------------------------------------------------


def cmd_prep(args: argparse.Namespace) -> int:
    from . import prep as prep_mod
    from .tailor import UndecidedPreferenceError, require_decided_preferences

    con = db.connect()
    try:
        row = con.execute(
            "SELECT a.id, j.title, j.description, c.name AS company "
            "FROM applications a JOIN jobs j ON j.id = a.job_id "
            "JOIN companies c ON c.id = j.company_id WHERE a.id = ?",
            (args.application_id,),
        ).fetchone()
        if row is None:
            print(f"error: no application with id {args.application_id}. "
                  "Run `jsa review` or `jsa save <job_id>` first.", file=sys.stderr)
            return 1

        profile = load_profile()
        require_decided_preferences(profile)
        description = row["description"] or ""
        result = prep_mod.generate(
            con, args.application_id, round=args.round, profile=profile)
        prep_id = con.execute(
            "SELECT MAX(id) FROM interview_prep WHERE application_id = ?",
            (args.application_id,),
        ).fetchone()[0]
        con.commit()
    except prep_mod.DegreeClaimError as exc:
        # The guard fired. That is the system working, not a crash.
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    except UndecidedPreferenceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()

    from . import posting
    said = posting.note(posting.visible(description))
    if said:
        print(f"note: {said}", file=sys.stderr)
    if result.thin_note:
        print(f"warning: {result.thin_note}", file=sys.stderr)
    print(f"prep {prep_id} for application {args.application_id} "
          f"({row['title']} at {row['company']}, {args.round})")
    print(f"  {len(result.questions)} question(s)  model {result.model}")
    print(f"  drilled from {result.drilled_from}")
    for question in result.questions[:2]:
        # The two standard drills lead the list; they are the ones that come up
        # in every screen, so they are the ones worth seeing without opening
        # anything.
        print(f"  - {question.question}")
    print(f"  read it:  jsa prep-show {prep_id}")
    return 0


def cmd_prep_show(args: argparse.Namespace) -> int:
    import json as _json
    con = db.connect()
    try:
        row = con.execute(
            "SELECT * FROM interview_prep WHERE id = ?", (args.prep_id,)
        ).fetchone()
    finally:
        con.close()
    if row is None:
        print(f"error: no prep with id {args.prep_id}", file=sys.stderr)
        return 1
    if row["company_brief"]:
        print("COMPANY BRIEF")
        print(f"  {row['company_brief']}")
        print()
    for i, q in enumerate(_json.loads(row["questions"] or "[]"), 1):
        print(f"{i}. {q.get('question', '')}")
        if q.get("why"):
            print(f"   why: {q['why']}")
        if q.get("answer_notes"):
            print(f"   say: {q['answer_notes']}")
        print()
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


def cmd_add(args: argparse.Namespace) -> int:
    """Bring in one job discovery did not find. Reads a public posting; sends nothing."""
    from . import intake

    if bool(args.url) == bool(args.paste):
        print("error: give a link, or --paste with --company and --title.",
              file=sys.stderr)
        return 1
    try:
        prefs = Preferences.from_profile(load_profile())
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    # Writes a job row, so bring an older tracker up to the schema first,
    # as discovery does (description_origin, ADR 0020).
    db.upgrade()
    con = db.connect()
    try:
        if args.url:
            added = intake.add_link(con, args.url, prefs, company=args.company)
        else:
            if args.file:
                text = Path(args.file).read_text(encoding="utf-8")
            else:
                print("Paste the posting, then press Ctrl+Z and Enter "
                      "(Ctrl+D on macOS/Linux):", file=sys.stderr)
                text = sys.stdin.read()
            added = intake.add_pasted(
                con, company=args.company or "", title=args.title or "",
                text=text, prefs=prefs, url=args.link or "",
                location=args.location or "")
        con.commit()
        if not args.no_enrich:
            added.enriched = intake.enrich(con, added.job_id)
            con.commit()
    except (intake.IntakeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()

    state = "added" if added.new else "already in your tracker"
    print(f"{state}: job {added.job_id} -- {added.title} at {added.company}")
    print(f"  score {added.score:.2f}")
    for reason in added.reasons[:4]:
        print(f"    · {reason}")
    if added.enriched:
        print(f"  {added.enriched}")
    for warning in added.warnings:
        print(f"  note: {warning}")
    print()
    if added.status:
        print(f"  you are tracking this one already: {added.status.replace('_', ' ')}")
        print(f"next:  open it on the dashboard: /job/{added.job_id}")
    else:
        print(f"next:  jsa save {added.job_id}   then   jsa tailor {added.job_id}")
        print(f"       or open it on the dashboard: /job/{added.job_id}")
    return 0


def cmd_fill(args: argparse.Namespace) -> int:
    """Paste the real posting into a job whose feed carried a stub (ADR 0020).

    The job keeps its number, application and drafts. Sends nothing.
    """
    from . import intake

    try:
        prefs = Preferences.from_profile(load_profile())
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    # Writes a job row, so bring an older tracker up to the schema first,
    # as discovery does (description_origin, ADR 0020).
    db.upgrade()
    con = db.connect()
    try:
        before = con.execute("SELECT match_score FROM jobs WHERE id = ?",
                             (args.job_id,)).fetchone()
        if args.file:
            text = Path(args.file).read_text(encoding="utf-8")
        else:
            print("Paste the posting, then press Ctrl+Z and Enter "
                  "(Ctrl+D on macOS/Linux):", file=sys.stderr)
            text = sys.stdin.read()
        added = intake.fill(con, args.job_id, text, prefs, location=args.location)
        con.commit()
        if not args.no_enrich:
            added.enriched = intake.enrich(con, added.job_id)
            con.commit()
    except (intake.IntakeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()

    old = (before["match_score"] or 0.0) if before else 0.0
    print(f"filled: job {added.job_id} -- {added.title} at {added.company}")
    print(f"  score {old:.2f} -> {added.score:.2f}")
    for reason in added.reasons[:4]:
        print(f"    · {reason}")
    if added.enriched:
        print(f"  {added.enriched}")
    for warning in added.warnings:
        print(f"  note: {warning}")
    print("  discovery will not overwrite this text.")
    if added.status:
        print(f"next:  jsa tailor {added.job_id} --force   (a new draft against the full posting)")
    else:
        print(f"next:  jsa save {added.job_id}   then   jsa tailor {added.job_id}")
    return 0


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


def _print_document(con, document_id: int) -> None:
    """Read one drafted document here, rather than hunting for the .docx.

    A cover letter is prose, so it is printed whole, with how it was produced
    and anything the checks in jsa/letter.py flagged. ADR 0007.
    """
    from . import render, review
    from .config import OUTPUT_DIR

    row = con.execute("SELECT * FROM documents WHERE id = ?",
                      (document_id,)).fetchone()
    if row is None:
        return
    path = review.safe_document_path(row["path"], OUTPUT_DIR)
    if path is None:
        print("     (the file is missing from output/)")
        return
    print(f"     {row['kind']} v{row['version']}  {path.name}")
    if row["note"]:
        print(f"     letter: {row['note']}")
    elif row["kind"] == "cover_letter":
        print("     letter: model prose, every word traced to your profile")
    text = render.extract_text(path)
    body = text[text.index("Dear"):] if "Dear" in text else text
    for line in body.splitlines():
        print(f"       {line}")


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
                  f"  requested {db.local_time(item.requested_at)}")
            print(f"     {item.summary}")
            if args.approval_id and item.subject_type == "document":
                _print_document(con, item.subject_id)
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


def cmd_inbox(args: argparse.Namespace) -> int:
    """Replies to your applications, read from your mailbox read-only (ADR 0021).

    Suggests stage changes; you confirm or dismiss each. Never sends, never
    changes the mailbox, and no mail text goes to a model.
    """
    from . import inbox

    action = getattr(args, "action", None) or "list"
    db.upgrade()           # an older tracker has no inbox_replies table yet
    con = db.connect()
    try:
        if action == "confirm":
            job_id, previous, stage = inbox.confirm(con, args.reply_id, stage=args.stage)
            con.commit()
            print(f"{previous} -> {stage}: job {job_id} (confirmed by you)")
            return 0
        if action == "dismiss":
            inbox.dismiss(con, args.reply_id)
            con.commit()
            print(f"dismissed reply {args.reply_id}")
            return 0
        if not args.no_fetch:
            report = inbox.fetch(con)
            print(f"checked {report.user}: {report.searched} message(s) looked at, "
                  f"{report.stored} new repl(ies) about your applications"
                  + (f", {report.ambiguous} unclear which one" if report.ambiguous else ""))
        rows = inbox.pending(con)
    except inbox.InboxError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except approvals.ApprovalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()
    if not rows:
        print("nothing waiting on you.")
        return 0
    for r in rows:
        who = (f"{r['company']} -- {r['title']} (job {r['job_id']}, {r['status']})"
               if r["application_id"] else "unclear which application")
        print(f"\n[{r['id']}] {who}")
        print(f"     {(r['received_at'] or '')[:10]}  {r['sender_domain']}  {r['subject']}")
        if r["suggested_stage"]:
            print(f"     looks like: {r['kind']} -> suggest {r['suggested_stage']}   "
                  f"confirm: jsa inbox confirm {r['id']}")
        else:
            print(f"     looks like: {r['kind']} (nothing to change)")
        print(f"     dismiss: jsa inbox dismiss {r['id']}")
    print("\nNothing moves until you confirm.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Move an application to a later stage. Always a human saying so."""
    con = db.connect()
    try:
        application_id, previous = approvals.set_stage(
            con, args.job_id, args.stage, note=args.note)
        con.commit()
        line = _job_line(con, args.job_id)
    except approvals.ApprovalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()
    print(f"{previous} -> {args.stage}: application {application_id} -- {line}")
    if args.stage in approvals.CLOSED:
        print("  it leaves the live pipeline; its history stays.")
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    """Record what you intend to do next for a job, and when."""
    con = db.connect()
    try:
        application_id = approvals.set_next_action(
            con, args.job_id, args.action, due=args.due)
        con.commit()
        line = _job_line(con, args.job_id)
    except approvals.ApprovalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()
    when = f" by {args.due}" if args.due else ""
    print(f"next for application {application_id}{when}: {args.action}")
    print(f"  {line}")
    return 0


def cmd_due(args: argparse.Namespace) -> int:
    """What needs attention. A report; it changes nothing."""
    con = db.connect()
    try:
        items = approvals.due_items(con, days=args.days)
    finally:
        con.close()
    if not items:
        print(f"nothing due in the next {args.days} day(s)")
        return 0
    for item in items:
        if item.days_out is None:
            when = f"no date, quiet {item.quiet_days} days"
        elif item.days_out < 0:
            when = f"OVERDUE by {-item.days_out} day(s)"
        elif item.days_out == 0:
            when = "due today"
        else:
            when = f"due in {item.days_out} day(s)"
        print(f"[{item.job_id}] {when}  ({item.status})")
        print(f"     {item.title} at {item.company}")
        print(f"     {item.next_action or 'no next action set — decide one, or let it go'}")
    print(f"\n{len(items)} item(s). Nothing here changed anything.")
    return 0


# A week is the usual point to chase an application that has had no reply.
FOLLOW_UP_DAYS = 7


def _shown_path(path: str) -> str:
    """Relative to the project when it is inside it; the paths are long enough."""
    from .config import ROOT
    try:
        return str(Path(path).resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return path


def cmd_applied(args: argparse.Namespace) -> int:
    """Record that YOU submitted it, and exactly what you sent. Sends nothing."""
    cover = approvals.NOT_SENT if args.no_cover else args.cover
    con = db.connect()
    try:
        before = con.execute("SELECT applied_at FROM applications WHERE job_id = ?",
                             (args.job_id,)).fetchone()
        adding = bool(before and before["applied_at"])
        application_id, all_approved = approvals.mark_applied(
            con, args.job_id, when=args.date, resume=args.resume, cover=cover)
        line = _job_line(con, args.job_id)
        sent = approvals.submitted(con, application_id)
        edited = [s for s in sent if approvals.changed_since_approval(con, s)]
        left_out = approvals.unsent_drafts(con, args.job_id, sent)
        app_row = con.execute(
            "SELECT applied_at, next_action FROM applications WHERE id = ?",
            (application_id,)).fetchone()
        # The record is written once, so it can be looked at first.
        if args.check:
            con.rollback()
        else:
            con.commit()
    except approvals.ApprovalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()
    verb = "added to what was sent" if adding else "marked applied"
    if args.check:
        verb = "CHECK ONLY, nothing written. Would record"
    print(f"{verb}: application {application_id} -- {line}")
    print(f"  applied {db.local_time(app_row['applied_at'])} (your local time)")
    if not sent:
        # Not a refusal. The tracker records what happened; see ADR 0003 #4.
        print("  recorded as sent: nothing. No approved document on file, and "
              "none named.")
    else:
        print("  recorded as sent (fixed now; later drafts will not change it):"
              if not args.check else "  as sent:")
        for item in sent:
            print(f"    {item.describe()}")
            if item.path:
                print(f"      {_shown_path(item.path)}")
            if not item.sha256:
                print("      the file was not on disk, so its contents could "
                      "not be recorded")
    for item in edited:
        print(f"  warning: doc {item.document_id} was changed on disk after you "
              "approved it. What you sent is recorded; the approval covers the "
              "earlier file.")
    for row in left_out:
        label = row["kind"].replace("_", " ")
        flag = "--resume" if row["kind"] == "resume" else "--cover"
        how = (f"add {flag} {row['id']} when you record it" if args.check
               else f"add it:\n      jsa applied {args.job_id} {flag} {row['id']}")
        print(f"  not recorded: {label} doc {row['id']} v{row['version']} is not "
              f"approved, so it was not assumed sent. If it went out, {how}")
    if sent and not all_approved:
        print("  note: something recorded as sent was not approved.")
    if args.check:
        print("  run it again without --check to record this.")
    elif not adding and not app_row["next_action"]:
        # Without one, `jsa due` has nothing to show until three weeks of
        # silence. Suggested, never set: an intention is yours to state.
        from datetime import timedelta
        applied_on = db.local_date(app_row["applied_at"])
        follow_up = ((applied_on + timedelta(days=FOLLOW_UP_DAYS)).isoformat()
                     if applied_on else "YYYY-MM-DD")
        print()
        print("next: nothing will remind you about this until it has been "
              f"quiet for {approvals.QUIET_DAYS} days.")
        print("  To be reminded sooner, set a follow-up:")
        print(f'    jsa next {args.job_id} "follow up if no reply" --due {follow_up}')
        print(f"  When they reply:  jsa status {args.job_id} phone_screen  "
              "(or rejected, technical, ...)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jsa", description=__doc__)
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="log per-call model, token and latency detail to stderr")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the tracker database").set_defaults(func=cmd_init)

    sub.add_parser(
        "doctor", help="what will not work yet in your profile and tracker"
    ).set_defaults(func=cmd_doctor)

    p_imp = sub.add_parser(
        "import-resume", help="turn your resume (.docx) into a draft profile")
    p_imp.add_argument("file", help="your resume, saved as .docx")
    p_imp.add_argument("--out", help="where to write the draft "
                       "(default profile/master_profile.draft.yaml)")
    p_imp.add_argument("--force", action="store_true",
                       help="replace an existing draft (never the live profile)")
    p_imp.set_defaults(func=cmd_import_resume)

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

    p_resc = sub.add_parser(
        "rescore", help="re-read pay and re-score stored listings (no network)")
    p_resc.set_defaults(func=cmd_rescore)

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
    p_match.add_argument(
        "--search",
        help='roles by title, e.g. "support engineer, data analyst": commas mean '
             "or, each word must start a word in the title",
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
    p_enrich.add_argument("--job", type=int, action="append", metavar="ID",
                          help="redo just this posting, whatever its score; "
                               "repeatable. For a fact you know is wrong")
    p_enrich.set_defaults(func=cmd_enrich)

    p_serve = sub.add_parser("serve", help="local review dashboard")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.set_defaults(func=cmd_serve)

    p_cadd = sub.add_parser("contact-add", help="record a person to reach out to")
    p_cadd.add_argument("--name", required=True)
    p_cadd.add_argument("--company", help="company name or slug already in the tracker")
    p_cadd.add_argument("--title")
    p_cadd.add_argument("--linkedin")
    p_cadd.add_argument("--email")
    p_cadd.add_argument(
        "--relationship", default="cold",
        choices=["cold", "warm", "alum", "referral", "recruiter", "friend"])
    p_cadd.add_argument("--notes")
    p_cadd.set_defaults(func=cmd_contact_add)

    sub.add_parser("contacts", help="list your contacts").set_defaults(
        func=cmd_contacts)

    p_odraft = sub.add_parser("outreach-draft", help="draft a message (never sends)")
    p_odraft.add_argument("--contact", type=int, required=True)
    p_odraft.add_argument("--job", type=int)
    p_odraft.add_argument(
        "--channel", default="linkedin_connect",
        choices=["linkedin_connect", "linkedin_dm", "email", "other"])
    p_odraft.add_argument(
        "--purpose", default="referral_ask",
        choices=["referral_ask", "informational", "follow_up", "thank_you"])
    p_odraft.set_defaults(func=cmd_outreach_draft)

    p_oshow = sub.add_parser("outreach-show", help="read a drafted message")
    p_oshow.add_argument("outreach_id", type=int)
    p_oshow.set_defaults(func=cmd_outreach_show)

    p_osent = sub.add_parser(
        "outreach-mark-sent",
        help="record that YOU sent it; this tool never transmits")
    p_osent.add_argument("outreach_id", type=int)
    p_osent.set_defaults(func=cmd_outreach_mark_sent)

    p_prep = sub.add_parser("prep", help="generate interview prep for an application")
    p_prep.add_argument("application_id", type=int)
    p_prep.add_argument(
        "--round", default="phone_screen",
        choices=["phone_screen", "technical", "system_design", "behavioral",
                 "onsite", "final"])
    p_prep.set_defaults(func=cmd_prep)

    p_pshow = sub.add_parser("prep-show", help="print a generated prep in full")
    p_pshow.add_argument("prep_id", type=int)
    p_pshow.set_defaults(func=cmd_prep_show)

    sub.add_parser(
        "tags", help="which of your profile tags match real postings"
    ).set_defaults(func=cmd_tags)

    p_tail = sub.add_parser("tailor", help="draft a resume or cover letter")
    p_tail.add_argument("job_id", type=int)
    p_tail.add_argument("--kind", choices=sorted(KIND_ARG), default="resume")
    p_tail.add_argument(
        "--force", action="store_true",
        help="draft a new version when one already exists")
    p_tail.set_defaults(func=cmd_tailor)

    p_add = sub.add_parser(
        "add", help="add one job from a link, or from pasted text")
    p_add.add_argument("url", nargs="?",
                       help="a Greenhouse, Lever, Ashby or Workday posting link")
    p_add.add_argument("--company", help="the company's name")
    p_add.add_argument("--paste", action="store_true",
                       help="add a posting you copy in by hand (any site)")
    p_add.add_argument("--title", help="with --paste: the job title")
    p_add.add_argument("--link", help="with --paste: where the posting lives")
    p_add.add_argument("--location", help="with --paste: where the job is")
    p_add.add_argument("--file", help="with --paste: read the text from a file")
    p_add.add_argument("--no-enrich", action="store_true",
                       help="skip the model call that reads degree/clearance/years")
    p_add.set_defaults(func=cmd_add)

    p_fill = sub.add_parser(
        "fill", help="paste the real posting into a job that only has a stub")
    p_fill.add_argument("job_id", type=int)
    p_fill.add_argument("--file", help="read the text from a file")
    p_fill.add_argument("--location",
                        help="where the job is, e.g. 'Austin, TX' or 'Remote (US)'; "
                             "replaces the stub's")
    p_fill.add_argument("--no-enrich", action="store_true",
                        help="skip the model call that reads degree/clearance/years")
    p_fill.set_defaults(func=cmd_fill)

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
        help="what to change; kept as the record of why")
    p_rej.set_defaults(func=cmd_reject)

    p_stage = sub.add_parser(
        "status", help="move an application to a later stage (you, not the tool)")
    p_stage.add_argument("job_id", type=int)
    p_stage.add_argument("stage", choices=list(approvals.STAGES), metavar="STAGE",
                         help="one of: " + ", ".join(approvals.STAGES))
    p_stage.add_argument("--note", help="what happened, kept in the trail")
    p_stage.set_defaults(func=cmd_status)

    p_inbox = sub.add_parser(
        "inbox", help="replies to your applications, from your mailbox (read-only)")
    p_inbox.add_argument("--no-fetch", action="store_true",
                         help="list what is already waiting; do not read the mailbox")
    inbox_sub = p_inbox.add_subparsers(dest="action")
    p_ic = inbox_sub.add_parser("confirm", help="move the application as suggested")
    p_ic.add_argument("reply_id", type=int)
    p_ic.add_argument("--stage", choices=list(approvals.STAGES), metavar="STAGE",
                      help="a different stage than the one suggested")
    p_id = inbox_sub.add_parser("dismiss", help="ignore this reply")
    p_id.add_argument("reply_id", type=int)
    p_inbox.set_defaults(func=cmd_inbox)

    p_next = sub.add_parser("next", help="set the next action for a job")
    p_next.add_argument("job_id", type=int)
    p_next.add_argument("action", help='e.g. "follow up with the recruiter"')
    p_next.add_argument("--due", help="YYYY-MM-DD")
    p_next.set_defaults(func=cmd_next)

    p_due = sub.add_parser("due", help="what is due, overdue, or gone quiet")
    p_due.add_argument("--days", type=int, default=7,
                       help="how far ahead to look (default 7)")
    p_due.set_defaults(func=cmd_due)

    p_done = sub.add_parser("applied", help="record that YOU submitted it")
    p_done.add_argument("job_id", type=int)
    p_done.add_argument("--date", help="ISO timestamp; defaults to now")
    p_done.add_argument(
        "--resume", type=int, metavar="DOC",
        help="the resume document you sent (default: the newest you approved)")
    p_cover = p_done.add_mutually_exclusive_group()
    p_cover.add_argument(
        "--cover", type=int, metavar="DOC",
        help="the cover letter you sent (default: the newest you approved)")
    p_cover.add_argument(
        "--no-cover", action="store_true", help="you sent no cover letter")
    p_done.add_argument(
        "--check", action="store_true",
        help="show what would be recorded, and write nothing")
    p_done.set_defaults(func=cmd_applied)

    sub.add_parser(
        "env", help="show where config and the API key are coming from"
    ).set_defaults(func=cmd_env)

    sub.add_parser(
        "models", help="check which LLM models this account can actually call"
    ).set_defaults(func=cmd_models)

    args = parser.parse_args(argv)
    # Windows pipes and redirects default to cp1252, which cannot encode "⚑" or
    # the en dash in a pay range: `jsa matches > file.txt` crashed mid-list.
    # Degrade the character, never the command.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    from .log import configure
    configure(getattr(args, "verbose", False))
    from .llm import LLMError

    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except LLMError as exc:
        # A missing key is a setup step, not a crash. Walking the README with
        # no key printed a twenty-line traceback with the useful sentence at
        # the bottom, which reads as a broken tool.
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
