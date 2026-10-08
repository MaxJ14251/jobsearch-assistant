"""The Companies page (plan 32, ADR 0032): what each company's postings ask
for in education, not who gets hired. And the page that credits the sources
of the education figures (plan 33, ADR 0033). Reads only."""

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

    @app.get("/about-data", response_class=HTMLResponse)
    def about_data():
        """Sources and attribution for the education figures (plans 32, 33).
        O*NET's CC BY 4.0 license requires the credit shown here."""
        from .. import roles
        from ..degree import CAVEAT
        year = next(iter(roles._education().values())).year if roles.available() else ""
        return render("about_data", "companies", title="About this data",
                      sources=roles.SOURCE_URLS, year=year,
                      min_confidence=roles.MIN_CONFIDENCE, degree_caveat=CAVEAT)
