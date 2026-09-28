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
  select,input[type=search],input[type=text],input[type=url],textarea{font:inherit;
    font-size:13px;padding:6px 8px;border:1px solid var(--rule);border-radius:3px;
    background:var(--surface);color:var(--ink);min-width:0}
  form.stack{display:grid;gap:10px;background:var(--surface);border:1px solid var(--rule);
    border-radius:4px;padding:14px 16px;margin-bottom:10px}
  form.stack label{display:grid;gap:4px;font-size:12.5px;color:var(--ink-2)}
  form.stack .two{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px}
  form.stack button{justify-self:start}
  label.pair-in{display:flex;gap:6px;align-items:center;font-size:13px;
    color:var(--ink-2);white-space:nowrap}
  input.miles{width:110px;accent-color:var(--copper)}
  input.miles:disabled{opacity:.45}
  output{font-variant-numeric:tabular-nums;min-width:66px}
  /* The map. Drawn from shipped data on this machine: no tiles, so no
     request tells anybody where the operator lives. */
  figure.map{margin:0 0 14px;background:var(--surface);border:1px solid var(--rule);
    border-radius:4px;padding:10px 10px 6px}
  figure.map svg{display:block;width:100%;height:auto;max-height:62vh}
  figure.map figcaption{color:var(--mute);font-size:12.5px;padding:4px 4px 2px;
    overflow-wrap:anywhere}
  .land{fill:var(--surface-2);stroke:var(--rule);stroke-width:.8}
  .ring{fill:var(--sage);fill-opacity:.08;stroke:var(--sage);stroke-width:1.2;
    stroke-dasharray:5 4}
  .dot{stroke:var(--surface);stroke-width:.8}
  .dot.in{fill:var(--copper);fill-opacity:.8}
  .dot.out{fill:var(--mute);fill-opacity:.4}
  figure.map a:focus-visible .dot{stroke:var(--copper);stroke-width:2.5}
  .home{fill:var(--ink)}
  .town{font-size:10px;fill:var(--ink-2);text-anchor:middle;paint-order:stroke;
    stroke:var(--surface);stroke-width:2.5px}
  .town.left{text-anchor:start}
  .bar{fill:var(--ink-2)}
  textarea{min-height:220px;resize:vertical;line-height:1.45}
  /* The preview is a sheet of paper in either theme: it shows the document as
     it prints, and a resume is black on white. */
  .desk{background:var(--surface-2);border-radius:4px;padding:24px 12px;overflow-x:auto}
  .sheet{background:#fff;color:#111;max-width:8.5in;margin:0 auto;
    box-shadow:0 1px 3px rgba(0,0,0,.18),0 8px 24px rgba(0,0,0,.08);
    padding-block:7%;line-height:1.25;overflow-wrap:anywhere}
  .sheet p{margin:0;white-space:pre-wrap}
  .sheet ul{margin:0;padding-left:1.5em}
  .sheet .blank{height:1.1em}
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
    form.stack .two{grid-template-columns:minmax(0,1fr)}
    .desk{padding:12px 0}
  }
  @media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body>
<nav><div class="wrap">
  <a href="/" class="{{ 'on' if page=='matches' }}">Matches</a>
  <a href="/pipeline" class="{{ 'on' if page=='pipeline' }}">Pipeline</a>
  <a href="/review" class="{{ 'on' if page=='review' }}">Review{% if pending_count %} ({{ pending_count }}){% endif %}</a>
  <a href="/add" class="{{ 'on' if page=='add' }}">Add a job</a>
</div></nav>
<div class="wrap">{% block body %}{% endblock %}</div>
</body></html>"""

MATCHES = """{% extends "base" %}{% block body %}
<h1>Matches</h1>
<p class="sub">{{ total }} unreviewed · showing {{ rows|length }}
  {%- if home %} · within {{ radius }} miles of {{ home }}:
    {{ near_count }} near you, {{ rows|length - near_count }} remote{% endif %}</p>
{% if home_problem %}<p class="note bad" role="alert">{{ home_problem }}</p>{% endif %}
{% if hidden %}<p class="sub">{{ hidden }} match(es) hidden by the radius.
  {%- if unplaced %} {{ unplaced }} of them name a place this could not find, so the distance is unknown rather than far.{% endif %}
  <a class="plain" href="{{ nationwide_url }}">Show them anyway</a></p>{% endif %}
<form class="filters" method="get" id="where">
  <label class="pair-in" for="f-radius">within
    <input type="range" name="radius" id="f-radius" class="miles"
           min="{{ radius_min }}" max="{{ radius_max }}" step="5"
           value="{{ radius or profile_radius }}" aria-label="How far you would go">
    <output for="f-radius" id="f-radius-out">{{ radius or profile_radius }} miles</output>
  </label>
  <label class="pair-in" for="f-home">of
    <input type="search" name="home" id="f-home" value="{{ home_text }}"
           placeholder="ZIP or City, ST" size="16"
           aria-label="Where you are">
  </label>
  <label class="pair-in" for="f-anywhere">
    <input type="checkbox" name="anywhere" id="f-anywhere" value="1"
           {{ 'checked' if not radius }}> anywhere in the US
  </label>
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
{% if map.drawn %}
<figure class="map">
  <svg viewBox="0 0 {{ map.width }} {{ map.height }}" role="img" aria-label="{{ map_note }}">
    {% for d in map.paths %}<path class="land" d="{{ d }}"/>{% endfor %}
    {% if map.circle %}<circle id="ring" class="ring" cx="{{ map.width // 2 }}"
      cy="{{ map.height // 2 }}" r="{{ '%.1f'|format(map.circle) }}"
      data-per-mile="{{ '%.6f'|format(map.per_mile) }}"/>{% endif %}
    {% for b in map.bubbles %}<a href="/job/{{ b.job_id }}"><circle
      class="dot {{ 'in' if b.inside else 'out' }}" cx="{{ '%.1f'|format(b.x) }}"
      cy="{{ '%.1f'|format(b.y) }}" r="{{ '%.1f'|format(b.r) }}"><title>{{ b.place }}: {{ b.count }} posting(s){% if b.miles is not none %}, {{ b.miles }} miles away{% endif %}</title></circle></a>
    {% endfor %}
    {% if map.home %}<circle class="home" cx="{{ '%.1f'|format(map.home[0]) }}"
      cy="{{ '%.1f'|format(map.home[1]) }}" r="4"><title>{{ map.home_name }}</title></circle>{% endif %}
    {% for l in map.labels %}<text class="town" x="{{ '%.1f'|format(l.x) }}"
      y="{{ '%.1f'|format(l.y) }}">{{ l.text }}</text>{% endfor %}
    <rect class="bar" x="16" y="{{ map.height - 22 }}" width="{{ '%.1f'|format(map.scale_px) }}" height="3"/>
    <text class="town left" x="16" y="{{ map.height - 28 }}">{{ map.scale_miles }} miles</text>
  </svg>
  <figcaption>{{ map_note }}
    {%- if map.kind == 'local' and map.off_map %}
    <a class="plain" href="{{ nationwide_url }}">See the whole country</a>{% endif %}</figcaption>
</figure>
{% endif %}
{% for r in rows %}
<div class="card">
  <div class="row1">
    <span class="score">{{ '%.2f'|format(r.match_score or 0) }}</span>
    <span class="title"><a class="plain" href="/job/{{ r.job_id }}">{{ r.title }}</a></span>
    <span class="co">{{ r.company }}</span>
  </div>
  <div class="meta">{{ r.location or 'location not stated' }} · {{ r.remote }}
    {%- if r.miles is not none and r.remote != 'remote' %} · {{ r.miles }} mi away
      {%- if r.via_copy %} (its nearest location){% endif %}{% endif %}
    {%- if r.variant_count and r.variant_count > 1 %} · +{{ r.variant_count - 1 }} more location(s){% endif %}</div>
  <div class="flags">
    {% if r.track == 'sales' %}<span class="flag">sales track</span>{% endif %}
    {% if r.degree_required == 1 %}<span class="flag" title="Shown so you know, never used to hide or rank a job">asks for a degree</span>
    {% elif r.degree_required == 0 %}<span class="flag ok">no degree needed</span>{% endif %}
    {% if r.clearance_required == 1 %}<span class="flag warn">clearance</span>{% endif %}
    {% if r.years_required is not none %}<span class="flag">{{ r.years_required }}+ yrs</span>{% endif %}
    {% for t in r.stack %}<span class="flag">{{ t }}</span>{% endfor %}
  </div>
  {% if r.reasons %}<ul class="reasons">{% for x in r.reasons %}<li>{{ x }}</li>{% endfor %}</ul>{% endif %}
</div>
{% else %}<p class="empty">Nothing matches those filters.</p>{% endfor %}
<script>
/* The slider, live. Everything here is presentation: it resizes the drawn
   circle and relabels it while the handle moves, and submits the form when
   the handle is released so the server answers the question again.

   It must never decide which postings match. That answer comes from the
   server, and a browser quietly disagreeing with it is exactly the bug this
   map exists to make impossible. Without this script the page still works:
   the slider and the checkbox are form controls and "Filter" submits them. */
(function () {
  var form = document.getElementById('where');
  if (!form) { return; }
  var miles = form.querySelector('#f-radius');
  var shown = form.querySelector('#f-radius-out');
  var anywhere = form.querySelector('#f-anywhere');
  var ring = document.getElementById('ring');
  var perMile = ring ? parseFloat(ring.getAttribute('data-per-mile')) : 0;
  function paint() {
    shown.textContent = miles.value + ' miles';
    miles.disabled = anywhere.checked;
    if (ring && perMile) { ring.setAttribute('r', (miles.value * perMile).toFixed(1)); }
  }
  miles.addEventListener('input', paint);
  miles.addEventListener('change', function () {
    anywhere.checked = false;
    form.submit();
  });
  anywhere.addEventListener('change', function () { paint(); form.submit(); });
  paint();
})();
</script>
{% endblock %}"""

JOB = """{% extends "base" %}{% block body %}
<h1>{{ job.title }}</h1>
<p class="sub">{{ job.company }} · {{ job.location or 'location not stated' }} · {{ job.remote }}</p>
<div class="flags" style="margin-bottom:14px">
  {% if application %}<span class="flag ok">application: {{ application.status }}</span>{% endif %}
  {% if job.degree_required == 1 %}<span class="flag" title="Shown so you know, never used to hide or rank a job">asks for a degree</span>
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
  <div class="meta">drafted {{ d.generated_at|localtime }} · {{ d.model or 'model not recorded' }}</div>
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
    {% if d.servable %}<a class="btn" href="/document/{{ d.id }}/preview">Preview</a>
    <a class="btn" href="/document/{{ d.id }}">Download .docx</a>
    {% else %}<span class="meta">The file is missing from output/.</span>{% endif %}
    {% if d.status == 'pending' %}<a class="plain" href="/review#doc-{{ d.id }}">Review it</a>{% endif %}
  </div>
</div>
{% else %}<p class="empty">No documents drafted for this job yet.</p>{% endfor %}

<h2>Interview prep</h2>
{% for p in preps %}
<div class="card"><div class="row1">
  <span class="title"><a class="plain" href="/prep/{{ p.id }}">{{ (p.round or 'general')|replace('_',' ')|capitalize }}</a></span>
  <span class="co">{{ p.count }} question(s) · {{ p.generated_at|localdate }}</span></div></div>
{% else %}<p class="empty">No interview prep yet.{% if application %} Run <code>jsa prep {{ application.id }}</code> to draft one.{% endif %}</p>{% endfor %}

<h2>Posting</h2>
{% if job.enrichment_note %}<p class="sub">{{ job.enrichment_note }}</p>{% endif %}
<p><a class="plain" href="{{ job.url }}" rel="noopener">Open the original posting</a></p>
<div class="jd">{{ job.description or 'No description captured.' }}</div>
{% endblock %}"""

PREP = """{% extends "base" %}{% block body %}
<p class="sub" style="margin-top:18px"><a class="plain" href="/job/{{ prep.job_id }}">← {{ prep.title }} at {{ prep.company }}</a></p>
<h1>Interview prep: {{ (prep.round or 'general')|replace('_',' ') }}</h1>
<p class="sub">{{ questions|length }} question(s) · drafted {{ prep.generated_at|localdate }} · the answers are notes in your own words, not a script</p>
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
<p class="sub">{{ total }} live application(s){% if overdue %} · <strong>{{ overdue }} overdue</strong>{% endif %} · moving a stage records that YOU said so</p>
{% if msg %}<p class="note {{ 'bad' if bad else 'good' }}" role="status">{{ msg }}</p>{% endif %}
{% for stage, items in groups %}
<h2>{{ stage|replace('_',' ') }} · {{ items|length }}</h2>
{% for r in items %}
<div class="card">
  <div class="row1">
    <span class="title"><a class="plain" href="/job/{{ r.job_id }}">{{ r.title }}</a></span>
    <span class="co">{{ r.company }}</span>
    {% if r.days_out is not none and r.days_out < 0 %}<span class="flag warn">overdue {{ -r.days_out }}d</span>
    {% elif r.days_out == 0 %}<span class="flag warn">due today</span>
    {% elif r.days_out is not none %}<span class="flag">due in {{ r.days_out }}d</span>{% endif %}
    {% if r.quiet %}<span class="flag warn">quiet {{ r.quiet }}d</span>{% endif %}
  </div>
  <div class="meta">{{ r.next_action or 'no next action set' }}
    {%- if r.next_action_due %} · due {{ r.next_action_due }}{% endif %}
    {%- if r.last_activity_at %} · last activity {{ r.last_activity_at|localdate }}{% endif %}</div>
  <form method="post" action="/job/{{ r.job_id }}/stage" class="inline">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <select name="stage" aria-label="Stage for {{ r.title }}">
      {% for s in stages %}<option value="{{ s }}" {{ 'selected' if s == r.status }}>{{ s|replace('_',' ') }}</option>{% endfor %}
    </select>
    <button class="ghost" type="submit">Move</button>
  </form>
</div>
{% endfor %}
{% else %}<p class="empty">Nothing in the pipeline yet. Save a match to start one.</p>{% endfor %}
{% if closed %}
<h2>closed · {{ closed|length }}</h2>
{% for r in closed %}
<div class="card"><div class="row1"><span class="flag">{{ r.status }}</span>
  <span class="title"><a class="plain" href="/job/{{ r.job_id }}">{{ r.title }}</a></span>
  <span class="co">{{ r.company }}</span></div>
  <div class="meta">history kept{% if r.last_activity_at %} · last activity {{ r.last_activity_at|localdate }}{% endif %}</div>
</div>
{% endfor %}
{% endif %}
{% endblock %}"""

REVIEW = """{% extends "base" %}{% block body %}
<h1>Review queue</h1>
<p class="sub">{{ rows|length }} awaiting your decision · approving records your decision and sends nothing</p>
{% if error and not error_in_rows %}<p class="note bad" role="alert">{{ error }}</p>{% endif %}
{% for r in rows %}
<div class="card" id="{{ ('doc-%s' % r.subject_id) if r.doc else ('item-%s' % r.approval_id) }}">
  <div class="row1"><span class="flag">{{ r.subject_type }}</span>
    <span class="title">{{ r.summary }}</span></div>
  <div class="meta">requested {{ r.requested_at|localtime }}
    {%- if r.doc and r.doc.job_id %} · <a class="plain" href="/job/{{ r.doc.job_id }}">job page</a>{% endif %}</div>

  {% if r.doc %}
    {% set c = r.doc.compare %}
    {% if r.doc.problem %}<p class="note bad">{{ r.doc.problem }}</p>{% endif %}
    {% if r.doc.kind == 'cover_letter' %}
      {% if r.doc.note %}<p class="note bad">{{ r.doc.note }}</p>
      {% else %}<p class="note good">Every word of this letter traces to your profile, the role title or the company name. Read it anyway: only you know whether it is true.</p>{% endif %}
      {% if r.doc.letter and r.doc.letter.problems %}
        <p class="note bad">Still flagged: {{ r.doc.letter.problems|join('; ') }}</p>{% endif %}
      <div class="doc">{% for p in r.doc.paragraphs %}<p>{{ p.text }}</p>{% endfor %}</div>
    {% endif %}
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
    {% if r.doc.paragraphs and r.doc.kind != 'cover_letter' %}
    <details><summary>Read the whole draft</summary><div class="doc">
      {% for p in r.doc.paragraphs %}
        {% if p.kind == 'heading' %}<h3>{{ p.text }}</h3>
        {% elif p.kind == 'bullet' %}<ul><li>{{ p.text }}</li></ul>
        {% else %}<p>{{ p.text }}</p>{% endif %}
      {% endfor %}</div></details>
    {% endif %}
    {% if r.doc.servable %}<div class="inline"><a class="btn" href="/document/{{ r.subject_id }}/preview">Preview as a page</a>
      <a class="btn" href="/document/{{ r.subject_id }}">Download .docx</a></div>{% endif %}
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

PREVIEW = """{% extends "base" %}{% block body %}
<p class="sub" style="margin-top:18px"><a class="plain" href="/job/{{ doc.job_id }}">← {{ job_title }} at {{ company }}</a></p>
<h1>{{ doc.kind|replace('_',' ')|capitalize }} v{{ doc.version }}</h1>
<p class="sub">document {{ doc.id }} · {{ status }} · drafted {{ doc.generated_at|localtime }} · read from the .docx on disk, so this is the file you would send</p>
<div class="inline" style="margin-bottom:12px">
  <a class="btn" href="/document/{{ doc.id }}">Download .docx</a>
  {% if status == 'pending' %}<a class="plain" href="/review#doc-{{ doc.id }}">Review it</a>{% endif %}
</div>
{% if problem %}<p class="note bad">{{ problem }}</p>{% else %}
<div class="desk"><div class="sheet" style="font-family:'{{ sheet.font }}',Carlito,'Segoe UI',Arial,sans-serif;font-size:{{ sheet.size }}pt;padding-left:{{ sheet.margin_left_pct }}%;padding-right:{{ sheet.margin_right_pct }}%">
{%- for p in sheet.paragraphs %}
  {%- if p.empty %}<div class="blank"></div>
  {%- else %}
  {%- set style = 'text-align:%s;margin-top:%spt;margin-bottom:%spt' % (p.align, p.before, p.after) %}
  {%- set body %}{% for r in p.runs %}<span style="font-size:{{ r.size }}pt{{ ';font-weight:700' if r.bold }}{{ ';font-style:italic' if r.italic }}">{{ r.text }}</span>{% endfor %}{% endset %}
  {%- if p.bullet %}<ul style="{{ style }}"><li>{{ body }}</li></ul>
  {%- else %}<p style="{{ style }}">{{ body }}</p>{% endif %}
  {%- endif %}
{%- endfor %}
</div></div>
<p class="sub" style="margin-top:10px">Fonts and line breaks can differ slightly from Word. The words, their order and their emphasis are exactly the file's.</p>
{% endif %}
{% endblock %}"""

ADD = """{% extends "base" %}{% block body %}
<h1>Add a job</h1>
<p class="sub">For a posting discovery did not find. It is stored and scored like any other, and nothing becomes an application until you save it.</p>
{% if msg %}<p class="note {{ 'bad' if bad else 'good' }}" role="alert">{{ msg }}</p>{% endif %}

<h2>From a link</h2>
<form class="stack" method="post" action="/add/link" onsubmit="var b=this.querySelector('button');b.disabled=true;b.textContent='Reading the posting…'">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <label for="add-url">Posting link on Greenhouse, Lever, Ashby or Workday
    <input type="url" name="url" id="add-url" required placeholder="https://job-boards.greenhouse.io/company/jobs/1234567"></label>
  <label for="add-company">Company name (optional; otherwise taken from the board)
    <input type="text" name="company" id="add-company"></label>
  <button type="submit">Add from link</button>
  <p class="meta" style="margin:0">The posting is read from that board's public job API, not from the page itself. A LinkedIn, Indeed or company-site link is not fetched: paste the posting below instead.</p>
</form>

<h2>Paste a posting</h2>
<form class="stack" method="post" action="/add/paste" onsubmit="var b=this.querySelector('button');b.disabled=true;b.textContent='Adding…'">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <div class="two">
    <label for="paste-company">Company<input type="text" name="company" id="paste-company" required></label>
    <label for="paste-title">Job title<input type="text" name="title" id="paste-title" required></label>
  </div>
  <div class="two">
    <label for="paste-location">Location<input type="text" name="location" id="paste-location" placeholder="Remote (US), or City, ST"></label>
    <label for="paste-link">Where it is posted (optional)<input type="url" name="link" id="paste-link"></label>
  </div>
  <label for="paste-text">The whole posting, requirements included
    <textarea name="text" id="paste-text" required minlength="{{ min_chars }}"></textarea></label>
  <button type="submit">Add pasted posting</button>
</form>
{% endblock %}"""

env = Environment(
    loader=DictLoader({"base": BASE, "matches": MATCHES, "job": JOB,
                       "prep": PREP, "pipeline": PIPELINE, "review": REVIEW,
                       "preview": PREVIEW, "add": ADD}),
    # Always on. select_autoescape(["html"]) keys on the template NAME, and
    # these are named "base", "job"... so it was silently off, and a job
    # description from a third-party board rendered as live HTML.
    autoescape=True,
)
env.filters["localtime"] = db.local_time
env.filters["localdate"] = lambda stamp: str(db.local_date(stamp) or stamp or "")


# How far the slider goes. Below five miles a radius is a postcode, not a
# commute; above three hundred it is a move, and `jsa.config.parse_radius`
# says the same thing to a profile.
RADIUS_MIN, RADIUS_MAX = 5, 300


def _radius(typed: str, anywhere: str) -> float | None:
    """Miles, or None for the whole country.

    Empty means nationwide, which is what n18's links and the "Show them
    anyway" link say. A value outside the slider's range is clamped rather
    than refused: it came from a URL somebody typed, and the honest response
    to "within 5000 miles" is the largest radius there is.
    """
    if anywhere:
        return None
    text = (typed or "").strip()
    if not text:
        return None
    try:
        miles = float(text)
    except ValueError:
        return None
    return min(float(RADIUS_MAX), max(float(RADIUS_MIN), miles))


def _map_note(view) -> str:
    """What the map is not showing, in words. Nothing is dropped in silence."""
    places_shown = len(view.bubbles)
    if view.kind == "local":
        bits = [f"{view.shown} posting(s) in {places_shown} place(s) on this map"]
        if view.off_map:
            bits.append(f"{view.off_map} further out than it reaches")
    else:
        bits = [f"{view.shown} posting(s) in {places_shown} place(s) "
                "across the lower 48"]
        if view.off_map:
            bits.append(f"{view.off_map} in Alaska, Hawaii or Puerto Rico, "
                        "which this map does not draw")
    if view.remote:
        bits.append(f"{view.remote} remote, with no distance to draw")
    if view.unplaced:
        bits.append(f"{view.unplaced} naming a place this could not find")
    return "; ".join(bits) + "."


def _origin(typed: str, profile: dict[str, Any] | None = None):
    """(Place, what to show in the box, a problem to report).

    Typed text wins; otherwise the profile's own origin, so the box is
    pre-filled with where the operator already said they are. The profile is
    passed in rather than loaded here: the app takes a profile_loader, and
    reading the real one behind its back is how a test passes while the
    feature is broken for anybody with a different profile.
    """
    from . import places

    typed = (typed or "").strip()
    if typed:
        found = places.origin(typed)
        if found is None:
            return None, typed, (
                f"{typed!r} is not a ZIP code or a US town this recognises, "
                "so the radius is off. Try a ZIP, or \"City, ST\".")
        return found, typed, ""
    try:
        from .config import Preferences

        found = Preferences.from_profile(profile or {}).home()
    except Exception:  # noqa: BLE001 - a missing profile is not an error here
        found = None
    return found, str(found) if found else "", ""


def _profile_radius(profile: dict[str, Any] | None) -> float:
    """How far the operator said they would go, or the shipped default."""
    from .config import DEFAULT_RADIUS_MILES, Preferences

    try:
        return Preferences.from_profile(profile or {}).radius_miles
    except Exception:  # noqa: BLE001 - a bad profile is not a reason to 500
        return DEFAULT_RADIUS_MILES


# How many cards one employer may take on the Matches page, so a board that
# posts four hundred roles cannot fill it.
PER_COMPANY = 3


def _per_company(rows, cap: int):
    """The first `cap` rows per company, order kept."""
    seen: dict[str, int] = {}
    out = []
    for row in rows:
        company = row.get("company") or ""
        if seen.get(company, 0) < cap:
            out.append(row)
            seen[company] = seen.get(company, 0) + 1
    return out


def _copies(con, rows) -> dict[str, list[tuple[str, str]]]:
    """Every copy's (location, remote) for the folded cards among `rows`.

    A card on the Matches page can stand for several postings of one req in
    different cities. Its own row is whichever scored best from the
    profile's home, which is not necessarily the one near the place somebody
    just typed into the box.
    """
    keys = sorted({r["dedup_key"] for r in rows
                   if r.get("dedup_key") and (r.get("variant_count") or 1) > 1})
    out: dict[str, list[tuple[str, str]]] = {}
    for start in range(0, len(keys), 500):          # SQLite's parameter limit
        chunk = keys[start:start + 500]
        for row in con.execute(
                "SELECT j.dedup_key, j.location, j.remote FROM jobs j "
                "WHERE j.archived_at IS NULL AND j.closed_at IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_id = j.id) "
                f"AND j.dedup_key IN ({','.join('?' * len(chunk))})", chunk):
            out.setdefault(row["dedup_key"], []).append(
                (row["location"] or "", row["remote"] or ""))
    return out


def _by_distance(rows, origin, radius: float | None, copies=None):
    """Filter to what is within the radius. Returns (rows, hidden, unplaced).

    A remote posting always passes: it has no distance, and hiding it behind
    a radius would be hiding the jobs that are open to everybody. A posting
    whose location cannot be placed is hidden when a radius is set -- but
    counted separately and named on the page, because "unknown" is not
    "far away".

    A folded card is judged by its NEAREST copy (`copies`, from _copies):
    "Deployed Engineer" with copies in Atlanta and Boston is near somebody
    in Boston even when the card's own row is the Atlanta one. A remote copy
    makes the whole card remote.

    The comparison is on the exact distance and only the DISPLAY is rounded.
    It used to compare the rounded number, so a job 25.4 miles away passed a
    25-mile radius while the map (which compares exactly) drew it outside
    the circle -- the one disagreement n19 said could not happen.
    """
    from . import places

    for row in rows:
        row["miles"] = row["exact_miles"] = None
        row["via_copy"] = False
        row["any_remote"] = (row.get("remote") or "") == "remote"
        spots = [(row.get("location") or "", False)]
        for location, remote in (copies or {}).get(row.get("dedup_key"), []):
            row["any_remote"] = row["any_remote"] or remote == "remote"
            spots.append((location, True))
        if origin is None:
            continue
        best = None
        for location, is_copy in spots:
            distance = places.nearest(origin, location)
            if distance is not None and (best is None or distance < best[0]):
                best = (distance, is_copy and location != spots[0][0])
        if best is not None:
            row["exact_miles"], row["via_copy"] = best
            row["miles"] = round(best[0])
    if radius is None or origin is None:
        return rows, 0, 0

    kept, hidden, unplaced = [], 0, 0
    for row in rows:
        if row["any_remote"] or (
                row["exact_miles"] is not None and row["exact_miles"] <= radius):
            kept.append(row)
            continue
        hidden += 1
        if row["exact_miles"] is None:
            unplaced += 1
    return kept, hidden, unplaced


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

    def job_row(job_id) -> dict[str, Any]:
        con = connect()
        try:
            row = con.execute(
                "SELECT j.id, j.title, j.location, c.name AS company FROM jobs j "
                "LEFT JOIN companies c ON c.id = j.company_id WHERE j.id = ?",
                (job_id,)).fetchone()
        finally:
            con.close()
        return dict(row) if row else {}

    def inspect_document(row, prof) -> dict[str, Any]:
        """Everything the pages show about one document. Never raises."""
        info: dict[str, Any] = {"servable": False, "paragraphs": [],
                                "compare": None, "problem": None,
                                "letter": None, "kind": row["kind"],
                                "note": row["note"],
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
        if row["kind"] == "cover_letter":
            # Prose has no source bullet to sit beside; what it has is a list
            # of words nothing in the profile supports. ADR 0007.
            from . import letter
            text = chr(10).join(p["text"] for p in info["paragraphs"])
            info["letter"] = {"problems": letter.check(text, prof,
                                                       job_row(row["job_id"]))}
            return info
        info["compare"] = review.compare(
            info["paragraphs"], _json_list(row["bullet_ids"]), prof)
        return info

    @app.get("/", response_class=HTMLResponse)
    def matches(request: Request, near: str = "", track: str = "",
                degree: str = "", remote: str = "", limit: int = 60,
                home: str = "", radius: str = "", anywhere: str = ""):
        from . import mapview
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

        origin, home_text, home_problem = _origin(home, profile())
        prefs_radius = _profile_radius(profile())
        # A bare visit to "/" is not a request for the whole country: it is
        # somebody opening their own dashboard, and their profile already
        # says how far they would go. Any query string is obeyed literally,
        # so every link on the page keeps meaning what it says.
        if not request.query_params and origin is not None:
            wanted = prefs_radius
        else:
            wanted = _radius(radius, anywhere)

        con = connect()
        try:
            # Every card, best first. The three-per-company cap is applied
            # AFTER the radius, in Python: applied first (it used to be, in
            # this SQL), a company's three slots went to its best cards
            # anywhere, the radius then hid them, and the one near you had
            # already been capped out. Measured in n20 at 40 miles: Austin
            # showed 2 nearby cards and now shows 6, Seattle 10 and now 15.
            listed = ("SELECT m.* FROM v_new_matches m WHERE "
                      f"{' AND '.join(where)} ORDER BY m.match_score DESC")
            rows = [_decode(r) for r in con.execute(listed, params).fetchall()]
            total = con.execute("SELECT COUNT(*) FROM v_new_matches").fetchone()[0]
            # The map draws every posting that survived the other filters,
            # uncapped and unlimited: it is a picture of where the work is,
            # and the top sixty is not that. Every COPY, not one per card
            # (n20): a card is kept when any of its copies is inside the
            # radius, so a dot inside the circle must exist for each one.
            drawn = [dict(r) for r in con.execute(
                "SELECT m.id AS job_id, m.title, m.location, m.remote "
                "FROM jobs m WHERE m.archived_at IS NULL "
                "AND m.closed_at IS NULL AND NOT EXISTS "
                "(SELECT 1 FROM applications a WHERE a.job_id = m.id) "
                f"AND {' AND '.join(where)}", params)]
            copies = _copies(con, rows)
        finally:
            con.close()

        view = mapview.build(drawn, origin, wanted)
        rows, hidden, unplaced = _by_distance(rows, origin, wanted, copies)
        rows = _per_company(rows, PER_COMPANY)[:limit]
        # Remote postings pass any radius, so without this the page can say
        # "within 25 miles" over a list that is mostly remote work.
        near_count = sum(1 for r in rows
                         if not r.get("any_remote")
                         and r.get("miles") is not None)
        return render("matches", "matches", rows=rows, total=total,
                      near=near, track=track,
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
                           {"anywhere": "1", "near": near, "track": track,
                            "degree": degree, "remote": remote,
                            "home": home_text}.items() if v}))

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

    def back_to_pipeline(msg: str, bad: bool) -> RedirectResponse:
        return RedirectResponse(
            "/pipeline?" + urlencode({"msg": msg, "bad": int(bad)}),
            status_code=303)

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

    @app.get("/document/{document_id}/preview", response_class=HTMLResponse)
    def preview(document_id: int):
        con = connect()
        try:
            row = con.execute(
                "SELECT d.*, j.title AS job_title, c.name AS company FROM documents d "
                "LEFT JOIN jobs j ON j.id = d.job_id "
                "LEFT JOIN companies c ON c.id = j.company_id WHERE d.id = ?",
                (document_id,)).fetchone()
            status = document_status(con, document_id)[0] if row else ""
        finally:
            con.close()
        path = review.safe_document_path(row["path"], out_dir()) if row else None
        if path is None:
            # The same single answer the download gives.
            return not_found("document")
        sheet, problem = None, None
        try:
            sheet = review.layout(path)
        except Exception:  # noqa: BLE001 - a corrupt file is reported, not raised
            problem = "This file could not be read as a Word document."
        return render("preview", "matches",
                      title=f"{row['kind'].replace('_', ' ').capitalize()} v{row['version']}",
                      doc=dict(row), sheet=sheet, problem=problem, status=status,
                      job_title=row["job_title"] or "job", company=row["company"] or "")

    @app.get("/add", response_class=HTMLResponse)
    def add_form(msg: str = "", bad: int = 0):
        from .intake import MIN_PASTED_CHARS
        return render("add", "add", title="Add a job", msg=msg, bad=bad,
                      min_chars=MIN_PASTED_CHARS)

    def back_to_add(msg: str) -> RedirectResponse:
        return RedirectResponse("/add?" + urlencode({"msg": msg, "bad": 1}),
                                status_code=303)

    def run_intake(action) -> RedirectResponse:
        """One path for both forms: add, commit, then the optional model check."""
        from . import intake
        from .config import Preferences
        prof = profile()
        if prof is None:
            return back_to_add("Your profile could not be loaded, so the job "
                               "cannot be scored.")
        con = connect()
        try:
            added = action(intake, con, Preferences.from_profile(prof))
            con.commit()
            added.enriched = intake.enrich(con, added.job_id)
            con.commit()
        except intake.IntakeError as exc:
            return back_to_add(str(exc))
        finally:
            con.close()
        parts = ["Added." if added.new else "Already in your tracker.",
                 f"Score {added.score:.2f}."]
        if added.enriched:
            parts.append(added.enriched[0].upper() + added.enriched[1:] + ".")
        parts.extend(added.warnings)
        if added.status:
            parts.append(f"You are tracking it: {added.status.replace('_', ' ')}.")
        return back_to_job(added.job_id, " ".join(parts), False)

    @app.post("/add/link")
    def add_link(url: str = Form(...), company: str = Form("")):
        return run_intake(lambda intake, con, prefs: intake.add_link(
            con, url, prefs, company=company.strip() or None))

    @app.post("/add/paste")
    def add_paste(company: str = Form(...), title: str = Form(...),
                  text: str = Form(...), location: str = Form(""),
                  link: str = Form("")):
        return run_intake(lambda intake, con, prefs: intake.add_pasted(
            con, company=company, title=title, text=text, prefs=prefs,
            url=link, location=location))

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
    def pipeline(msg: str = "", bad: int = 0):
        from datetime import date

        con = connect()
        try:
            rows = [dict(r) for r in con.execute(
                "SELECT a.id AS application_id, a.job_id, a.status, a.next_action, "
                "       a.next_action_due, a.last_activity_at, j.title, "
                "       c.name AS company "
                "  FROM applications a JOIN jobs j ON j.id = a.job_id "
                "  LEFT JOIN companies c ON c.id = j.company_id "
                " WHERE a.archived_at IS NULL").fetchall()]
        finally:
            con.close()

        today = date.today()
        for row in rows:
            row["days_out"] = None
            row["quiet"] = None
            if row["next_action_due"]:
                try:
                    row["days_out"] = (
                        date.fromisoformat(row["next_action_due"]) - today).days
                except ValueError:
                    pass
            if row["last_activity_at"] and not row["next_action_due"]:
                try:
                    quiet = (today - db.local_date(row["last_activity_at"])).days
                    row["quiet"] = quiet if quiet >= approvals.QUIET_DAYS else None
                except (TypeError, ValueError):
                    pass
        live = [r for r in rows if r["status"] not in approvals.CLOSED]
        closed = [r for r in rows if r["status"] in approvals.CLOSED]
        groups = [(stage, [r for r in live if r["status"] == stage])
                  for stage in approvals.STAGES if stage not in approvals.CLOSED]
        groups = [(stage, items) for stage, items in groups if items]
        return render("pipeline", "pipeline", groups=groups, closed=closed,
                      total=len(live), stages=list(approvals.STAGES),
                      overdue=sum(1 for r in live
                                  if r["days_out"] is not None and r["days_out"] < 0),
                      msg=msg, bad=bad)

    @app.post("/job/{job_id}/stage")
    def do_stage(job_id: int, stage: str = Form(...)):
        """The same approvals call `jsa status` makes. ADR 0003 decision 5."""
        con = connect()
        try:
            _, previous = approvals.set_stage(con, job_id, stage)
            con.commit()
        except approvals.ApprovalError as exc:
            return back_to_pipeline(str(exc), True)
        finally:
            con.close()
        return back_to_pipeline(
            f"Moved from {previous.replace('_', ' ')} to "
            f"{stage.replace('_', ' ')}.", False)

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
