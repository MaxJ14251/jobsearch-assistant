"""Local review dashboard.

Binds to 127.0.0.1 only. This database holds a real job search — names of
companies applied to, dates, rejection notes — and none of it should be
reachable from the network.

Server-rendered with Jinja2 and no build step: it runs on the dependencies
already in requirements.txt. Approve and reject go through `jsa.approvals`, the
same path the CLI uses, so the human-approval guarantee is identical here.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import DictLoader, Environment, select_autoescape

from . import approvals, db

HOST = "127.0.0.1"          # never 0.0.0.0
PORT = 8765

BASE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} · Job Search</title>
<style>
  :root{
    --paper:#F2F3F0;--surface:#fff;--surface-2:#E9EBE6;--ink:#171C1A;
    --ink-2:#39423E;--mute:#6E7873;--rule:#D3D7D0;
    --copper:#B5652E;--sage:#3F7A66;--sage-soft:#DDEAE3;
    --clay:#A8412F;--clay-soft:#F5DFDA;
  }
  @media (prefers-color-scheme:dark){:root{
    --paper:#121615;--surface:#191F1D;--surface-2:#222927;--ink:#E8EBE7;
    --ink-2:#C0C7C2;--mute:#8B958F;--rule:#2C3532;--copper:#D98A4F;
    --sage:#6DAF96;--sage-soft:#1B2A25;--clay:#D2705B;--clay-soft:#2E1B17;}}
  *{box-sizing:border-box}
  body{margin:0;background:var(--paper);color:var(--ink);line-height:1.55;
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
  .wrap{max-width:1000px;margin:0 auto;padding:0 16px 64px}
  nav{border-bottom:1px solid var(--rule);background:var(--surface);
      position:sticky;top:0;z-index:5}
  nav .wrap{display:flex;gap:18px;padding-block:12px;align-items:center;
    flex-wrap:wrap;padding-bottom:12px}
  nav a{color:var(--ink-2);text-decoration:none;font-weight:500;font-size:14px}
  nav a.on{color:var(--copper)}
  h1{font-size:22px;margin:24px 0 4px;line-height:1.2}
  .sub{color:var(--mute);font-size:13px;margin:0 0 18px}
  form.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px;
    background:var(--surface);border:1px solid var(--rule);border-radius:4px;padding:12px}
  select,input[type=search]{font:inherit;font-size:13px;padding:6px 8px;
    border:1px solid var(--rule);border-radius:3px;background:var(--surface);
    color:var(--ink);min-width:0}
  button{font:inherit;font-size:13px;padding:6px 12px;border:0;border-radius:3px;
    background:var(--copper);color:#fff;cursor:pointer}
  button.ghost{background:var(--surface-2);color:var(--ink-2)}
  .card{background:var(--surface);border:1px solid var(--rule);border-radius:4px;
    padding:14px 16px;margin-bottom:10px}
  .row1{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
  .score{font:600 14px ui-monospace,Menlo,monospace;color:var(--copper);
    font-variant-numeric:tabular-nums}
  .title{font-weight:600;overflow-wrap:anywhere}
  .co{color:var(--mute);font-size:13px;overflow-wrap:anywhere}
  .meta{color:var(--mute);font-size:12.5px;margin-top:3px;overflow-wrap:anywhere}
  .flags{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
  .flag{font:500 11px ui-monospace,Menlo,monospace;padding:2px 7px;border-radius:3px;
    background:var(--surface-2);color:var(--mute);white-space:nowrap}
  .flag.warn{background:var(--clay-soft);color:var(--clay)}
  .flag.ok{background:var(--sage-soft);color:var(--sage)}
  .reasons{margin:8px 0 0;padding-left:16px;color:var(--ink-2);font-size:13px}
  .reasons li{margin:2px 0;overflow-wrap:anywhere}
  .jd{background:var(--surface);border:1px solid var(--rule);border-radius:4px;
    padding:16px;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;
    font-size:13.5px;color:var(--ink-2);max-height:70vh;overflow-y:auto}
  .empty{color:var(--mute);padding:28px 0}
  a.plain{color:var(--copper);overflow-wrap:anywhere}
  @media (max-width:460px){
    .wrap{padding:0 12px 48px}
    h1{font-size:19px}
    form.filters{flex-direction:column}
    select,input[type=search],form.filters button{width:100%}
  }
</style></head><body>
<nav><div class="wrap">
  <a href="/" class="{{ 'on' if page=='matches' }}">Matches</a>
  <a href="/pipeline" class="{{ 'on' if page=='pipeline' }}">Pipeline</a>
  <a href="/review" class="{{ 'on' if page=='review' }}">Review{% if pending_count %} ({{ pending_count }}){% endif %}</a>
</div></nav>
<div class="wrap">{% block body %}{% endblock %}</div>
</body></html>"""

MATCHES = """{% extends "base" %}{% block body %}
<h1>Matches</h1>
<p class="sub">{{ total }} unreviewed · showing {{ rows|length }}</p>
<form class="filters" method="get">
  <select name="near"><option value="">Anywhere</option>
    {% for r in regions %}<option value="{{ r }}" {{ 'selected' if r==near }}>{{ r }}</option>{% endfor %}
  </select>
  <select name="track"><option value="">Both tracks</option>
    <option value="engineering" {{ 'selected' if track=='engineering' }}>Engineering</option>
    <option value="sales" {{ 'selected' if track=='sales' }}>Sales</option>
  </select>
  <select name="degree"><option value="">Degree: any</option>
    <option value="no" {{ 'selected' if degree=='no' }}>Not required</option>
    <option value="yes" {{ 'selected' if degree=='yes' }}>Required</option>
  </select>
  <select name="remote"><option value="">Any location type</option>
    <option value="remote" {{ 'selected' if remote=='remote' }}>Remote only</option>
  </select>
  <button type="submit">Filter</button>
</form>
{% for r in rows %}
<div class="card">
  <div class="row1">
    <span class="score">{{ '%.2f'|format(r.match_score or 0) }}</span>
    <span class="title"><a class="plain" href="/job/{{ r.job_id }}">{{ r.title }}</a></span>
    <span class="co">{{ r.company }}</span>
  </div>
  <div class="meta">{{ r.location or 'location not stated' }} · {{ r.remote }}
    {%- if r.variant_count and r.variant_count > 1 %} · +{{ r.variant_count - 1 }} more location(s){% endif %}</div>
  <div class="flags">
    {% if r.track == 'sales' %}<span class="flag">sales track</span>{% endif %}
    {% if r.degree_required == 1 %}<span class="flag warn">degree required</span>
    {% elif r.degree_required == 0 %}<span class="flag ok">no degree needed</span>{% endif %}
    {% if r.clearance_required == 1 %}<span class="flag warn">clearance</span>{% endif %}
    {% if r.years_required is not none %}<span class="flag">{{ r.years_required }}+ yrs</span>{% endif %}
    {% for t in r.stack %}<span class="flag">{{ t }}</span>{% endfor %}
  </div>
  {% if r.reasons %}<ul class="reasons">{% for x in r.reasons %}<li>{{ x }}</li>{% endfor %}</ul>{% endif %}
</div>
{% else %}<p class="empty">Nothing matches those filters.</p>{% endfor %}
{% endblock %}"""

JOB = """{% extends "base" %}{% block body %}
<h1>{{ job.title }}</h1>
<p class="sub">{{ job.company }} · {{ job.location or 'location not stated' }} · {{ job.remote }}</p>
<div class="flags" style="margin-bottom:14px">
  {% if job.degree_required == 1 %}<span class="flag warn">degree required</span>
  {% elif job.degree_required == 0 %}<span class="flag ok">no degree needed</span>{% endif %}
  {% if job.clearance_required == 1 %}<span class="flag warn">clearance</span>{% endif %}
  {% if job.years_required is not none %}<span class="flag">{{ job.years_required }}+ yrs</span>{% endif %}
  {% for t in stack %}<span class="flag">{{ t }}</span>{% endfor %}
</div>
{% if job.enrichment_note %}<p class="sub">{{ job.enrichment_note }}</p>{% endif %}
<p><a class="plain" href="{{ job.url }}" rel="noopener">Open the original posting</a></p>
<div class="jd">{{ job.description or 'No description captured.' }}</div>
{% endblock %}"""

PIPELINE = """{% extends "base" %}{% block body %}
<h1>Pipeline</h1>
<p class="sub">{{ rows|length }} live application(s)</p>
{% for r in rows %}
<div class="card">
  <div class="row1"><span class="flag">{{ r.status }}</span>
    <span class="title">{{ r.title }}</span><span class="co">{{ r.company }}</span></div>
  <div class="meta">{{ r.next_action or 'no next action set' }}
    {%- if r.last_activity_at %} · last activity {{ r.last_activity_at[:10] }}{% endif %}</div>
</div>
{% else %}<p class="empty">Nothing in the pipeline yet.</p>{% endfor %}
{% endblock %}"""

REVIEW = """{% extends "base" %}{% block body %}
<h1>Review queue</h1>
<p class="sub">{{ rows|length }} awaiting your decision</p>
{% for r in rows %}
<div class="card">
  <div class="row1"><span class="flag">{{ r.subject_type }}</span>
    <span class="title">{{ r.summary }}</span></div>
  <div class="meta">requested {{ r.requested_at[:16].replace('T',' ') }}</div>
  <form method="post" action="/approve" style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
    <input type="hidden" name="approval_id" value="{{ r.approval_id }}">
    <button type="submit">Approve</button>
  </form>
  <form method="post" action="/reject" style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">
    <input type="hidden" name="approval_id" value="{{ r.approval_id }}">
    <input type="search" name="feedback" placeholder="What should change? (required)" style="flex:1">
    <button class="ghost" type="submit">Reject</button>
  </form>
  {% if error_id == r.approval_id %}<p class="meta" style="color:var(--clay)">{{ error }}</p>{% endif %}
</div>
{% else %}<p class="empty">Nothing waiting. </p>{% endfor %}
{% endblock %}"""

env = Environment(
    loader=DictLoader({"base": BASE, "matches": MATCHES, "job": JOB,
                       "pipeline": PIPELINE, "review": REVIEW}),
    autoescape=select_autoescape(["html"]),
)


def _regions() -> list[str]:
    from .cli import load_regions
    return sorted(load_regions())


def _pending_count(con: sqlite3.Connection) -> int:
    return con.execute(
        "SELECT COUNT(*) FROM approvals WHERE decision='pending'").fetchone()[0]


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["reasons"] = json.loads(d.get("match_reasons") or "[]")
    d["stack"] = json.loads(d.get("tech_stack") or "[]")
    return d


def create_app() -> FastAPI:
    app = FastAPI(title="Job Search Review")

    def render(name: str, page: str, **ctx) -> HTMLResponse:
        con = db.connect()
        try:
            ctx.setdefault("pending_count", _pending_count(con))
        finally:
            con.close()
        html = env.get_template(name).render(
            page=page, title=page.title(), **ctx)
        return HTMLResponse(html)

    @app.get("/", response_class=HTMLResponse)
    def matches(near: str = "", track: str = "", degree: str = "",
                remote: str = "", limit: int = 60):
        from .cli import load_regions, region_clause
        where, params = ["1=1"], {}
        if near and near in load_regions():
            where.append(f"({region_clause(load_regions(), near)})")
        if track in ("engineering", "sales"):
            where.append("m.track = :track")
            params["track"] = track
        if degree == "yes":
            where.append("m.degree_required = 1")
        elif degree == "no":
            where.append("(m.degree_required = 0 OR m.degree_required IS NULL)")
        if remote == "remote":
            where.append("m.remote = 'remote'")

        con = db.connect()
        try:
            sql = f"""WITH capped AS (
                        SELECT m.*, ROW_NUMBER() OVER (
                          PARTITION BY m.company ORDER BY m.match_score DESC) AS rk
                        FROM v_new_matches m WHERE {' AND '.join(where)})
                      SELECT * FROM capped WHERE rk <= 3
                      ORDER BY match_score DESC LIMIT :limit"""
            params["limit"] = limit
            rows = [_decode(r) for r in con.execute(sql, params).fetchall()]
            total = con.execute("SELECT COUNT(*) FROM v_new_matches").fetchone()[0]
        finally:
            con.close()
        return render("matches", "matches", rows=rows, total=total,
                      regions=_regions(), near=near, track=track,
                      degree=degree, remote=remote)

    @app.get("/job/{job_id}", response_class=HTMLResponse)
    def job_detail(job_id: int):
        con = db.connect()
        try:
            row = con.execute(
                "SELECT j.*, c.name AS company FROM jobs j "
                "JOIN companies c ON c.id = j.company_id WHERE j.id = ?",
                (job_id,)).fetchone()
        finally:
            con.close()
        if row is None:
            return HTMLResponse("<p>No such job.</p>", status_code=404)
        return render("job", "matches", job=dict(row),
                      stack=json.loads(row["tech_stack"] or "[]"))

    @app.get("/pipeline", response_class=HTMLResponse)
    def pipeline():
        con = db.connect()
        try:
            rows = [dict(r) for r in con.execute("SELECT * FROM v_pipeline").fetchall()]
        finally:
            con.close()
        return render("pipeline", "pipeline", rows=rows)

    @app.get("/review", response_class=HTMLResponse)
    def review(error: str = "", error_id: int = 0):
        con = db.connect()
        try:
            rows = [dict(r) for r in
                    con.execute("SELECT * FROM v_awaiting_approval").fetchall()]
        finally:
            con.close()
        return render("review", "review", rows=rows, error=error,
                      error_id=error_id)

    @app.post("/approve")
    def do_approve(approval_id: int = Form(...)):
        con = db.connect()
        try:
            approvals.approve(con, approval_id)
            con.commit()
        finally:
            con.close()
        return RedirectResponse("/review", status_code=303)

    @app.post("/reject")
    def do_reject(approval_id: int = Form(...), feedback: str = Form("")):
        con = db.connect()
        try:
            approvals.reject(con, approval_id, feedback)
            con.commit()
        except approvals.ApprovalError as exc:
            return RedirectResponse(
                f"/review?error={exc}&error_id={approval_id}", status_code=303)
        finally:
            con.close()
        return RedirectResponse("/review", status_code=303)

    return app


LOOPBACK = ("127.0.0.1", "localhost", "::1")
ALLOW_PUBLIC_BIND_ENV = "JSA_ALLOW_PUBLIC_BIND"


def serve(host: str = HOST, port: int = PORT) -> None:
    """Start the dashboard. Loopback only, unless explicitly overridden.

    A container is the one legitimate exception: the process must bind
    0.0.0.0 *inside* the container for the port mapping to work at all, and
    safety comes from the host side binding to 127.0.0.1. That case sets
    JSA_ALLOW_PUBLIC_BIND=1 (see docker-compose.yml) so the override is a
    deliberate, greppable decision rather than a silently weakened guard.
    """
    import os

    import uvicorn

    if host not in LOOPBACK:
        if os.environ.get(ALLOW_PUBLIC_BIND_ENV) != "1":
            raise ValueError(
                f"refusing to bind to {host!r}: this database holds a real job "
                "search and must stay on the loopback interface. If you are "
                f"running in a container, set {ALLOW_PUBLIC_BIND_ENV}=1 and "
                "publish the port as 127.0.0.1:8765:8765 so it is still "
                "unreachable from the network."
            )
        print(
            f"WARNING: binding to {host!r} because {ALLOW_PUBLIC_BIND_ENV}=1. "
            "Anything that can reach this port can read your job search."
        )
    print(f"review dashboard: http://{host}:{port}  (ctrl-c to stop)")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
