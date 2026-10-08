"""The Companies page (plan 32, ADR 0032): what each company's postings ask
for in education. What postings ask for, not who gets hired. Reads only."""

from __future__ import annotations

from fastapi.responses import HTMLResponse


def register(app, ctx) -> None:
    """Add this area's routes to `app`; shared helpers come from `ctx`."""
    connect = ctx.connect
    render = ctx.render

    @app.get("/companies", response_class=HTMLResponse)
    def companies_page(sort: str = "open"):
        from .. import companies
        from ..degree import CAVEAT
        sort = sort if sort in ("open", "certs") else "open"
        con = connect()
        try:
            profiles = companies.all_profiles(con, sort=sort)
        finally:
            con.close()
        return render("companies", "companies", title="Companies", profiles=profiles,
                      sort=sort, caveat=CAVEAT, minimum=companies.MIN_FOR_SHARES)
