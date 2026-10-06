"""Matches: the list, the map and the filters (`/`).

Moved from jsa/web.py without change (plan 24); see register()."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse

from . import (
    MAX_PICKED,
    NOT_TAKEN,
    RADIUS_MAX,
    RADIUS_MIN,
    _by_distance,
    _card_data,
    _copies,
    _map_note,
    _pipeline_rows,
)


def register(app, ctx) -> None:
    """Add this area's routes to `app`; shared helpers come from `ctx`."""
    learned = ctx.learned
    match_connect = ctx.match_connect
    match_deck = ctx.match_deck
    match_filters = ctx.match_filters
    match_place = ctx.match_place
    new_rows = ctx.new_rows
    profile = ctx.profile
    render = ctx.render

    @app.get("/", response_class=HTMLResponse)
    def matches(request: Request, near: str = "", track: str = "",
                degree: str = "", remote: str = "", limit: int = 60,
                home: str = "", radius: str = "", anywhere: str = "",
                only: str = "", q: str = ""):
        from .. import mapview
        q = q.strip()
        where, params = match_filters(near, track, q, degree, remote)
        origin, home_text, home_problem, wanted, prefs_radius = match_place(
            not request.query_params, home, radius, anywhere)
        con = match_connect(q)
        try:
            rows = learned(con, new_rows(con, where, params))
            total = con.execute("SELECT COUNT(*) FROM v_new_matches").fetchone()[0]
            # The map draws every posting that survived the other filters,
            # uncapped and unlimited: it is a picture of where the work is,
            # and the top sixty is not that. Every COPY, not one per card
            # (n20): a card is kept when any of its copies is inside the
            # radius, so a dot inside the circle must exist for each one.
            # `key` is the card a copy belongs to -- the same grouping
            # v_new_matches folds by -- so a dot and its card can find
            # each other on the page.
            drawn = [dict(r) for r in con.execute(
                "SELECT m.id AS job_id, m.title, m.location, m.remote, "
                "COALESCE(m.dedup_key, 'job:' || m.id) AS key "
                "FROM jobs m WHERE m.archived_at IS NULL "
                f"AND m.closed_at IS NULL AND {NOT_TAKEN} "
                f"AND {' AND '.join(where)}", params)]
            copies = _copies(con, rows)
            # The operator's own live applications (n21): on the map in
            # their status colour, and in the list when inside the radius.
            mine = _pipeline_rows(con, where, params)
        finally:
            con.close()

        view = mapview.build(drawn, origin, wanted)
        picked = {key for key in only.split(",") if key.strip()}
        if picked and len(picked) <= MAX_PICKED:
            # A bubble on the map, opened: exactly the cards in it, whatever
            # the radius, the three-per-company cap or the card limit would
            # have listed. Asked for by name, like "Show them anyway".
            rows, hidden, unplaced = _by_distance(
                [r for r in rows if r["key"] in picked], origin, None, copies)
            mine, _, _ = _by_distance(
                [r for r in mine if r.get("key") in picked], origin, None)
        else:
            rows, hidden, unplaced = match_deck(rows, copies, origin, wanted)
            rows = rows[:limit]
            mine, _, _ = _by_distance(mine, origin, wanted)
        rows = sorted(mine + rows,
                      key=lambda r: -(r.get("adjusted", r.get("match_score")) or 0))
        # Remote postings pass any radius, so without this the page can say
        # "within 25 miles" over a list that is mostly remote work.
        near_count = sum(1 for r in rows
                         if not r.get("any_remote")
                         and r.get("miles") is not None)

        # What the page's script draws. Only for the local map: with no home
        # there is no distance to measure, and "anywhere" is the national
        # picture the server already drew.
        live = None
        if origin is not None and wanted:
            pts, remote_n, unplaced_n = mapview.points(
                drawn + [{**r, "status": r["status"]} for r in mine], origin)
            from .. import basemap
            live = {"radius": wanted, "min": RADIUS_MIN, "max": RADIUS_MAX,
                    "home": str(origin), "points": pts,
                    # Radians the usual map of the US is turned at home;
                    # the page turns by this much when zoomed out.
                    "turn": round(mapview.albers_turn(origin), 6),
                    # Where the page fetches its ground from, and how far
                    # each tier may be zoomed. None: no data, a flat map.
                    "basemap": ({"home": home, "ppm": basemap.PPM,
                                 "reach": basemap.REACH, "v": basemap.version()}
                                if basemap.available() else None),
                    "remote": remote_n, "unplaced": unplaced_n,
                    "cards": _card_data(rows)}
        return render("matches", "matches", rows=rows, total=total,
                      no_profile=profile() is None,
                      mine=sum(1 for r in rows if r["status"] != "new"),
                      live=live, wide=True,
                      near=near, track=track, q=q,
                      q_terms=[t.strip() for t in q.split(",") if t.strip()],
                      clear_q_url="?" + urlencode(
                          [(k, v) for k, v in request.query_params.multi_items()
                           if k not in ("q", "only")]),
                      degree=degree, remote=remote,
                      home=str(origin) if (origin and wanted) else "",
                      home_text=home_text, home_problem=home_problem,
                      radius="%g" % wanted if wanted else "",
                      profile_radius="%g" % prefs_radius,
                      radius_min=RADIUS_MIN, radius_max=RADIUS_MAX,
                      map=view, map_note=_map_note(view),
                      hidden=hidden, near_count=near_count, unplaced=unplaced,
                      nationwide_url="?" + urlencode(
                          {k: v for k, v in
                           {"anywhere": "1", "near": near, "q": q,
                            "degree": degree, "remote": remote,
                            "home": home_text}.items() if v}))
