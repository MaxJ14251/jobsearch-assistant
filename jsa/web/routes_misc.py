"""Small routes: reset learning (plan 19), dismiss the daily banner (plan 18), basemap tiles.

Moved from jsa/web.py without change (plan 24); see register()."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Form
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

from .. import db
from . import _origin


def register(app, ctx) -> None:
    """Add this area's routes to `app`; shared helpers come from `ctx`."""
    connect = ctx.connect
    profile = ctx.profile

    @app.post("/learning/reset")
    def learning_reset(back: str = Form("/turbo")):
        """Ignore every swipe so far (plan 19). Nothing is deleted."""
        from .. import learning
        con = connect()
        try:
            learning.reset(con)
            con.commit()
        finally:
            con.close()
        target = back if back in ("/", "/turbo") else "/turbo"
        return RedirectResponse(target + "?" + urlencode(
            {"msg": "Learning reset: the order is back to your match scores "
                    "until you swipe more.", "bad": 0}), status_code=303)

    @app.post("/daily/seen")
    def daily_seen(run_id: int = Form(...), back: str = Form("/")):
        """"Got it" on the daily banner. Hides runs up to this one."""
        con = connect()
        try:
            con.execute("UPDATE daily_runs SET seen_at = ? WHERE id <= ? "
                        "AND seen_at IS NULL", (db.utcnow(), run_id))
            con.commit()
        finally:
            con.close()
        return RedirectResponse(back if back in ("/", "/pipeline") else "/",
                                status_code=303)

    @app.get("/basemap/{tier}")
    def basemap_tier(tier: str, home: str = "", x: float = 0.0, y: float = 0.0,
                     v: str = ""):   # v: only makes the URL change with the data
        """The ground under the live map around (x, y) miles from the centre
        (n23). Same origin, same Host check as every other route: the only
        place map data comes from is this machine."""
        import math

        from .. import basemap
        if tier not in basemap.TIERS:
            return PlainTextResponse("no such tier", status_code=404)
        if not (math.isfinite(x) and math.isfinite(y)):
            return PlainTextResponse("x and y must be numbers", status_code=400)
        origin, _, _ = _origin(home, profile())
        if origin is None:
            return PlainTextResponse("no centre to draw around", status_code=404)
        limit = 13000.0                   # half the Earth's circumference, in miles
        data = basemap.layers(origin, tier, (max(-limit, min(limit, x)),
                                             max(-limit, min(limit, y))))
        if data is None:
            return PlainTextResponse("no basemap data", status_code=404)
        return JSONResponse(data, headers={"Cache-Control": "private, max-age=86400"})
