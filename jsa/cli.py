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

from . import db, discover
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
