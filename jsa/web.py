"""Local review dashboard.

Binds to 127.0.0.1 only. This database holds a real job search — names of
companies applied to, dates, rejection notes — and the generated documents
carry full contact details. None of it should be reachable from the network.

Server-rendered with Jinja2 and no build step: it runs on the dependencies
already in requirements.txt. Approve and reject go through `jsa.approvals`, the
same path the CLI uses, so the human-approval guarantee is identical here.
Tailoring goes through `jsa.drafting`, the same path `jsa tailor` uses.

Two guards beyond the loopback bind, because a loopback server is still
reachable from the browser that visits other sites:

- Host header check. A hostile page can point its own domain at 127.0.0.1
  (DNS rebinding) and then read responses as same-origin. Its requests still
  carry its own hostname, so anything but a loopback name is refused.
- A per-process token on every form. Any page can make the browser POST to
  127.0.0.1; it cannot read the token, so it cannot forge an approval.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import (FileResponse, HTMLResponse, PlainTextResponse,
                               RedirectResponse)
from jinja2 import DictLoader, Environment

from . import approvals, db, review

HOST = "127.0.0.1"          # never 0.0.0.0
PORT = 8765

DOCX_TYPE = ("application/vnd.openxmlformats-officedocument."
             "wordprocessingml.document")

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
  h1{font-size:22px;margin:24px 0 4px;line-height:1.2;text-wrap:balance;
    overflow-wrap:anywhere}
  h2{font-size:13px;margin:28px 0 8px;letter-spacing:.06em;text-transform:uppercase;
    color:var(--ink-2)}
  .sub{color:var(--mute);font-size:13px;margin:0 0 18px;overflow-wrap:anywhere}
  form.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px;
    background:var(--surface);border:1px solid var(--rule);border-radius:4px;padding:12px}
  select,input[type=search]{font:inherit;font-size:13px;padding:6px 8px;
    border:1px solid var(--rule);border-radius:3px;background:var(--surface);
    color:var(--ink);min-width:0}
  button{font:inherit;font-size:13px;padding:6px 12px;border:0;border-radius:3px;
    background:var(--copper);color:#fff;cursor:pointer}
  button:disabled{opacity:.6;cursor:progress}
  button.ghost{background:var(--surface-2);color:var(--ink-2)}
  :focus-visible{outline:2px solid var(--copper);outline-offset:2px}
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
  .empty{color:var(--mute);padding:12px 0}
  a.plain{color:var(--copper);overflow-wrap:anywhere}
  a.btn{display:inline-block;font-size:13px;padding:6px 12px;border-radius:3px;
    background:var(--surface-2);color:var(--ink);text-decoration:none}
  code{font:12.5px ui-monospace,Menlo,monospace;overflow-wrap:anywhere}
  .note{border-radius:4px;padding:10px 12px;margin:10px 0;font-size:13.5px;
    overflow-wrap:anywhere}
  .note.bad{background:var(--clay-soft);color:var(--clay)}
  .note.good{background:var(--sage-soft);color:var(--sage)}
  .inline{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
  .ids{font:12px ui-monospace,Menlo,monospace;color:var(--mute);overflow-wrap:anywhere}
  details{margin-top:8px}
  summary{cursor:pointer;color:var(--copper);font-size:13px}
  .pair{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px;
    padding:10px 0;border-top:1px solid var(--rule)}
  .pair.flag-row{background:var(--clay-soft);padding-inline:10px;border-radius:3px;
    border-top-color:transparent;margin-top:4px}
  .lbl{font:600 10.5px ui-monospace,Menlo,monospace;letter-spacing:.06em;
    text-transform:uppercase;color:var(--mute);margin-bottom:2px;overflow-wrap:anywhere}
  .pair p{margin:0;font-size:13.5px;overflow-wrap:anywhere}
  mark{background:transparent;color:var(--clay);font-weight:600}
  .doc{background:var(--surface-2);border-radius:4px;padding:12px 14px;
    font-size:13.5px;overflow-wrap:anywhere}
  .doc h3{font-size:12px;letter-spacing:.06em;margin:12px 0 4px}
  .doc p{margin:2px 0}
  .doc ul{margin:2px 0;padding-left:18px}
  .qa{border-top:1px solid var(--rule);padding:14px 0}
  .qa .q{font-weight:600;overflow-wrap:anywhere}
  .qa .why{color:var(--mute);font-size:13px;margin:4px 0;overflow-wrap:anywhere}
  .qa .say{margin:8px 0 0;padding-left:12px;border-left:3px solid var(--sage);
    overflow-wrap:anywhere;max-width:70ch}
  @media (max-width:460px){
    .wrap{padding:0 12px 48px}
    h1{font-size:19px}
    form.filters{flex-direction:column}
    select,input[type=search],form.filters button{width:100%}
    .pair{grid-template-columns:minmax(0,1fr)}
  }
  @media (prefers-reduced-motion:reduce){*{transition:none!important}}
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
  <select name="near" id="f-near" aria-label="Region"><option value="">Anywhere</option>
    {% for r in regions %}<option value="{{ r }}" {{ 'selected' if r==near }}>{{ r }}</option>{% endfor %}
  </select>
  <select name="track" id="f-track" aria-label="Track"><option value="">Both tracks</option>
    <option value="engineering" {{ 'selected' if track=='engineering' }}>Engineering</option>
    <option value="sales" {{ 'selected' if track=='sales' }}>Sales</option>
  </select>
  <select name="degree" id="f-degree" aria-label="Degree"><option value="">Degree: any</option>
    <option value="no" {{ 'selected' if degree=='no' }}>Not required</option>
    <option value="yes" {{ 'selected' if degree=='yes' }}>Required</option>
  </select>
  <select name="remote" id="f-remote" aria-label="Location type"><option value="">Any location type</option>
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
  {% if application %}<span class="flag ok">application: {{ application.status }}</span>{% endif %}
  {% if job.degree_required == 1 %}<span class="flag warn">degree required</span>
  {% elif job.degree_required == 0 %}<span class="flag ok">no degree needed</span>{% endif %}
  {% if job.clearance_required == 1 %}<span class="flag warn">clearance</span>{% endif %}
  {% if job.years_required is not none %}<span class="flag">{{ job.years_required }}+ yrs</span>{% endif %}
  {% for t in stack %}<span class="flag">{{ t }}</span>{% endfor %}
</div>
{% if msg %}<p class="note {{ 'bad' if bad else 'good' }}" role="status">{{ msg }}</p>{% endif %}

<h2>Documents</h2>
{% if not application %}
  <p class="sub">Tailoring needs a saved application. Saving only records that you are pursuing this job.</p>
  <form method="post" action="/job/{{ job.id }}/save" class="inline">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <button type="submit">Save as application</button>
  </form>
{% else %}
  <form method="post" action="/job/{{ job.id }}/tailor" class="inline"
        onsubmit="var b=this.querySelector('button');b.disabled=true;b.textContent='Drafting, up to a minute…'">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <select name="kind" id="tailor-kind" aria-label="Document kind">
      <option value="resume">Resume</option>
      <option value="cover_letter">Cover letter</option>
    </select>
    <button type="submit">{{ 'Draft a new version' if documents else 'Tailor a draft' }}</button>
    <span class="meta">The draft goes to your review queue. Nothing is sent.</span>
  </form>
{% endif %}
{% for d in documents %}
<div class="card" id="doc-{{ d.id }}">
  <div class="row1">
    <span class="title">{{ d.kind|replace('_',' ')|capitalize }} v{{ d.version }}</span>
    <span class="flag {{ {'approved':'ok','rejected':'warn'}.get(d.status,'') }}">{{ d.status }}</span>
    {% if d.flagged %}<span class="flag warn">{{ d.flagged }} line(s) not in your profile</span>{% endif %}
    <span class="co">document {{ d.id }}</span>
  </div>
  <div class="meta">drafted {{ d.generated_at[:16].replace('T',' ') }} · {{ d.model or 'model not recorded' }}</div>
  {% if d.feedback %}<div class="meta">your note: {{ d.feedback }}</div>{% endif %}
  <div class="flags">
    {% for g in d.gaps %}<span class="flag warn">gap: {{ g }}</span>
    {% else %}<span class="flag ok">no keyword gaps reported</span>{% endfor %}
  </div>
  <details><summary>{{ d.bullets|length }} bullet(s) selected from your profile</summary>
    <ul class="reasons">{% for b in d.bullets %}
      <li><span class="ids">{{ b.id }}</span> · {{ b.text or 'no longer in your profile' }}</li>
    {% endfor %}</ul>
  </details>
  <div class="inline">
    {% if d.servable %}<a class="btn" href="/document/{{ d.id }}">Download .docx</a>
    {% else %}<span class="meta">The file is missing from output/.</span>{% endif %}
    {% if d.status == 'pending' %}<a class="plain" href="/review#doc-{{ d.id }}">Review it</a>{% endif %}
  </div>
</div>
{% else %}<p class="empty">No documents drafted for this job yet.</p>{% endfor %}

<h2>Interview prep</h2>
{% for p in preps %}
<div class="card"><div class="row1">
  <span class="title"><a class="plain" href="/prep/{{ p.id }}">{{ (p.round or 'general')|replace('_',' ')|capitalize }}</a></span>
  <span class="co">{{ p.count }} question(s) · {{ p.generated_at[:10] }}</span></div></div>
{% else %}<p class="empty">No interview prep yet.{% if application %} Run <code>jsa prep {{ application.id }}</code> to draft one.{% endif %}</p>{% endfor %}

<h2>Posting</h2>
{% if job.enrichment_note %}<p class="sub">{{ job.enrichment_note }}</p>{% endif %}
<p><a class="plain" href="{{ job.url }}" rel="noopener">Open the original posting</a></p>
<div class="jd">{{ job.description or 'No description captured.' }}</div>
{% endblock %}"""

PREP = """{% extends "base" %}{% block body %}
<p class="sub" style="margin-top:18px"><a class="plain" href="/job/{{ prep.job_id }}">← {{ prep.title }} at {{ prep.company }}</a></p>
<h1>Interview prep: {{ (prep.round or 'general')|replace('_',' ') }}</h1>
<p class="sub">{{ questions|length }} question(s) · drafted {{ prep.generated_at[:10] }} · the answers are notes in your own words, not a script</p>
{% if prep.company_brief %}<h2>Company brief</h2><p style="max-width:70ch;overflow-wrap:anywhere">{{ prep.company_brief }}</p>{% endif %}
<h2>Questions</h2>
{% for q in questions %}
<div class="qa">
  <div class="q">{{ loop.index }}. {{ q.question }}</div>
  {% if q.why %}<div class="why">Why they ask: {{ q.why }}</div>{% endif %}
  {% if q.answer_notes %}<p class="say">{{ q.answer_notes }}</p>{% endif %}
</div>
{% else %}<p class="empty">This prep has no questions.</p>{% endfor %}
{% endblock %}"""

PIPELINE = """{% extends "base" %}{% block body %}
<h1>Pipeline</h1>
<p class="sub">{{ rows|length }} live application(s)</p>
{% for r in rows %}
<div class="card">
  <div class="row1"><span class="flag">{{ r.status }}</span>
    <span class="title"><a class="plain" href="/job/{{ r.job_id }}">{{ r.title }}</a></span><span class="co">{{ r.company }}</span></div>
  <div class="meta">{{ r.next_action or 'no next action set' }}
    {%- if r.last_activity_at %} · last activity {{ r.last_activity_at[:10] }}{% endif %}</div>
</div>
{% else %}<p class="empty">Nothing in the pipeline yet.</p>{% endfor %}
{% endblock %}"""

REVIEW = """{% extends "base" %}{% block body %}
<h1>Review queue</h1>
<p class="sub">{{ rows|length }} awaiting your decision · approving records your decision and sends nothing</p>
{% if error and not error_in_rows %}<p class="note bad" role="alert">{{ error }}</p>{% endif %}
{% for r in rows %}
<div class="card" id="{{ ('doc-%s' % r.subject_id) if r.doc else ('item-%s' % r.approval_id) }}">
  <div class="row1"><span class="flag">{{ r.subject_type }}</span>
    <span class="title">{{ r.summary }}</span></div>
  <div class="meta">requested {{ r.requested_at[:16].replace('T',' ') }}
    {%- if r.doc and r.doc.job_id %} · <a class="plain" href="/job/{{ r.doc.job_id }}">job page</a>{% endif %}</div>

  {% if r.doc %}
    {% set c = r.doc.compare %}
    {% if r.doc.problem %}<p class="note bad">{{ r.doc.problem }}</p>{% endif %}
    {% if c %}
      {% if c.flagged %}<p class="note bad">{{ c.flagged }} line(s) say something your profile does not. Read the highlighted rows before you decide.</p>
      {% else %}<p class="note good">Every line traces back to your profile. Only you know whether each one is true, so read it anyway.</p>{% endif %}
      {% if c.summary %}
      <div class="pair {{ 'flag-row' if c.summary.status == 'flag' }}">
        <div><div class="lbl">Summary on the draft</div><p>{{ c.summary.text }}</p></div>
        <div><div class="lbl">Words not in your profile</div>
          <p>{% for w in c.summary.added %}<mark>{{ w }}</mark>{{ ', ' if not loop.last }}{% else %}none{% endfor %}</p></div>
      </div>{% endif %}
      {% for b in c.bullets %}
      <div class="pair {{ 'flag-row' if b.status in ('flag','unsourced') }}">
        <div><div class="lbl">On the draft · {{ b.note }}</div><p>{{ b.text }}</p>
          {% if b.added %}<p class="meta">not in your profile: {% for w in b.added %}<mark>{{ w }}</mark>{{ ', ' if not loop.last }}{% endfor %}</p>{% endif %}</div>
        <div><div class="lbl">Your profile{% if b.source_id %} · {{ b.source_id }}{% endif %}</div>
          <p>{{ b.source or 'no matching bullet' }}</p></div>
      </div>
      {% endfor %}
      {% if c.missing_sources %}<p class="note bad">Listed bullets no longer in your profile: {{ c.missing_sources|join(', ') }}</p>{% endif %}
    {% endif %}
    {% if r.doc.paragraphs %}
    <details><summary>Read the whole draft</summary><div class="doc">
      {% for p in r.doc.paragraphs %}
        {% if p.kind == 'heading' %}<h3>{{ p.text }}</h3>
        {% elif p.kind == 'bullet' %}<ul><li>{{ p.text }}</li></ul>
        {% else %}<p>{{ p.text }}</p>{% endif %}
      {% endfor %}</div></details>
    {% endif %}
    {% if r.doc.servable %}<div class="inline"><a class="btn" href="/document/{{ r.subject_id }}">Download .docx</a></div>{% endif %}
  {% elif r.body %}
    <div class="doc" style="margin-top:8px"><p style="white-space:pre-wrap">{{ r.body }}</p></div>
  {% endif %}

  <form method="post" action="/approve" class="inline" style="margin-top:12px">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <input type="hidden" name="approval_id" value="{{ r.approval_id }}">
    <button type="submit">Approve</button>
  </form>
  <form method="post" action="/reject" class="inline">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <input type="hidden" name="approval_id" value="{{ r.approval_id }}">
    <input type="search" name="feedback" id="feedback-{{ r.approval_id }}"
           aria-label="What should change" placeholder="What should change? (required)" style="flex:1">
    <button class="ghost" type="submit">Reject</button>
  </form>
  {% if error_id == r.approval_id %}<p class="note bad" role="alert">{{ error }}</p>{% endif %}
</div>
{% else %}<p class="empty">Nothing waiting.</p>{% endfor %}
{% endblock %}"""

env = Environment(
    loader=DictLoader({"base": BASE, "matches": MATCHES, "job": JOB,
                       "prep": PREP, "pipeline": PIPELINE, "review": REVIEW}),
    # Always on. select_autoescape(["html"]) keys on the template NAME, and
    # these are named "base", "job"... so it was silently off, and a job
    # description from a third-party board rendered as live HTML.
    autoescape=True,
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


def _json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


# Hostnames a request may name. See the module docstring.
ALLOWED_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def host_allowed(host_header: str) -> bool:
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        name, _, rest = host.partition("]")
        name += "]"
    else:
        name, _, port = host.partition(":")
        rest = ":" + port if port else ""
    if rest and not (rest.startswith(":") and rest[1:].isdigit()):
        return False
    return name in ALLOWED_HOSTS


def create_app(db_path: Path | None = None, output_dir: Path | None = None,
               profile_loader: Callable[[], dict[str, Any]] | None = None) -> FastAPI:
    """The three arguments exist for tests. Served, the app uses the
    configured tracker, output/ and profile."""
    app = FastAPI(title="Job Search Review")
    app.state.csrf_token = secrets.token_urlsafe(32)

    def connect() -> sqlite3.Connection:
        return db.connect(db_path) if db_path is not None else db.connect()

    def out_dir() -> Path:
        from . import config
        return Path(output_dir) if output_dir is not None else config.OUTPUT_DIR

    def profile() -> dict[str, Any] | None:
        try:
            if profile_loader is not None:
                return profile_loader()
            from .config import load_profile
            return load_profile()
        except Exception:  # noqa: BLE001 - a missing profile degrades the page
            return None

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if not host_allowed(request.headers.get("host", "")):
            return PlainTextResponse("Unrecognised host.", status_code=400)
        if request.method == "POST":
            # The raw body, not request.form(): parsing the form here consumes
            # it, and the route then receives nothing.
            body = (await request.body()).decode("utf-8", "replace")
            sent = parse_qs(body).get("csrf", [""])[0]
            if not secrets.compare_digest(sent, app.state.csrf_token):
                return PlainTextResponse(
                    "This form has expired. Reload the page and try again.",
                    status_code=403)
        return await call_next(request)

    def render(name: str, page: str, **ctx) -> HTMLResponse:
        con = connect()
        try:
            ctx.setdefault("pending_count", _pending_count(con))
        finally:
            con.close()
        title = ctx.pop("title", page.title())
        html = env.get_template(name).render(
            page=page, title=title, csrf=app.state.csrf_token, **ctx)
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    def not_found(what: str) -> HTMLResponse:
        return HTMLResponse(f"<p>No such {what}.</p>", status_code=404)

    def document_status(con, document_id: int) -> tuple[str, str | None]:
        row = con.execute(
            "SELECT decision, feedback FROM approvals WHERE subject_type = 'document' "
            "AND subject_id = ? ORDER BY id DESC LIMIT 1", (document_id,)).fetchone()
        return (row["decision"], row["feedback"]) if row else ("not queued", None)

    def inspect_document(row, prof) -> dict[str, Any]:
        """Everything the pages show about one document. Never raises."""
        info: dict[str, Any] = {"servable": False, "paragraphs": [],
                                "compare": None, "problem": None,
                                "job_id": row["job_id"]}
        path = review.safe_document_path(row["path"], out_dir())
        if path is None:
            info["problem"] = "The file for this draft is missing from output/."
            return info
        info["servable"] = True
        try:
            info["paragraphs"] = review.readable(path)
        except Exception:  # noqa: BLE001 - a corrupt file is reported, not raised
            info["problem"] = "This file could not be read as a Word document."
            return info
        if prof is None:
            info["problem"] = ("Your profile could not be loaded, so this draft "
                               "cannot be checked against it.")
            return info
        info["compare"] = review.compare(
            info["paragraphs"], _json_list(row["bullet_ids"]), prof)
        return info

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

        con = connect()
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
    def job_detail(job_id: int, msg: str = "", bad: int = 0):
        prof = profile()
        sources: dict[str, Any] = {}
        if prof is not None:
            from .tailor import collect_bullets
            sources = collect_bullets(prof)
        con = connect()
        try:
            row = con.execute(
                "SELECT j.*, c.name AS company FROM jobs j "
                "JOIN companies c ON c.id = j.company_id WHERE j.id = ?",
                (job_id,)).fetchone()
            if row is None:
                return not_found("job")
            application = con.execute(
                "SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
            documents = []
            # documents.job_id is authoritative (ADR 0003 decision 2).
            for d in con.execute(
                    "SELECT * FROM documents WHERE job_id = ? "
                    "ORDER BY version DESC, id DESC", (job_id,)).fetchall():
                status, feedback = document_status(con, d["id"])
                info = inspect_document(d, prof)
                documents.append({
                    **dict(d), "status": status, "feedback": feedback,
                    "gaps": _json_list(d["keywords_missing"]),
                    "bullets": [{"id": i, "text": sources[i].text if i in sources else None}
                                for i in _json_list(d["bullet_ids"])],
                    "servable": info["servable"],
                    "flagged": (info["compare"] or {}).get("flagged", 0),
                })
            preps = []
            if application is not None:
                for p in con.execute(
                        "SELECT * FROM interview_prep WHERE application_id = ? "
                        "AND archived_at IS NULL ORDER BY id DESC",
                        (application["id"],)).fetchall():
                    preps.append({**dict(p), "count": len(_json_list(p["questions"]))})
        finally:
            con.close()
        return render("job", "matches", title=row["title"], job=dict(row),
                      stack=_json_list(row["tech_stack"]),
                      application=dict(application) if application else None,
                      documents=documents, preps=preps, msg=msg, bad=bad)

    def back_to_job(job_id: int, msg: str, bad: bool) -> RedirectResponse:
        query = urlencode({"msg": msg, "bad": int(bad)})
        return RedirectResponse(f"/job/{job_id}?{query}", status_code=303)

    @app.post("/job/{job_id}/save")
    def do_save(job_id: int):
        con = connect()
        try:
            _, created = approvals.save_application(con, job_id)
            con.commit()
        except approvals.ApprovalError as exc:
            return back_to_job(job_id, str(exc), True)
        finally:
            con.close()
        return back_to_job(job_id, "Saved as an application." if created
                           else "Already saved.", False)

    @app.post("/job/{job_id}/tailor")
    def do_tailor(job_id: int, kind: str = Form("resume")):
        from .drafting import DraftError, draft_document
        if kind not in ("resume", "cover_letter"):
            return back_to_job(job_id, f"Unknown document kind {kind!r}.", True)
        prof = profile()
        if prof is None:
            return back_to_job(job_id, "Your profile could not be loaded.", True)
        con = connect()
        try:
            # Pressing the button is the explicit request for a new version,
            # which is what --force means on the command line.
            result = draft_document(con, job_id, kind, force=True, profile=prof)
        except DraftError as exc:
            prefix = "Refused by a safety check: " if exc.refused else ""
            return back_to_job(job_id, prefix + str(exc), True)
        finally:
            con.close()
        parts = [f"Drafted {kind.replace('_', ' ')} v{result.version}. "
                 "It is waiting in the review queue."]
        if result.revert_reasons:
            parts.append(f"{len(result.revert_reasons)} rewrite(s) went back to "
                         "your own words.")
        if result.gaps:
            parts.append("Gaps: " + ", ".join(result.gaps) + ".")
        return back_to_job(job_id, " ".join(parts), False)

    @app.get("/document/{document_id}")
    def download(document_id: int):
        con = connect()
        try:
            row = con.execute("SELECT path FROM documents WHERE id = ?",
                              (document_id,)).fetchone()
        finally:
            con.close()
        path = review.safe_document_path(row["path"], out_dir()) if row else None
        if path is None:
            # One answer for "no such row" and "the row points somewhere it
            # may not": the difference is not the requester's business.
            return not_found("document")
        return FileResponse(path, filename=path.name, media_type=DOCX_TYPE,
                            headers={"Cache-Control": "no-store"})

    @app.get("/prep/{prep_id}", response_class=HTMLResponse)
    def prep_detail(prep_id: int):
        con = connect()
        try:
            row = con.execute(
                "SELECT p.*, a.job_id, j.title, c.name AS company "
                "FROM interview_prep p JOIN applications a ON a.id = p.application_id "
                "JOIN jobs j ON j.id = a.job_id JOIN companies c ON c.id = j.company_id "
                "WHERE p.id = ?", (prep_id,)).fetchone()
        finally:
            con.close()
        if row is None:
            return not_found("interview prep")
        questions = [q for q in _json_list(row["questions"]) if isinstance(q, dict)]
        return render("prep", "matches", title="Interview prep",
                      prep=dict(row), questions=questions)

    @app.get("/pipeline", response_class=HTMLResponse)
    def pipeline():
        con = connect()
        try:
            # v_pipeline has no job_id, and the job page is where documents
            # live. Joined here so existing trackers need no migration.
            rows = [dict(r) for r in con.execute(
                "SELECT p.*, a.job_id AS job_id FROM v_pipeline p "
                "JOIN applications a ON a.id = p.application_id").fetchall()]
        finally:
            con.close()
        return render("pipeline", "pipeline", rows=rows)

    @app.get("/review", response_class=HTMLResponse)
    def review_queue(error: str = "", error_id: int = 0):
        prof = profile()
        con = connect()
        try:
            rows = []
            for r in con.execute("SELECT * FROM v_awaiting_approval").fetchall():
                item = dict(r)
                if r["subject_type"] == "document":
                    d = con.execute("SELECT * FROM documents WHERE id = ?",
                                    (r["subject_id"],)).fetchone()
                    item["doc"] = inspect_document(d, prof) if d else {
                        "problem": "This draft's record is missing.",
                        "servable": False, "paragraphs": [], "compare": None,
                        "job_id": None}
                elif r["subject_type"] == "outreach":
                    o = con.execute("SELECT draft_body FROM outreach WHERE id = ?",
                                    (r["subject_id"],)).fetchone()
                    item["body"] = o["draft_body"] if o else None
                rows.append(item)
        finally:
            con.close()
        return render("review", "review", title="Review", rows=rows,
                      error=error, error_id=error_id,
                      error_in_rows=any(r["approval_id"] == error_id for r in rows))

    def back_to_review(error: str = "", approval_id: int = 0) -> RedirectResponse:
        if not error:
            return RedirectResponse("/review", status_code=303)
        query = urlencode({"error": error, "error_id": approval_id})
        return RedirectResponse(f"/review?{query}", status_code=303)

    # Both call exactly what `jsa approve` / `jsa reject` call. ADR 0003
    # decision 5: one path to a human decision.
    @app.post("/approve")
    def do_approve(approval_id: int = Form(...)):
        con = connect()
        try:
            approvals.approve(con, approval_id)
            con.commit()
        except approvals.ApprovalError as exc:
            return back_to_review(str(exc), approval_id)
        finally:
            con.close()
        return back_to_review()

    @app.post("/reject")
    def do_reject(approval_id: int = Form(...), feedback: str = Form("")):
        con = connect()
        try:
            approvals.reject(con, approval_id, feedback)
            con.commit()
        except approvals.ApprovalError as exc:
            return back_to_review(str(exc), approval_id)
        finally:
            con.close()
        return back_to_review()

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
