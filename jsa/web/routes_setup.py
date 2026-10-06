"""Setup (plan 20): the draft profile's preferences, first-run adopt, first search.

Moved from jsa/web.py without change (plan 24); see register()."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse


def register(app, ctx) -> None:
    """Add this area's routes to `app`; shared helpers come from `ctx`."""
    connect = ctx.connect
    render = ctx.render

    # --- Setup (plan 20): draft only; adopt on first run only -------------

    NL = "\n"   # one value per line in the setup form's text areas

    def setup_form_values(draft: dict | None) -> dict[str, Any]:
        p = (draft or {}).get("job_search_preferences") or {}
        locs = [str(x) for x in p.get("locations") or []]
        home = str(p.get("home_location") or "")
        floor = p.get("compensation_floor_usd")
        tri = {True: "yes", False: "no"}
        return {
            "target_titles": NL.join(map(str, p.get("target_titles") or [])),
            "fallback_titles": NL.join(map(str, p.get("fallback_titles") or [])),
            "home": home, "radius": p.get("radius_miles") or 40,
            # Only "Remote (US)" is the checkbox; any other "Remote (...)"
            # stays a line of its own, not silently replaced (review R-29).
            "remote": "Remote (US)" in locs,
            "locations": NL.join(x for x in locs if x != "Remote (US)" and x != home),
            "work_authorization": p.get("work_authorization") or "",
            "needs_visa_sponsorship": tri.get(p.get("needs_visa_sponsorship"), ""),
            "willing_to_relocate": tri.get(p.get("willing_to_relocate"), ""),
            "floor": "no floor" if floor == "no_floor" else ("" if floor is None else floor),
            "max_years": p.get("max_years_experience") or "",
            "years_filter": p.get("years_filter") or "reject",
            "exclude_keywords": NL.join(map(str, p.get("exclude_keywords") or [])),
        }

    def setup_page(msg: str = "", bad: int = 0, form: dict | None = None,
                   errors: dict | None = None) -> HTMLResponse:
        from .. import config, doctor, setup
        from ..llm import LLMError, api_key
        from ..resume_import import DRAFT_NAME
        draft = setup.load_draft()
        try:
            api_key()
            has_key = True
        except LLMError:
            has_key = False
        con = connect()
        try:
            blocking = doctor.run(draft, con).blocking if draft is not None else []
            progress = app.state.discovery.progress(con)
        finally:
            con.close()
        live = config.PROFILE_PATH
        return render("setup", "setup", title="Set up", msg=msg, bad=bad,
                      draft=draft is not None, draft_name=DRAFT_NAME,
                      f=form or setup_form_values(draft), errors=errors or {},
                      blocking=blocking, can_adopt=setup.can_adopt(),
                      live_exists=live.exists(),
                      live_personal=live.exists() and not setup.can_adopt(),
                      draft_full=str(setup.draft_path()), live_full=str(live),
                      api_key=has_key, progress=progress)

    def back_to_setup(msg: str, bad: bool = False) -> RedirectResponse:
        return RedirectResponse("/setup?" + urlencode({"msg": msg, "bad": int(bad)}),
                                status_code=303)

    @app.get("/setup", response_class=HTMLResponse)
    def setup_get(msg: str = "", bad: int = 0):
        return setup_page(msg, bad)

    @app.post("/setup/start")
    def setup_start():
        from .. import setup
        setup.start_from_example()
        return back_to_setup("Started from the example. Replace its preferences "
                             "below, and its work history in the draft file.")

    @app.post("/setup/preferences", response_class=HTMLResponse)
    async def setup_preferences(request: Request):
        from .. import setup
        form = {k: str(v) for k, v in (await request.form()).items() if k != "csrf"}
        if setup.load_draft() is None:
            return back_to_setup("Start with step 1 first.", True)
        prefs, errors = setup.validate(form)
        if errors:
            values = {**form, "remote": bool(form.get("remote"))}
            return setup_page("Nothing was saved: fix the marked fields.", 1,
                              values, errors)
        setup.set_preferences(setup.draft_path(), prefs)
        return back_to_setup("Saved to the draft.")

    @app.post("/setup/adopt")
    def setup_adopt():
        from .. import setup
        try:
            copy = setup.adopt()
        except (PermissionError, FileNotFoundError) as exc:
            return back_to_setup(str(exc)[:1].upper() + str(exc)[1:] + ".", True)
        return back_to_setup("This is now your profile."
                             + (f" The example it replaced is backed up in {copy}."
                                if copy else "") + " Next: find jobs.")

    @app.post("/setup/discover")
    def setup_discover():
        if not app.state.discovery.start():
            return back_to_setup("A search is already running.", True)
        return back_to_setup("Searching in the background. This page shows progress.")

    @app.get("/setup/discover/status")
    def setup_discover_status():
        con = connect()
        try:
            return JSONResponse(app.state.discovery.progress(con))
        finally:
            con.close()
