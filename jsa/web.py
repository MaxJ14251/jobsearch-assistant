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

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, RedirectResponse)
from jinja2 import DictLoader, Environment

from . import approvals, db, review
from .prep import INTERVIEW_ROUNDS

HOST = "127.0.0.1"          # never 0.0.0.0
MAX_PICKED = 500            # cards one opened map bubble may ask for
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
    --blue:#2F6BC4;--violet:#7A4DC4;
    /* The ground under the live map (n23): context, so quieter than
       anything drawn on it. Water against land is at least 1.3:1 in both
       themes (n26: 1.38 light, 1.33 dark; it was 1.23 and 1.12). */
    --map-water:#C5D6DE;--map-land:#F7F6F1;--map-urban:#ECE6DA;
    --map-road:#D8B98C;--map-edge:#BCC5C0;
  }
  @media (prefers-color-scheme:dark){:root{
    --paper:#121615;--surface:#191F1D;--surface-2:#222927;--ink:#E8EBE7;
    --ink-2:#C0C7C2;--mute:#8B958F;--rule:#2C3532;--copper:#D98A4F;
    --sage:#6DAF96;--sage-soft:#1B2A25;--clay:#D2705B;--clay-soft:#2E1B17;
    --blue:#6FA3EE;--violet:#AE8BEB;
    --map-water:#0A1114;--map-land:#252C29;--map-urban:#303834;
    --map-road:#6C5D49;--map-edge:#404C47;}}
  *{box-sizing:border-box}
  [hidden]{display:none!important}
  body{margin:0;background:var(--paper);color:var(--ink);line-height:1.55;
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
  .wrap{max-width:1000px;margin:0 auto;padding:0 16px 64px}
  .wrap.wide{max-width:1480px}
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
  .city{font-size:9.5px;fill:var(--mute);text-anchor:middle;letter-spacing:.06em;
    text-transform:uppercase;paint-order:stroke;stroke:var(--map-land);stroke-width:3px;
    pointer-events:none}
  .bar{fill:var(--ink-2)}
  /* n21: the Matches page as map + list + analytics. Without the script it
     is the page above: the server's map, the server's list. */
  .st-new,[data-status=new]{--st:var(--mute)} .st-saved,[data-status=saved]{--st:var(--blue)}
  .st-applied,[data-status=applied]{--st:var(--sage)}
  .st-interview,[data-status=interview]{--st:var(--violet)}
  .split{display:grid;gap:14px;grid-template-columns:minmax(0,1fr)}
  .feed{min-width:0}
  @media (width>=1100px){
    .split.live{grid-template-columns:minmax(0,1fr) 440px;align-items:start}
    .split.live .mapcol{position:sticky;top:58px}
    .split.live .feed{max-height:calc(100vh - 70px);overflow-y:auto;
      padding-right:4px;scrollbar-width:thin}
  }
  .pill{font:600 10.5px ui-monospace,Menlo,monospace;letter-spacing:.05em;
    text-transform:uppercase;padding:2px 6px;border-radius:3px;white-space:nowrap;
    color:var(--st);background:color-mix(in srgb,var(--st) 15%,transparent)}
  .eta{white-space:nowrap}
  .card[data-key]{cursor:pointer;transition:border-color .12s}
  .card.hov{border-color:var(--ink-2)}
  .card.sel{border-color:var(--copper);box-shadow:inset 3px 0 0 var(--copper)}
  .feed-head{display:flex;flex-wrap:wrap;gap:6px;align-items:center;
    justify-content:space-between;position:sticky;top:0;z-index:2;
    background:var(--paper);padding:0 0 10px}
  .chips,.seg{display:flex;flex-wrap:wrap;gap:4px}
  .chip{display:inline-flex;gap:6px;align-items:center;font-size:12px;padding:4px 8px;
    border:1px solid var(--rule);border-radius:3px;background:var(--surface);color:var(--ink)}
  .chip i{width:9px;height:9px;border-radius:50%;background:var(--st);display:inline-block}
  .chip[aria-pressed="false"]{color:var(--mute);border-style:dashed;background:transparent}
  .chip[aria-pressed="false"] i{background:transparent;box-shadow:inset 0 0 0 1.5px var(--st)}
  .chip b{font:500 11px ui-monospace,Menlo,monospace;color:var(--mute)}
  .seg button{font-size:12px;padding:4px 8px;background:transparent;color:var(--mute);
    border:1px solid var(--rule)}
  .seg button[aria-pressed="true"]{background:var(--ink);color:var(--paper);border-color:var(--ink)}
  .livemap{position:relative;height:min(70vh,720px);min-height:360px;overflow:hidden;
    border-radius:3px;background:var(--surface);touch-action:none;user-select:none}
  figure.map .livemap svg{display:block;width:100%;height:100%;max-height:none}
  .bm-land{fill:var(--map-land);stroke:var(--map-edge);stroke-width:.8px}
  .bm-urban{fill:var(--map-urban)}
  .bm-water{fill:var(--map-water)}
  .bm-county{fill:none;stroke:var(--map-edge);stroke-width:.5px;stroke-dasharray:4 3}
  .bm-lake{fill:var(--map-water);stroke:var(--map-edge);stroke-width:.5px}
  .bm-road{fill:none;stroke:var(--map-road);stroke-width:1.1px;stroke-linejoin:round}
  .livemap{isolation:isolate}
  .livemap .bm-canvas{position:absolute;z-index:-1;pointer-events:none;transform-origin:0 0}
  .livemap .mapnorth{position:absolute;right:13px;top:158px;z-index:2;width:26px;height:26px;
    border-radius:50%;background:var(--surface);border:1px solid var(--rule);
    font:600 10px/26px ui-monospace,Menlo,monospace;text-align:center;color:var(--ink-2)}
  .livemap .mapnorth::before{content:"";position:absolute;left:50%;top:-6px;margin-left:-4px;
    border:4px solid transparent;border-top:0;border-bottom:7px solid var(--copper)}
  .livemap .bm-note{position:absolute;left:10px;bottom:34px;font-size:11.5px;
    color:var(--mute);background:var(--surface);padding:2px 6px;border-radius:3px}
  .livemap .mk{cursor:pointer}
  .picked{scroll-margin-top:64px;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:baseline;margin:0 0 10px;
    padding:8px 12px;border:1px solid var(--rule);border-radius:4px;background:var(--surface-2);
    font-size:13px;color:var(--ink-2)}
  .picked b{color:var(--ink)}
  .picked button{margin-left:auto;font:inherit;font-size:12.5px;padding:3px 10px;
    border:1px solid var(--rule);border-radius:3px;background:var(--surface);color:var(--ink);cursor:pointer}
  .livemap .mk:focus-visible circle{stroke:var(--copper);stroke-width:3}
  .rp{position:absolute;left:10px;top:10px;width:250px;z-index:3;display:grid;gap:10px;
    background:var(--surface);border:1px solid var(--rule);border-radius:4px;padding:12px;
    box-shadow:0 8px 24px -14px rgba(0,0,0,.45);font-size:12.5px;
    max-height:calc(100% - 20px);overflow-y:auto}
  .rp .num{font:500 34px/1 ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums}
  .rp .stbar{display:flex;height:5px;border-radius:3px;overflow:hidden;background:var(--surface-2)}
  .rp .stbar span{background:var(--st)}
  .rp .row{display:flex;justify-content:space-between;align-items:center;gap:8px}
  .rp .modes{display:grid;grid-template-columns:repeat(4,1fr);gap:2px;
    background:var(--surface-2);border-radius:3px;padding:2px}
  .rp .modes button{background:transparent;color:var(--mute);font-size:11.5px;padding:5px 0}
  .rp .modes button[aria-checked="true"]{background:var(--surface);color:var(--ink);
    box-shadow:0 1px 2px rgba(0,0,0,.15)}
  .rp .sw{display:flex;gap:6px}
  .rp .sw button{width:18px;height:18px;padding:0;border-radius:50%;background:var(--c);
    border:2px solid transparent}
  .rp .sw button[aria-checked="true"]{border-color:var(--ink)}
  .rp input[type=range]{width:120px;accent-color:var(--copper)}
  .rp details{margin:0}
  .rp details p{margin:6px 0 0;color:var(--mute)}
  .mapctl{position:absolute;right:10px;top:10px;z-index:3;display:grid;
    border:1px solid var(--rule);border-radius:3px;overflow:hidden;background:var(--surface)}
  .mapctl button{background:transparent;color:var(--ink-2);width:30px;height:30px;
    padding:0;border-radius:0;border-bottom:1px solid var(--rule);font-size:15px}
  .mapctl button:last-child{border-bottom:0}
  .maplegend{position:absolute;right:10px;bottom:10px;z-index:2;display:grid;gap:3px;
    background:var(--surface);border:1px solid var(--rule);border-radius:3px;
    padding:6px 8px;font-size:11px;pointer-events:none}
  .maplegend span{display:flex;gap:6px;align-items:center}
  .maplegend i{width:9px;height:9px;border-radius:50%;background:var(--st)}
  .mapfoot{position:absolute;left:10px;bottom:10px;z-index:2;pointer-events:none;
    font:11px ui-monospace,Menlo,monospace;color:var(--mute)}
  .mapfoot .sb{height:6px;border:1.5px solid var(--ink-2);border-top:0;margin-top:3px}
  .tip{position:absolute;z-index:4;pointer-events:none;max-width:260px;font-size:12px;
    background:var(--surface);border:1px solid var(--rule);border-radius:3px;
    padding:7px 9px;box-shadow:0 6px 18px -10px rgba(0,0,0,.5)}
  .tip b{display:block;font-weight:600}
  .tip span{display:block;color:var(--mute)}
  .drawer{margin-top:16px;background:var(--surface);border:1px solid var(--rule);border-radius:4px}
  .drawer>summary{padding:10px 14px;font-size:13px;color:var(--ink);list-style-position:inside}
  .drawer>summary b{letter-spacing:.06em;text-transform:uppercase;font-size:12px;margin-right:8px}
  .an{display:grid;gap:20px 28px;padding:4px 14px 16px;
    grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
  .an h3{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);
    margin:8px 0 8px;display:flex;justify-content:space-between;gap:8px}
  .an h3 small{letter-spacing:0;text-transform:none}
  .an ol{list-style:none;margin:0;padding:0;display:grid;gap:3px}
  .an li{display:grid;grid-template-columns:minmax(0,7.5rem) 1fr 2.2rem;gap:8px;
    align-items:center;font-size:12px}
  .an li>span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .an .track{height:7px;background:var(--surface-2);border-radius:2px;overflow:hidden}
  .an .track i{display:block;height:100%;background:var(--ink-2);border-radius:2px}
  .an li b{font:500 11.5px ui-monospace,Menlo,monospace;text-align:right}
  .an .foot{color:var(--mute);font-size:11.5px;margin:6px 0 0}
  .an li[role=button]{cursor:pointer;border-radius:2px;padding:1px 2px}
  .an li[role=button]:hover{background:var(--surface-2)}
  .an li[aria-pressed="true"]{font-weight:600}
  .an li[aria-pressed="true"] .track i{background:var(--copper)}
  .facet{font-size:12px;display:flex;gap:6px;align-items:center;width:100%}
  .facet button{font-size:12px;padding:3px 8px;background:var(--ink);color:var(--paper)}
  @media (max-width:760px){
    .rp{position:static;width:auto;max-height:none;border-width:0 0 1px;border-radius:0;
      box-shadow:none}
    .livemap{height:auto;display:flex;flex-direction:column}
    figure.map .livemap svg{height:58vh;min-height:320px}
    .maplegend{display:none}
    .mapctl{top:auto;bottom:44px}
    .livemap .mapnorth{top:10px}
  }
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
<div class="wrap{{ ' wide' if wide }}">{% block body %}{% endblock %}</div>
</body></html>"""

MATCHES = """{% extends "base" %}{% block body %}
<h1>Matches</h1>
{% if no_profile %}<p class="note bad" role="alert">No profile yet, so nothing can be scored for you.
  <a class="plain" href="/import">Import your resume</a>, or copy
  <code>profile/master_profile.example.yaml</code> to <code>profile/master_profile.yaml</code> and fill it in.</p>{% endif %}
<div id="summary">
<p class="sub">{{ total }} unreviewed · showing {{ rows|length - mine }}
  {%- if mine %} and {{ mine }} of your applications{% endif %}
  {%- if home %} · within {{ radius }} miles of {{ home }}:
    {{ near_count }} near you, {{ rows|length - near_count }} remote{% endif %}</p>
{% if home_problem %}<p class="note bad" role="alert">{{ home_problem }}</p>{% endif %}
{% if hidden %}<p class="sub">{{ hidden }} match(es) hidden by the radius.
  {%- if unplaced %} {{ unplaced }} of them name a place this could not find, so the distance is unknown rather than far.{% endif %}
  <a class="plain" href="{{ nationwide_url }}">Show them anyway</a></p>{% endif %}
</div>
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
  <input type="search" name="q" id="f-q" value="{{ q }}" size="26"
         placeholder="Search roles, e.g. support, data analyst"
         aria-label="Search roles">
  <select name="degree" id="f-degree" aria-label="Degree"><option value="">Degree: any</option>
    <option value="no" {{ 'selected' if degree=='no' }}>Not required</option>
    <option value="yes" {{ 'selected' if degree=='yes' }}>Required</option>
  </select>
  <select name="remote" id="f-remote" aria-label="Location type"><option value="">Any location type</option>
    <option value="remote" {{ 'selected' if remote=='remote' }}>Remote only</option>
  </select>
  <button type="submit">Filter</button>
</form>
{% if q_terms %}<p class="sub" id="q-hint">Matching titles: {% for t in q_terms %}<strong>{{ t }}</strong>{% if not loop.last %} <em>or</em> {% endif %}{% endfor %} · <a class="plain" href="{{ clear_q_url }}">Clear</a></p>{% endif %}
<div class="split{{ ' live' if live }}">
<div class="mapcol">
{% if map.drawn %}
<figure class="map" id="map-figure">
  <svg viewBox="0 0 {{ map.width }} {{ map.height }}" role="img" aria-label="{{ map_note }}">
    {% if map.ground %}<g aria-hidden="true"><rect class="bm-water" width="100%" height="100%"/><g transform="matrix({{ '%.6f'|format(map.per_mile) }} 0 0 {{ '%.6f'|format(-map.per_mile) }} {{ map.width / 2 }} {{ map.height / 2 }})">{% for name, d in map.ground.items() %}<path class="bm-{{ name }}" vector-effect="non-scaling-stroke" d="{{ d }}"/>{% endfor %}</g></g>{% endif %}
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
  <figcaption id="map-note">{{ map_note }}
    {%- if map.kind == 'local' and map.off_map %}
    <a class="plain" href="{{ nationwide_url }}">See the whole country</a>{% endif %}</figcaption>
</figure>
{% endif %}
{% if live %}
<div class="rp" id="radius-panel" hidden>
  <div>
    <div class="lbl">Inside the circle</div>
    <div class="row" style="justify-content:flex-start;align-items:baseline">
      <span class="num" id="rp-count" aria-live="polite">0</span><span id="rp-of" class="meta" style="margin:0"></span>
    </div>
  </div>
  <div class="stbar" id="rp-bar" aria-hidden="true"></div>
  <div class="row"><span>Commute bands</span>
    <label class="pair-in" for="rp-bands"><input type="checkbox" id="rp-bands"> show</label></div>
  <div class="modes" id="rp-modes" role="radiogroup" aria-label="How you would get there">
    <button type="button" role="radio" data-mode="drive">Drive</button>
    <button type="button" role="radio" data-mode="transit">Transit</button>
    <button type="button" role="radio" data-mode="bike">Bike</button>
    <button type="button" role="radio" data-mode="walk">Walk</button>
  </div>
  <div class="meta" id="rp-median" style="margin:0"></div>
  <details><summary>How the times are estimated</summary>
    <p>Straight-line distance, times a typical detour, at an average speed, plus a start-up time:
      drive ×1.25 at 32 mph +4 min; transit ×1.35 at 13 mph +12 min; bike ×1.3 at 11 mph +2 min;
      walk ×1.25 at 3 mph. Not routed. Traffic, timetables and hills are not in it.</p></details>
  <div class="row"><span>Circle</span>
    <span class="sw" id="rp-fence" role="radiogroup" aria-label="Circle colour">
      <button type="button" role="radio" data-fence="sage" style="--c:var(--sage)" aria-label="Sage"></button>
      <button type="button" role="radio" data-fence="copper" style="--c:var(--copper)" aria-label="Copper"></button>
      <button type="button" role="radio" data-fence="blue" style="--c:var(--blue)" aria-label="Blue"></button>
      <button type="button" role="radio" data-fence="ink" style="--c:var(--ink-2)" aria-label="Ink"></button>
    </span></div>
  <label class="row" for="rp-fill"><span>Fill</span>
    <input type="range" id="rp-fill" min="0" max="40" step="1"></label>
</div>
{% endif %}
</div>
<section class="feed" id="feed" aria-label="The list">
  <div class="feed-head" id="feed-head" hidden>
    <div class="chips" id="feed-chips" role="group" aria-label="Show by status"></div>
    <div class="seg" id="feed-sort" role="group" aria-label="Sort the list">
      <button type="button" data-sort="score" aria-pressed="true">Best match</button>
      <button type="button" data-sort="near" aria-pressed="false">Nearest</button>
      <button type="button" data-sort="pay" aria-pressed="false">Pay</button>
      <button type="button" data-sort="new" aria-pressed="false">Newest</button>
    </div>
    <div class="facet" id="feed-facet" hidden></div>
  </div>
  <div id="feed-list">
{% for r in rows %}
<div class="card" data-key="{{ r.key }}" data-status="{{ r.status }}">
  <div class="row1">
    <span class="score">{{ '%.2f'|format(r.match_score or 0) }}</span>
    {% if r.status != 'new' %}<span class="pill" title="Your application">{{ r.stage|replace('_',' ') }}</span>{% endif %}
    <span class="title"><a class="plain" href="/job/{{ r.job_id }}">{{ r.title }}</a></span>
    <span class="co">{{ r.company }}</span>
  </div>
  <div class="meta">{{ r.location or 'location not stated' }} · {{ r.remote }}
    {%- if r.miles is not none and r.remote != 'remote' %} · {{ r.miles }} mi away
      {%- if r.via_copy %} (its nearest location){% endif %}{% endif %}
    {%- if r.variant_count and r.variant_count > 1 %} · +{{ r.variant_count - 1 }} more location(s){% endif %}<span class="eta"></span></div>
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
  </div>
</section>
</div>
{% if live %}
<details class="drawer" id="analytics" open>
  <summary><b>Area analytics</b><span id="an-sum" class="meta"></span></summary>
  <div class="an" id="an-body"><p class="empty">The area analytics need JavaScript.</p></div>
</details>
<script type="application/json" id="map-data">{{ live|tojson }}</script>
{% endif %}
<script>
/* The Matches page, live (n21). Presentation only, as it was before n21.

   The server decides which postings are in the list, every time. While the
   radius handle moves, this redraws the circle and sorts DOTS into inside
   and outside by "d" -- the distance the server measured, the same float
   its filter compared, compared the same way. When the handle settles it
   asks the server for the page again and swaps the list in. The
   three-per-company cap, folding and the remote rule live in Python only.

   Chips, sorting and the analytics rearrange what the server sent; they
   never add to it. Commute times are estimates and say so. Nothing here
   reaches another host. Without this script the page is the form, the
   server's map and the server's list. */
(function () {
  'use strict';
  var form = document.getElementById('where');
  if (!form) { return; }
  var slider = form.querySelector('#f-radius');
  var shown = form.querySelector('#f-radius-out');
  var anywhere = form.querySelector('#f-anywhere');
  var dataEl = document.getElementById('map-data');

  if (!dataEl) {
    /* No home, or "anywhere": the server's map, with its circle resized
       while the handle moves and the form submitted when it is let go. */
    var ring = document.getElementById('ring');
    var perMile = ring ? parseFloat(ring.getAttribute('data-per-mile')) : 0;
    var paint = function () {
      shown.textContent = slider.value + ' miles';
      slider.disabled = anywhere.checked;
      if (ring && perMile) { ring.setAttribute('r', (slider.value * perMile).toFixed(1)); }
    };
    slider.addEventListener('input', paint);
    slider.addEventListener('change', function () { anywhere.checked = false; form.submit(); });
    anywhere.addEventListener('change', function () { paint(); form.submit(); });
    paint();
    return;
  }

  var live = JSON.parse(dataEl.textContent);
  var R = live.radius;
  var NS = 'http://www.w3.org/2000/svg';
  var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---- per-viewer preferences: conveniences, so a failure is harmless -- */
  function load(k, d) {
    try { var v = localStorage.getItem('jsa.map.' + k); return v === null ? d : JSON.parse(v); }
    catch (e) { return d; }
  }
  function save(k, v) { try { localStorage.setItem('jsa.map.' + k, JSON.stringify(v)); } catch (e) { /* private window */ } }

  var MODES = {
    drive: {noun: 'driving', detour: 1.25, mph: 32, start: 4, rings: [10, 20, 30, 45, 60, 90]},
    transit: {noun: 'by transit', detour: 1.35, mph: 13, start: 12, rings: [20, 30, 45, 60, 90, 120]},
    bike: {noun: 'cycling', detour: 1.3, mph: 11, start: 2, rings: [15, 30, 45, 60, 90, 120]},
    walk: {noun: 'walking', detour: 1.25, mph: 3, start: 0, rings: [15, 30, 45, 60, 90, 120]}
  };
  var FENCE = {sage: 'var(--sage)', copper: 'var(--copper)', blue: 'var(--blue)', ink: 'var(--ink-2)'};
  var ST_COLOR = {'new': 'var(--mute)', saved: 'var(--blue)', applied: 'var(--sage)', interview: 'var(--violet)'};
  var STATUSES = [['interview', 'Interview'], ['applied', 'Applied'], ['saved', 'Saved'], ['new', 'New']];

  var state = {
    mode: MODES[load('mode', 'drive')] ? load('mode', 'drive') : 'drive',
    bands: load('bands', true) === true,
    fence: FENCE[load('fence', 'sage')] ? load('fence', 'sage') : 'sage',
    fill: Math.max(0, Math.min(40, +load('fill', 10) || 0)),
    on: {interview: true, applied: true, saved: true, 'new': true},
    sort: 'score', sel: null, hov: null, facet: null
  };

  function minutes(d) { var m = MODES[state.mode]; return m.start + d * m.detour / m.mph * 60; }
  function reach(t) { var m = MODES[state.mode]; return Math.max(0, t - m.start) * m.mph / 60 / m.detour; }
  function fmtMin(x) {
    if (x < 60) { return Math.max(1, Math.round(x)) + ' min'; }
    var h = Math.floor(x / 60), r = Math.round(x % 60);
    return r ? h + ' h ' + r + ' min' : h + ' h';
  }
  function fmtMiles(r) { return (r % 1 ? r.toFixed(1) : String(r)) + ' mi'; }
  function money(n) { return '$' + Math.round(n / 1000) + 'k'; }
  function median(xs) {
    if (!xs.length) { return null; }
    var s = xs.slice().sort(function (a, b) { return a - b; }), m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }
  function node(tag, attrs, parent, svg) {
    var n = svg ? document.createElementNS(NS, tag) : document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (attrs[k] !== null && attrs[k] !== undefined) { n.setAttribute(k, attrs[k]); }
    });
    if (parent) { parent.appendChild(n); }
    return n;
  }
  function sv(tag, attrs, parent) { return node(tag, attrs, parent, true); }
  function text(parent, tag, words, cls) {
    var n = node(tag, cls ? {'class': cls} : {}, parent); n.textContent = words; return n;
  }
  function clear(n) { while (n.firstChild) { n.removeChild(n.firstChild); } }

  /* ---- the map ---------------------------------------------------------- */
  var fig = document.getElementById('map-figure');
  var staticSvg = fig.querySelector('svg');
  if (staticSvg) { staticSvg.style.display = 'none'; }
  var box = node('div', {'class': 'livemap'});
  fig.insertBefore(box, fig.firstChild);
  var panel = document.getElementById('radius-panel');
  box.appendChild(panel);
  panel.hidden = false;
  var svg = sv('svg', {role: 'img', 'aria-label': 'Map of postings around ' + live.home}, box);
  var clipC = sv('circle', {cx: 0, cy: 0}, sv('clipPath', {id: 'fence-clip'}, sv('defs', {}, svg)));
  var ground = sv('rect', {width: '100%', height: '100%'}, svg);
  ground.style.fill = 'var(--surface)';
  var world = sv('g', {}, svg);
  var gridG = sv('g', {}, world);
  var fenceFill = sv('circle', {cx: 0, cy: 0}, world);
  var bandsG = sv('g', {'clip-path': 'url(#fence-clip)'}, world);
  var fenceLine = sv('circle', {cx: 0, cy: 0, 'vector-effect': 'non-scaling-stroke'}, world);
  var marksG = sv('g', {}, svg);
  var labelsG = sv('g', {}, svg);
  var overG = sv('g', {}, svg);

  var ctl = node('div', {'class': 'mapctl'}, box);
  [['+', 'Zoom in', function () { zoomBy(1.6); }],
   ['−', 'Zoom out', function () { zoomBy(1 / 1.6); }],
   ['⤢', 'Fit the circle', function () { fly(fitView(R)); }],
   ['⌖', 'Back to home', function () { fly(centerOn(0, 0, view.s)); }]
  ].forEach(function (b) {
    var btn = node('button', {type: 'button', title: b[1], 'aria-label': b[1]}, ctl);
    btn.textContent = b[0];
    btn.addEventListener('click', b[2]);
  });
  var legend = node('div', {'class': 'maplegend', 'aria-hidden': 'true'}, box);
  STATUSES.forEach(function (s) {
    var row = node('span', {'class': 'st-' + s[0]}, legend);
    node('i', {}, row); row.appendChild(document.createTextNode(s[1]));
  });
  var foot = node('div', {'class': 'mapfoot'}, box);
  var northEl = node('div', {'class': 'mapnorth', hidden: '', 'aria-hidden': 'true',
                              title: 'North. Zoomed out, the map turns to match the usual map of the US.'}, box);
  northEl.textContent = 'N';
  var bmNote = node('div', {'class': 'bm-note', hidden: ''}, box);
  bmNote.textContent = 'Map detail ends at this zoom';

  /* ---- the ground ------------------------------------------------------- */
  /* Land, towns and roads from this machine's own data (n23). The server
     projects every vertex with the function that places the pins, and this
     paints them with the same view numbers (S, tx, ty) the pins are drawn
     with, so the two cannot drift. It is painted into a canvas BEHIND the
     svg, once per settled view: while the view moves, the canvas is only
     moved and scaled -- the same affine change the pins get -- because
     repainting megabytes of outline on every frame is what made a pan stall.
     Context only: no tab stop, no accessible name. */
  var BM = live.basemap, bm = {chunks: {}, pending: {}, shown: null, at: null};
  var BM_TIERS = ['coarse', 'medium', 'fine'];
  var BM_LAYERS = ['land', 'urban', 'county', 'lake', 'road'];
  var BM_MARGIN = 0.5;          /* painted beyond each edge, for panning into */
  var canvas = null, paint = null, painter = null, chunkSeq = 0, paintSeq = 0, asked = '';
  /* The one painting routine. The page runs it where it must, and the
     worker runs it from this very source, so the two cannot differ. */
  function paintGround(g, p, m) {
    var c = m.colors, px = m.px;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, m.w, m.h);
    g.fillStyle = c.water; g.fillRect(0, 0, m.w, m.h);
    g.setTransform(m.t[0], m.t[1], m.t[2], m.t[3], m.t[4], m.t[5]);
    g.lineJoin = 'round';
    if (p.land) { g.fillStyle = c.land; g.fill(p.land); }
    if (p.urban) { g.fillStyle = c.urban; g.fill(p.urban); }
    if (p.county) {
      g.strokeStyle = c.edge; g.lineWidth = 0.5 * px;
      g.setLineDash([4 * px, 3 * px]); g.stroke(p.county); g.setLineDash([]);
    }
    if (p.land) { g.strokeStyle = c.edge; g.lineWidth = 0.8 * px; g.stroke(p.land); }
    /* Lakes are filled, not outlined: the Census splits a lake at every
       county line, and an outline would draw each seam across the water. */
    if (p.lake) { g.fillStyle = c.water; g.fill(p.lake); }
    if (p.road) { g.strokeStyle = c.road; g.lineWidth = 1.1 * px; g.stroke(p.road); }
  }
  /* Painting a whole tier takes 25 to 90 ms (n26, measured). On the page's
     own thread that is a hitch every time the map settles, so a worker
     paints on an OffscreenCanvas and hands the finished picture back whole.
     Until it arrives the old picture stays up, moved like everything else.
     Only the newest request is painted. A browser that cannot do this
     paints here instead, as before. */
  var PAINTER = [
    'var chunks = {};',
    'onmessage = function (e) {',
    '  var m = e.data, p, k;',
    '  if (m.type === "chunk") {',
    '    p = {}; for (k in m.layers) { p[k] = new Path2D(m.layers[k]); }',
    '    chunks[m.id] = p; return;',
    '  }',
    '  if (m.type === "drop") { delete chunks[m.id]; return; }',
    '  p = chunks[m.id];',
    '  if (!p) { postMessage({seq: m.seq, bitmap: null}); return; }',
    '  var t0 = performance.now(), c = new OffscreenCanvas(m.w, m.h), g = c.getContext("2d");',
    '  paintGround(g, p, m);',
    '  if (m.debug) { g.getImageData(0, 0, 1, 1); }',
    '  var bitmap = c.transferToImageBitmap();',
    '  postMessage({seq: m.seq, bitmap: bitmap, ms: performance.now() - t0}, [bitmap]);',
    '};'
  ].join('\\n');
  function localPaths(layers) {
    var paths = {};
    BM_LAYERS.forEach(function (name) { if (layers[name]) { paths[name] = new Path2D(layers[name]); } });
    return paths;
  }
  if (BM) {
    canvas = node('canvas', {'class': 'bm-canvas', 'aria-hidden': 'true'}, null);
    box.insertBefore(canvas, box.firstChild);
    try {
      if (window.Worker && window.OffscreenCanvas && window.Blob && window.URL) {
        painter = new Worker(URL.createObjectURL(new Blob(
          [paintGround.toString() + '\\n' + PAINTER], {type: 'text/javascript'})));
        painter.onmessage = bmPainted;
        painter.onerror = function () { bmLocal(); };
        paint = canvas.getContext('bitmaprenderer');
        if (!paint) { throw new Error('no bitmaprenderer'); }
      }
    } catch (e) {
      if (painter) { painter.terminate(); }
      painter = null;
    }
    if (!painter) { paint = canvas.getContext('2d'); }
  }
  /* The worker failed (an older browser without Path2D in workers): paint
     here from now on, on a fresh canvas, from the chunks already fetched. */
  function bmLocal() {
    if (!painter) { return; }
    painter.terminate(); painter = null;
    var fresh = node('canvas', {'class': 'bm-canvas', 'aria-hidden': 'true'}, null);
    canvas.replaceWith(fresh); canvas = fresh; paint = canvas.getContext('2d');
    BM_TIERS.forEach(function (t) {
      var ch = bm.chunks[t];
      if (ch && !ch.paths) { ch.paths = localPaths(ch.src); }
    });
    bm.at = null; bm.job = null; asked = ''; redraw();
  }
  /* The coarsest tier still honest at this scale. */
  function bmWant(S) {
    for (var i = 0; i < BM_TIERS.length; i++) { if (S <= BM.ppm[BM_TIERS[i]]) { return BM_TIERS[i]; } }
    return 'fine';
  }
  /* A chunk is used only if it holds the whole view. */
  function bmCovers(ch) {
    if (ch.reach === null) { return true; }
    var z = size(), half = Math.hypot(z.w, z.h) / 2 / view.s;
    return Math.hypot(view.cx - ch.at[0], view.cy - ch.at[1]) + half <= ch.reach;
  }
  function bmBuild(d) {
    var ch = {id: ++chunkSeq, tier: d.tier, reach: d.reach, at: d.at,
              cities: d.cities || [], src: d.layers, paths: null};
    if (painter) { painter.postMessage({type: 'chunk', id: ch.id, layers: d.layers}); }
    else { ch.paths = localPaths(d.layers); }
    return ch;
  }
  function bmEnsure(tier) {
    var ch = bm.chunks[tier];
    if ((ch && bmCovers(ch)) || bm.pending[tier]) { return; }
    var at = BM.reach[tier] === null ? [0, 0] : [view.cx, view.cy];
    bm.pending[tier] = true;
    var q = new URLSearchParams({home: BM.home, x: at[0].toFixed(1), y: at[1].toFixed(1), v: BM.v});
    fetch('/basemap/' + tier + '?' + q.toString(), {credentials: 'same-origin'})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        bm.pending[tier] = false;
        if (!d) { return; }
        var old = bm.chunks[tier];
        bm.chunks[tier] = bmBuild(d);
        if (old && painter) { painter.postMessage({type: 'drop', id: old.id}); }
        redraw();
      })
      .catch(function () { bm.pending[tier] = false; });
  }
  /* Work that waits for the view to stop: a fly or a drag passes through
     scales and places nobody stops at. */
  var bmTimer = 0, flying = false;
  function bmSoon() {
    if (bmTimer) { return; }
    bmTimer = setTimeout(function () {
      bmTimer = 0;
      if (flying || drag) { bmSoon(); return; }
      bmEnsure(bmWant(view.s));
      bm.shown = bmPick(view.s);
      bmPaint();
      bmDraw(view.s);
    }, 120);
  }
  /* The tier this scale wants when it is here; while it loads, a FINER
     one that already covers the view (more detail than needed is still
     true). Never a coarser one: at this scale its simplified coast would
     be visibly in the wrong place, so the ground stays flat instead. */
  function bmPick(S) {
    var want = bmWant(S), have = bm.chunks[want];
    if (!(have && bmCovers(have))) { bmSoon(); }
    for (var i = BM_TIERS.indexOf(want); i < BM_TIERS.length; i++) {
      var ch = bm.chunks[BM_TIERS[i]];
      if (ch && bmCovers(ch)) { return ch; }
    }
    return null;
  }
  function bmColor(name) {
    return getComputedStyle(document.documentElement).getPropertyValue('--map-' + name).trim();
  }
  /* Paint the shown chunk at the current view. The transform is the one
     `world` gets in draw(), shifted by the margin: same S, same tx, ty. */
  function bmPaint() {
    if (!canvas) { return; }
    var z = size(), dpr = window.devicePixelRatio || 1;
    var ox = z.w * BM_MARGIN, oy = z.h * BM_MARGIN, W = z.w + 2 * ox, H = z.h + 2 * oy;
    var ch = bm.shown, M = frame(view, z.w, z.h);
    var key = [ch ? ch.id : 0, z.w, z.h, view.cx, view.cy, view.s].join();
    if (key === asked) { return; }            /* already asked for exactly this */
    if (bm.job) { bm.next = true; return; }  /* one in flight: ask again when it lands */
    asked = key;
    /* An svg has no offsetLeft; place by the boxes the browser laid out. */
    var sr = svg.getBoundingClientRect(), br = box.getBoundingClientRect();
    var job = {at: {cx: view.cx, cy: view.cy, s: view.s, w: z.w, h: z.h, ch: ch, m: M},
               W: W, H: H, left: sr.left - br.left - box.clientLeft - ox,
               top: sr.top - br.top - box.clientTop - oy};
    if (!ch) { bmShow(job, null); return; }
    var msg = {w: Math.round(W * dpr), h: Math.round(H * dpr), px: 1 / view.s,
               t: [dpr * M[0], dpr * M[1], dpr * M[2], dpr * M[3],
                   dpr * (M[4] + ox), dpr * (M[5] + oy)],
               colors: {water: bmColor('water'), land: bmColor('land'), urban: bmColor('urban'),
                        road: bmColor('road'), edge: bmColor('edge')}};
    if (painter) {
      job.seq = msg.seq = ++paintSeq; msg.type = 'paint'; msg.id = ch.id; msg.debug = FRAMES;
      bm.job = job;
      painter.postMessage(msg);
      return;
    }
    var t0 = FRAMES ? performance.now() : 0;
    bmPlace(job, msg.w, msg.h);
    paintGround(paint, ch.paths, msg);
    if (FRAMES) {
      paint.getImageData(0, 0, 1, 1);         /* wait for the pixels */
      console.info('[paint] ' + JSON.stringify({where: 'page', tier: ch.tier,
        ms: +(performance.now() - t0).toFixed(1), px_per_mile: +view.s.toFixed(3)}));
    }
    bm.at = job.at;
  }
  function bmPlace(job, w, h) {
    canvas.width = w; canvas.height = h;
    canvas.style.width = job.W + 'px'; canvas.style.height = job.H + 'px';
    canvas.style.left = job.left + 'px'; canvas.style.top = job.top + 'px';
    canvas.style.transform = 'none';
  }
  /* Nothing to show: clear the picture. */
  function bmShow(job) {
    bmPlace(job, 1, 1);
    if (painter) { paint.transferFromImageBitmap(null); }
    else { paint.clearRect(0, 0, 1, 1); }
    bm.at = job.at;
  }
  /* The worker's picture. Put it up, and move it to wherever the view has
     got to since it was asked for. */
  function bmPainted(e) {
    var m = e.data, job = bm.job;
    if (!job || m.seq !== job.seq) { if (m.bitmap) { m.bitmap.close(); } return; }
    bm.job = null;
    if (m.bitmap) {
      var t0 = FRAMES ? performance.now() : 0;
      bmPlace(job, m.bitmap.width, m.bitmap.height);
      paint.transferFromImageBitmap(m.bitmap);
      bm.at = job.at;
      if (FRAMES) {
        console.info('[paint] ' + JSON.stringify({where: 'worker', tier: job.at.ch.tier,
          worker_ms: +m.ms.toFixed(1), page_ms: +(performance.now() - t0).toFixed(2),
          px_per_mile: +job.at.s.toFixed(3)}));
      }
    }
    if (bm.next) { bm.next = false; asked = ''; }
    redraw();
  }
  function bmDraw(S) {
    if (!BM) { return; }
    var ch = bmPick(S), z = size(), a = bm.at;
    bm.shown = ch;
    if (!a || a.ch !== ch || a.w !== z.w || a.h !== z.h) {
      if (flying || drag) { bmSoon(); } else { bmPaint(); }
    } else if (a.s !== view.s || a.cx !== view.cx || a.cy !== view.cy) {
      bmSoon();
    }
    a = bm.at;
    if (a && a.ch) {
      /* Map what was painted at view `a` onto the view now: the same
         affine change the pins just went through, turn included. */
      var ox = a.w * BM_MARGIN, oy = a.h * BM_MARGIN;
      var t = compose([1, 0, 0, 1, ox, oy], compose(frame(view, a.w, a.h),
                      compose(invert(a.m), [1, 0, 0, 1, -ox, -oy])));
      canvas.style.transform = 'matrix(' + t.join(',') + ')';
    }
    ground.style.fill = (a && a.ch) ? 'transparent' : 'var(--surface)';
    /* Past the finest tier's measured accuracy the ground fades out rather
       than pretend to a precision it does not have (ADR 0019). */
    var limit = BM.ppm.fine, fade = S <= limit ? 1 : Math.max(0, 1 - (S - limit) / limit);
    canvas.style.opacity = fade;
    bmNote.hidden = !(a && a.ch && S > limit);
  }
  if (BM && window.matchMedia) {
    var scheme = window.matchMedia('(prefers-color-scheme: dark)');
    if (scheme.addEventListener) {
      scheme.addEventListener('change', function () { bm.at = null; asked = ''; redraw(); });
    }
  }
  var tip = node('div', {'class': 'tip', hidden: ''}, box);

  var view = {cx: 0, cy: 0, s: 1};
  function size() { return {w: svg.clientWidth || box.clientWidth || 600, h: svg.clientHeight || 420}; }
  function panelWidth() { return window.innerWidth > 760 ? panel.offsetWidth + 20 : 0; }

  /* The turn. North is straight up at home, where pins and streets are
     read. Zoomed out towards the whole country, the map turns to the
     orientation of the usual map of the United States (Albers, centred on
     96 W), because north-up at home tips the rest of the country over by as
     much as its meridians converge. Turning about home changes no distance:
     the circle, the rings and every d stay exactly what they were. */
  var TURN = live.turn || 0;
  function turnAt(s) {
    var lo = Math.log(0.6), hi = Math.log(3);
    var t = Math.max(0, Math.min(1, (Math.log(s) - lo) / (hi - lo)));
    return TURN * (1 - t * t * (3 - 2 * t));
  }
  /* A view as [a, b, c, d, e, f], the six numbers of an svg or css
     matrix(): miles (x east, y north) to pixels, y flipped, turned. The
     pins, the rings and the ground all go through this one function. */
  function frame(v, w, h) {
    var turn = turnAt(v.s), a = Math.cos(turn) * v.s, b = -Math.sin(turn) * v.s;
    var c = b, d = -a;
    return [a, b, c, d, w / 2 - (a * v.cx + c * v.cy), h / 2 - (b * v.cx + d * v.cy)];
  }
  function apply(m, x, y) { return [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]]; }
  function invert(m) {
    var det = m[0] * m[3] - m[1] * m[2];
    var a = m[3] / det, b = -m[1] / det, c = -m[2] / det, d = m[0] / det;
    return [a, b, c, d, -(a * m[4] + c * m[5]), -(b * m[4] + d * m[5])];
  }
  function compose(m, n) {          /* m after n */
    return [m[0] * n[0] + m[2] * n[1], m[1] * n[0] + m[3] * n[1],
            m[0] * n[2] + m[2] * n[3], m[1] * n[2] + m[3] * n[3],
            m[0] * n[4] + m[2] * n[5] + m[4], m[1] * n[4] + m[3] * n[5] + m[5]];
  }
  /* Miles moved by a pixel offset at scale s (its turn included). */
  function unshift(s, dx, dy) { return apply(invert(frame({cx: 0, cy: 0, s: s}, 0, 0)), dx, dy); }
  function toWorld(px, py) { var z = size(); return apply(invert(frame(view, z.w, z.h)), px, py); }
  /* The view at scale s that puts the point (x, y) at pixel (px, py). */
  function anchor(s, x, y, px, py) {
    var z = size(), u = unshift(s, px - z.w / 2, py - z.h / 2);
    return {cx: x - u[0], cy: y - u[1], s: s};
  }
  /* The view that puts (x, y) in the middle of the part of the map the
     panel does not cover. */
  function centerOn(x, y, s) {
    var z = size(), left = panelWidth();
    return anchor(s, x, y, left + (z.w - left) / 2, z.h / 2);
  }
  function fitView(r) {
    var z = size(), left = panelWidth(), room = Math.max(120, Math.min(z.w - left, z.h));
    return centerOn(0, 0, room / 2 / (Math.max(r, 2) * 1.12));
  }
  function clampS(s) { return Math.max(0.03, Math.min(250, s)); }

  /* ?debug=frames (n26): the worst frame of each pan or zoom, written to
     the console once the map settles. Off unless the address asks for it;
     it writes nothing anywhere else and sends nothing. */
  var FRAMES = new URLSearchParams(location.search).get('debug') === 'frames';
  var frameLog = null, lastWheel = 0, motion = {n: 0, worst: 0, total: 0, timer: 0};
  function moving() { return flying || drag || performance.now() - lastWheel < 150; }
  function watchFrames() {
    if (!FRAMES || frameLog) { return; }
    frameLog = {last: performance.now(), worst: 0, n: 0, still: 0};
    requestAnimationFrame(function tick(t) {
      var gap = t - frameLog.last;
      frameLog.last = t;
      if (moving()) { frameLog.worst = Math.max(frameLog.worst, gap); frameLog.n++; frameLog.still = 0; }
      else if (++frameLog.still > 10) {
        console.info('[frames] ' + JSON.stringify({
          tier: bm.shown ? bm.shown.tier : 'none', frames: frameLog.n,
          worst_ms: Math.round(frameLog.worst), px_per_mile: +view.s.toFixed(3),
          page: document.visibilityState}));
        frameLog = null;
        return;
      }
      requestAnimationFrame(tick);
    });
  }

  var raf = 0;
  function redraw() { if (!raf) { raf = requestAnimationFrame(function () { raf = 0; draw(); }); } }
  var anim = 0;
  function fly(to) {
    cancelAnimationFrame(anim);
    to.s = clampS(to.s);
    if (reduced) { view = to; redraw(); return; }
    var from = {cx: view.cx, cy: view.cy, s: view.s}, t0 = performance.now();
    flying = true;
    watchFrames();
    (function step(t) {
      var k = Math.min(1, (t - t0) / 340), e = 1 - Math.pow(1 - k, 3);
      view = {cx: from.cx + (to.cx - from.cx) * e, cy: from.cy + (to.cy - from.cy) * e,
              s: from.s * Math.pow(to.s / from.s, e)};
      flying = k < 1;
      draw();
      if (k < 1) { anim = requestAnimationFrame(step); }
    })(t0);
  }
  function zoomBy(k) { fly({cx: view.cx, cy: view.cy, s: view.s * k}); }

  function feedKeys() {
    var keys = {};
    cards().forEach(function (c) { if (!c.hidden) { keys[c.getAttribute('data-key')] = c; } });
    return keys;
  }

  function cluster(pts, px) {
    var out = [];
    pts.forEach(function (p) {
      var best = null, bd = px;
      out.forEach(function (c) {
        if (c.inside !== p.inside) { return; }
        var d = Math.hypot(c.x - p.x, c.y - p.y);
        if (d < bd) { best = c; bd = d; }
      });
      if (best) {
        var n = best.m.length;
        best.x = (best.x * n + p.x) / (n + 1); best.y = (best.y * n + p.y) / (n + 1);
        best.m.push(p);
      } else { out.push({x: p.x, y: p.y, inside: p.inside, m: [p]}); }
    });
    return out;
  }

  function draw() {
    var t0 = FRAMES ? performance.now() : 0;
    drawNow();
    if (FRAMES && moving()) {
      var spent = performance.now() - t0;
      motion.n++; motion.worst = Math.max(motion.worst, spent); motion.total += spent;
      clearTimeout(motion.timer);
      motion.timer = setTimeout(function () {
        console.info('[draw] ' + JSON.stringify({tier: bm.shown ? bm.shown.tier : 'none',
          draws: motion.n, worst_ms: +motion.worst.toFixed(1),
          mean_ms: +(motion.total / motion.n).toFixed(2), px_per_mile: +view.s.toFixed(3)}));
        motion = {n: 0, worst: 0, total: 0, timer: 0};
      }, 400);
    }
  }
  function drawNow() {
    var z = size(), w = z.w, h = z.h, S = view.s;
    svg.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
    var M = frame(view, w, h), H = apply(M, 0, 0);
    world.setAttribute('transform', 'matrix(' + M.join(' ') + ')');
    var color = FENCE[state.fence];
    bmDraw(S);
    var turn = turnAt(S);
    northEl.hidden = Math.abs(turn) < 0.01;
    northEl.style.transform = 'rotate(' + (-turn * 180 / Math.PI).toFixed(2) + 'deg)';

    /* Range rings about home: in this projection a circle IS a distance. */
    clear(gridG);
    var far = Math.hypot(Math.max(Math.abs(H[0]), Math.abs(w - H[0])), Math.max(Math.abs(H[1]), Math.abs(h - H[1]))) / S;
    var step = [1, 2, 5, 10, 25, 50, 100, 250, 500].find(function (m) { return m * S >= 70; }) || 1000;
    for (var k = 1; k * step <= far && k <= 60; k++) {
      var g = sv('circle', {cx: 0, cy: 0, r: k * step, 'vector-effect': 'non-scaling-stroke'}, gridG);
      g.style.fill = 'none'; g.style.stroke = 'var(--rule)'; g.style.strokeWidth = '1px';
    }

    clipC.setAttribute('r', R);
    fenceFill.setAttribute('r', R);
    fenceFill.style.fill = color;
    fenceFill.style.fillOpacity = (state.bands ? state.fill * 0.5 : state.fill) / 100;
    fenceLine.setAttribute('r', R);
    fenceLine.style.fill = 'none'; fenceLine.style.stroke = color; fenceLine.style.strokeWidth = '1.75px';

    clear(bandsG);
    var rings = [];
    if (state.bands) {
      MODES[state.mode].rings.forEach(function (t) { var r = reach(t); if (r > 0) { rings.push({t: t, r: r}); } });
    }
    rings.slice().reverse().forEach(function (b) {
      var c = sv('circle', {cx: 0, cy: 0, r: b.r}, bandsG);
      c.style.fill = color; c.style.fillOpacity = 0.09;
    });
    rings.forEach(function (b) {
      var c = sv('circle', {cx: 0, cy: 0, r: b.r, 'vector-effect': 'non-scaling-stroke'}, bandsG);
      c.style.fill = 'none'; c.style.stroke = color; c.style.strokeOpacity = 0.6;
      c.style.strokeWidth = '1px'; c.style.strokeDasharray = '3 4';
    });

    /* Markers. Inside or outside is decided by d, never by x and y. */
    var inFeed = feedKeys();
    var pts = [];
    live.points.forEach(function (p) {
      if (!state.on[p.status]) { return; }
      var q = apply(M, p.x, p.y), x = q[0], y = q[1];
      if (x < -30 || y < -30 || x > w + 30 || y > h + 30) { return; }
      pts.push({p: p, x: x, y: y, inside: p.d <= R});
    });
    clear(marksG); clear(labelsG); clear(overG);
    var clusters = cluster(pts, 22);
    clusters.filter(function (c) { return !c.inside; }).forEach(function (c) { drawCluster(c, inFeed); });
    clusters.filter(function (c) { return c.inside; }).forEach(function (c) { drawCluster(c, inFeed); });

    /* Name the places with the most postings, where the names fit. */
    var taken = [];
    clusters.slice().sort(function (a, b) { return b.m.length - a.m.length; }).slice(0, 14).forEach(function (c) {
      if (c.m.length < 2 && S < 6) { return; }
      var count = {}, best = null;
      c.m.forEach(function (q) { count[q.p.place] = (count[q.p.place] || 0) + 1; });
      Object.keys(count).forEach(function (n) { if (!best || count[n] > count[best]) { best = n; } });
      var r = c.m.length > 1 ? 12 + Math.min(9, Math.log2(c.m.length) * 2.4) : 8;
      var bx = [c.x + r + 3, c.y - 8, c.x + r + 5 + best.length * 6.3, c.y + 6];
      if (taken.some(function (t) { return bx[0] < t[2] && bx[2] > t[0] && bx[1] < t[3] && bx[3] > t[1]; })) { return; }
      taken.push(bx);
      var tl = sv('text', {x: c.x + r + 4, y: c.y + 4, 'class': 'town left'}, labelsG);
      tl.textContent = best;
    });

    /* City names from the ground, after the jobs' own names: a job's label
       always wins the space, and no name sits on a marker or on home. Each
       name is on the same Census town point a pin for that town uses.
       Biggest places first; none once the ground has faded out. */
    var shownGround = bm.at && bm.at.ch;
    if (shownGround && S <= 2 * BM.ppm.fine) {
      var blocked = taken.concat(clusters.map(function (c) {
        var r = c.m.length > 1 ? 14 : 9;
        return [c.x - r, c.y - r, c.x + r, c.y + r];
      }), [[H[0] - 12, H[1] - 12, H[0] + 12, H[1] + 12]]);
      var named = 0;
      shownGround.cities.forEach(function (ct) {
        if (named >= 40) { return; }
        var q = apply(M, ct[1], ct[2]), half = ct[0].length * 3.4 + 3;
        if (q[0] < half || q[1] < 10 || q[0] > w - half || q[1] > h - 10) { return; }
        var bx = [q[0] - half, q[1] - 8, q[0] + half, q[1] + 5];
        if (blocked.some(function (t) { return bx[0] < t[2] && bx[2] > t[0] && bx[1] < t[3] && bx[3] > t[1]; })) { return; }
        blocked.push(bx); named++;
        sv('text', {x: q[0].toFixed(1), y: (q[1] + 3).toFixed(1), 'class': 'city'}, labelsG).textContent = ct[0];
      });
    }

    /* The card under the pointer, and the chosen one: every copy of it. */
    [[state.sel, 2, null], [state.hov, 1.25, '2 3']].forEach(function (s) {
      if (!s[0]) { return; }
      pts.forEach(function (q) {
        if (q.p.key !== s[0]) { return; }
        var c = sv('circle', {cx: q.x, cy: q.y, r: 12}, overG);
        c.style.fill = 'none'; c.style.stroke = 'var(--ink)'; c.style.strokeWidth = s[1] + 'px';
        if (s[2]) { c.style.strokeDasharray = s[2]; }
        c.style.pointerEvents = 'none';
      });
    });

    var lastY = Infinity;
    rings.forEach(function (b) {
      if (b.r >= R * 0.97 || b.r * S < 16) { return; }
      var x = H[0], y = H[1] - b.r * S;
      if (y < 0 || y > h || lastY - y < 20) { return; }
      lastY = y;
      var label = b.t >= 60 ? fmtMin(b.t) : b.t + ' min';
      var g = sv('g', {transform: 'translate(' + x + ' ' + y + ')'}, overG);
      g.style.pointerEvents = 'none';
      var bg = sv('rect', {x: -label.length * 3.3 - 5, y: -8, width: label.length * 6.6 + 10, height: 16, rx: 3}, g);
      bg.style.fill = 'var(--surface)'; bg.style.opacity = 0.92;
      var t = sv('text', {'text-anchor': 'middle', dy: '0.35em'}, g);
      t.style.fill = color; t.style.font = '10px ui-monospace,Menlo,monospace';
      t.textContent = label;
    });

    var ex = H[0] + R * S, ey = H[1];
    if (ex > 0 && ex < w) {
      var rl = fmtMiles(R), rg = sv('g', {transform: 'translate(' + ex + ' ' + ey + ')'}, overG);
      rg.style.pointerEvents = 'none';
      sv('rect', {x: -rl.length * 3.6 - 6, y: -10, width: rl.length * 7.2 + 12, height: 20, rx: 3}, rg).style.fill = color;
      var rt = sv('text', {'text-anchor': 'middle', dy: '0.35em'}, rg);
      rt.style.fill = 'var(--surface)'; rt.style.font = '600 11px ui-monospace,Menlo,monospace';
      rt.textContent = rl;
    }

    var home = sv('g', {transform: 'translate(' + H[0] + ' ' + H[1] + ')'}, overG);
    home.style.pointerEvents = 'none';
    var hc = sv('circle', {r: 8}, home); hc.style.fill = 'var(--surface)'; hc.style.stroke = 'var(--ink)'; hc.style.strokeWidth = '2px';
    sv('circle', {r: 2.5}, home).style.fill = 'var(--ink)';
    var ht = sv('text', {x: 12, y: -10, 'class': 'town left'}, home);
    ht.style.fontWeight = '600'; ht.textContent = 'Home';

    var scaleMi = [0.5, 1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500].filter(function (m) { return m * S <= 130; }).pop() || 0.5;
    clear(foot);
    text(foot, 'div', scaleMi + ' mi');
    node('div', {'class': 'sb', style: 'width:' + (scaleMi * S).toFixed(1) + 'px'}, foot);
  }

  function drawCluster(c, inFeed) {
    var n = c.m.length;
    var g = sv('g', {transform: 'translate(' + c.x.toFixed(1) + ' ' + c.y.toFixed(1) + ')',
                     'class': 'mk', role: 'button', tabindex: c.inside ? 0 : -1}, marksG);
    var dimmed = state.facet && !c.m.some(function (q) { return inFeed[q.p.key]; });
    g.style.opacity = !c.inside ? 0.32 : (dimmed ? 0.3 : 1);
    if (n === 1) {
      var p = c.m[0].p;
      g.setAttribute('aria-label', titleOf(p) + ', ' + p.d.toFixed(1) + ' miles');
      sv('circle', {r: 11}, g).style.fill = 'transparent';
      var big = p.key === state.sel || p.key === state.hov;
      var dot = sv('circle', {r: big ? 7.5 : 5.5}, g);
      dot.style.fill = ST_COLOR[p.status]; dot.style.stroke = 'var(--surface)'; dot.style.strokeWidth = '2px';
    } else {
      g.setAttribute('aria-label', n + ' postings here. List them.');
      var r = 11 + Math.min(9, Math.log2(n) * 2.4), C = 2 * Math.PI * (r - 2), acc = 0;
      var back = sv('circle', {r: r + 1.5}, g); back.style.fill = 'var(--surface)';
      STATUSES.forEach(function (s) {
        var share = c.m.filter(function (q) { return q.p.status === s[0]; }).length / n;
        if (!share) { return; }
        var arc = sv('circle', {r: r - 2, transform: 'rotate(-90)'}, g);
        arc.style.fill = 'none'; arc.style.stroke = ST_COLOR[s[0]]; arc.style.strokeWidth = '3.5px';
        arc.style.strokeDasharray = (share * C) + ' ' + C; arc.style.strokeDashoffset = String(-acc * C);
        acc += share;
      });
      var t = sv('text', {'text-anchor': 'middle', dy: '0.36em'}, g);
      t.style.fill = 'var(--ink)'; t.style.font = '500 11px ui-monospace,Menlo,monospace';
      t.textContent = n;
    }
    g.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
    var open = function () {
      if (n > 1) { listCluster(c); return; }
      var p = c.m[0].p, card = feedKeys()[p.key];
      if (card) { select(p.key, false); card.scrollIntoView({block: 'nearest', behavior: reduced ? 'auto' : 'smooth'}); }
      else { window.location.assign('/job/' + p.job); }
    };
    g.addEventListener('click', open);
    g.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    g.addEventListener('pointerenter', function () { showTip(c); if (n === 1) { hover(c.m[0].p.key); } });
    g.addEventListener('pointerleave', function () { tip.hidden = true; if (n === 1) { hover(null); } });
  }

  /* The map data carries no titles: a posting the radius hides must not be
     on the page at all (tests/test_radius). A listed card names itself. */
  function titleOf(p) {
    var card = feedKeys()[p.key], t = card && card.querySelector('.title');
    return t ? t.textContent.trim() : 'A posting in ' + p.place;
  }
  function showTip(c) {
    clear(tip);
    var inFeed = feedKeys();
    if (c.m.length === 1) {
      var p = c.m[0].p;
      text(tip, 'b', titleOf(p));
      text(tip, 'span', p.place + ' · ' + p.d.toFixed(1) + ' mi · ~' + fmtMin(minutes(p.d)) + ' ' + MODES[state.mode].noun + ' (est.)');
      var where = !c.inside ? 'Outside the circle. Click to open it.'
        : inFeed[p.key] ? 'In the list. Click to find it.'
        : 'Inside, but not in the list (three per company). Click to open it.';
      text(tip, 'span', where);
    } else {
      text(tip, 'b', c.m.length + ' postings ' + (c.inside ? 'inside' : 'outside') + ' the circle');
      text(tip, 'span', STATUSES.map(function (s) {
        var k = c.m.filter(function (q) { return q.p.status === s[0]; }).length;
        return k ? k + ' ' + s[1].toLowerCase() : '';
      }).filter(Boolean).join(' · '));
      text(tip, 'span', 'Click to list them. Scroll to zoom in.');
    }
    var z = size();
    tip.style.left = Math.min(c.x + 16, z.w - 270) + 'px';
    tip.style.top = Math.max(8, Math.min(c.y - 12, z.h - 90)) + 'px';
    tip.hidden = false;
  }

  /* Pan, zoom. */
  var drag = null;
  svg.addEventListener('pointerdown', function (e) {
    if (e.button !== 0) { return; }
    cancelAnimationFrame(anim); flying = false;
    drag = {x: e.clientX, y: e.clientY, cx: view.cx, cy: view.cy, moved: false};
    watchFrames();
    svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener('pointermove', function (e) {
    if (!drag) { return; }
    var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) { drag.moved = true; }
    var u = unshift(view.s, dx, dy);
    view.cx = drag.cx - u[0]; view.cy = drag.cy - u[1];
    redraw();
  });
  function endDrag(e) {
    if (drag && !drag.moved && e.type === 'pointerup') { select(null, false); }
    drag = null;
  }
  svg.addEventListener('pointerup', endDrag);
  svg.addEventListener('pointercancel', endDrag);
  svg.addEventListener('wheel', function (e) {
    e.preventDefault();
    cancelAnimationFrame(anim); flying = false;
    lastWheel = performance.now();
    watchFrames();
    var r = svg.getBoundingClientRect();
    var sx = e.clientX - r.left, sy = e.clientY - r.top, at = toWorld(sx, sy);
    view = anchor(clampS(view.s * Math.exp(-e.deltaY * 0.0016)), at[0], at[1], sx, sy);
    redraw();
  }, {passive: false});
  if (window.ResizeObserver) { new ResizeObserver(redraw).observe(box); }

  /* ---- the panel -------------------------------------------------------- */
  var bandsBox = document.getElementById('rp-bands');
  var fill = document.getElementById('rp-fill');
  bandsBox.checked = state.bands;
  fill.value = state.fill;
  function radio(groupId, attr, value) {
    Array.prototype.forEach.call(document.querySelectorAll('#' + groupId + ' [' + attr + ']'), function (b) {
      b.setAttribute('aria-checked', String(b.getAttribute(attr) === value));
    });
  }
  radio('rp-modes', 'data-mode', state.mode);
  radio('rp-fence', 'data-fence', state.fence);
  document.getElementById('rp-modes').addEventListener('click', function (e) {
    var b = e.target.closest('[data-mode]'); if (!b) { return; }
    state.mode = b.getAttribute('data-mode'); save('mode', state.mode);
    radio('rp-modes', 'data-mode', state.mode); refresh();
  });
  document.getElementById('rp-fence').addEventListener('click', function (e) {
    var b = e.target.closest('[data-fence]'); if (!b) { return; }
    state.fence = b.getAttribute('data-fence'); save('fence', state.fence);
    radio('rp-fence', 'data-fence', state.fence); redraw();
  });
  bandsBox.addEventListener('change', function () { state.bands = bandsBox.checked; save('bands', state.bands); redraw(); });
  fill.addEventListener('input', function () { state.fill = +fill.value; save('fill', state.fill); redraw(); });

  function counter() {
    var by = {interview: 0, applied: 0, saved: 0, 'new': 0}, n = 0, mins = [];
    live.points.forEach(function (p) {
      if (state.on[p.status] && p.d <= R) { n++; by[p.status]++; mins.push(minutes(p.d)); }
    });
    document.getElementById('rp-count').textContent = n;
    document.getElementById('rp-of').textContent = ' postings within ' + fmtMiles(R) +
      (live.remote ? '; ' + live.remote + ' remote have no distance' : '');
    var bar = document.getElementById('rp-bar'); clear(bar);
    STATUSES.forEach(function (s) {
      if (by[s[0]]) { node('span', {'class': 'st-' + s[0], style: 'width:' + (100 * by[s[0]] / n) + '%'}, bar); }
    });
    var med = median(mins);
    document.getElementById('rp-median').textContent = med === null ? 'Nothing inside the circle yet.'
      : 'Median ' + MODES[state.mode].noun + ' inside: about ' + fmtMin(med) + ' (estimated).';
  }

  /* ---- the list --------------------------------------------------------- */
  var feed = document.getElementById('feed');
  document.getElementById('feed-head').hidden = false;
  var chips = document.getElementById('feed-chips');
  STATUSES.forEach(function (s) {
    var b = node('button', {type: 'button', 'class': 'chip st-' + s[0], 'aria-pressed': 'true', 'data-status': s[0]}, chips);
    node('i', {}, b); b.appendChild(document.createTextNode(s[1] + ' ')); node('b', {}, b);
  });
  chips.addEventListener('click', function (e) {
    var b = e.target.closest('[data-status]'); if (!b) { return; }
    var st = b.getAttribute('data-status');
    state.on[st] = !state.on[st];
    b.setAttribute('aria-pressed', String(state.on[st]));
    refresh();
  });
  document.getElementById('feed-sort').addEventListener('click', function (e) {
    var b = e.target.closest('[data-sort]'); if (!b) { return; }
    state.sort = b.getAttribute('data-sort');
    Array.prototype.forEach.call(this.querySelectorAll('[data-sort]'), function (x) {
      x.setAttribute('aria-pressed', String(x === b));
    });
    applyFeed();
  });
  function cards() { return Array.prototype.slice.call(document.querySelectorAll('#feed-list .card[data-key]')); }
  function info(c) { return live.cards[c.getAttribute('data-key')] || {}; }
  function bindFeed() { cards().forEach(function (c, i) { c.setAttribute('data-i', i); }); }
  function facetOk(d) {
    if (!state.facet) { return true; }
    return state.facet.kind === 'skill' ? (d.skills || []).indexOf(state.facet.value) >= 0
                                        : d.company === state.facet.value;
  }
  var SORTS = {
    score: function (a, b) { return +a.getAttribute('data-i') - +b.getAttribute('data-i'); },
    near: function (a, b) {
      var x = info(a), y = info(b);
      var dx = x.remote || x.miles === null || x.miles === undefined ? Infinity : x.miles;
      var dy = y.remote || y.miles === null || y.miles === undefined ? Infinity : y.miles;
      return dx - dy || SORTS.score(a, b);
    },
    pay: function (a, b) {
      var x = info(a).pay, y = info(b).pay;
      return (y ? y[1] : -1) - (x ? x[1] : -1) || SORTS.score(a, b);
    },
    'new': function (a, b) {
      var x = info(a).found || '', y = info(b).found || '';
      return x < y ? 1 : x > y ? -1 : SORTS.score(a, b);
    }
  };
  function applyFeed() {
    var list = document.getElementById('feed-list');
    var all = cards(), counts = {interview: 0, applied: 0, saved: 0, 'new': 0}, visible = 0;
    all.sort(SORTS[state.sort]).forEach(function (c) { list.appendChild(c); });
    all.forEach(function (c) {
      var d = info(c), st = c.getAttribute('data-status'), key = c.getAttribute('data-key');
      counts[st] += 1;
      c.hidden = !(state.on[st] && facetOk(d));
      if (!c.hidden) { visible++; }
      var eta = c.querySelector('.eta');
      if (eta) {
        eta.textContent = d.miles !== null && d.miles !== undefined && !d.remote
          ? ' · about ' + fmtMin(minutes(d.miles)) + ' ' + MODES[state.mode].noun + ' (est.)' : '';
      }
      c.classList.toggle('sel', key === state.sel);
      c.classList.toggle('hov', key === state.hov);
    });
    Array.prototype.forEach.call(chips.querySelectorAll('[data-status]'), function (b) {
      b.querySelector('b').textContent = counts[b.getAttribute('data-status')];
    });
    var none = document.getElementById('feed-none');
    if (!visible && all.length) {
      if (!none) { none = text(list, 'p', 'Everything in the list is hidden by the choices above.', 'empty'); none.id = 'feed-none'; }
    } else if (none) { none.remove(); }
    var facet = document.getElementById('feed-facet'); clear(facet);
    facet.hidden = !state.facet;
    if (state.facet) {
      text(facet, 'span', 'Only ' + (state.facet.kind === 'skill' ? 'asking for ' : 'at ') + state.facet.value, 'meta');
      var x = node('button', {type: 'button'}, facet); x.textContent = 'Show all';
      x.addEventListener('click', function () { state.facet = null; refresh(); });
    }
    analytics();
  }
  function hover(key) {
    if (state.hov === key) { return; }
    state.hov = key;
    cards().forEach(function (c) { c.classList.toggle('hov', c.getAttribute('data-key') === key); });
    redraw();
  }
  function select(key, flyTo) {
    state.sel = key;
    cards().forEach(function (c) { c.classList.toggle('sel', c.getAttribute('data-key') === key); });
    if (flyTo && key) {
      var best = null;
      live.points.forEach(function (p) { if (p.key === key && (!best || p.d < best.d)) { best = p; } });
      if (best) { fly(centerOn(best.x, best.y, Math.max(view.s, fitView(12).s))); }
    }
    redraw();
  }
  feed.addEventListener('mouseover', function (e) {
    var c = e.target.closest('.card[data-key]'); hover(c ? c.getAttribute('data-key') : null);
  });
  feed.addEventListener('mouseleave', function () { hover(null); });
  feed.addEventListener('click', function (e) {
    if (e.target.closest('a,button')) { return; }
    var c = e.target.closest('.card[data-key]'); if (!c) { return; }
    select(c.getAttribute('data-key'), true);
  });

  /* ---- analytics of what the list shows ---------------------------------- */
  var an = document.getElementById('an-body'), anSum = document.getElementById('an-sum');
  function bars(parent, title, note, rows, pick) {
    var col = node('div', {}, parent);
    var h = text(col, 'h3', title); if (note) { text(h, 'small', note); }
    var ol = node('ol', {}, col), top = Math.max.apply(null, rows.map(function (r) { return r[1]; }).concat([1]));
    rows.forEach(function (r) {
      var li = node('li', {}, ol);
      text(li, 'span', r[0]).title = r[0];
      node('i', {style: 'width:' + (100 * r[1] / top) + '%'}, node('span', {'class': 'track'}, li));
      text(li, 'b', String(r[1]));
      if (pick) {
        var on = !!state.facet && state.facet.kind === pick && state.facet.value === r[0];
        li.setAttribute('role', 'button'); li.setAttribute('tabindex', '0');
        li.setAttribute('aria-pressed', String(on));
        var go = function () { state.facet = on ? null : {kind: pick, value: r[0]}; refresh(); };
        li.addEventListener('click', go);
        li.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
      }
    });
    if (!rows.length) { text(col, 'p', 'Nothing to count.', 'foot'); }
    return col;
  }
  function tally(xs) {
    var m = {};
    xs.forEach(function (x) { m[x] = (m[x] || 0) + 1; });
    return Object.keys(m).map(function (k) { return [k, m[k]]; })
      .sort(function (a, b) { return b[1] - a[1] || (a[0] < b[0] ? -1 : 1); });
  }
  function analytics() {
    var shownCards = cards().filter(function (c) { return !c.hidden; }).map(info);
    clear(an);
    var pays = shownCards.filter(function (d) { return d.pay; }).map(function (d) { return (d.pay[0] + d.pay[1]) / 2; });
    var BANDS = [[0, 60000, 'under 60k'], [60000, 90000, '60-90k'], [90000, 120000, '90-120k'],
                 [120000, 150000, '120-150k'], [150000, 200000, '150-200k'], [200000, Infinity, '200k and up']];
    var pay = bars(an, 'Pay, by midpoint', pays.length + ' of ' + shownCards.length + ' state it',
      BANDS.map(function (b) { return [b[2], pays.filter(function (v) { return v >= b[0] && v < b[1]; }).length]; }));
    var mp = median(pays);
    if (mp !== null) { text(pay, 'p', 'Median ' + money(mp) + ' a year. Hourly pay is counted at 2,080 hours.', 'foot'); }
    var withSkills = shownCards.filter(function (d) { return d.skills && d.skills.length; });
    bars(an, 'Skills asked for', 'from ' + withSkills.length + ' read by enrich',
      tally([].concat.apply([], withSkills.map(function (d) { return d.skills; }))).slice(0, 8), 'skill');
    bars(an, 'Hiring here', 'click to filter',
      tally(shownCards.map(function (d) { return d.company; })).slice(0, 6), 'company');
    var placed = shownCards.filter(function (d) { return d.miles !== null && d.miles !== undefined && !d.remote; });
    var ringsNow = MODES[state.mode].rings, rows = ringsNow.map(function (t, i) {
      var lo = i ? ringsNow[i - 1] : 0;
      return [(i ? lo + '-' : 'up to ') + fmtMin(t), placed.filter(function (d) { var m = minutes(d.miles); return m > lo && m <= t; }).length];
    });
    var last = ringsNow[ringsNow.length - 1];
    rows.push(['over ' + fmtMin(last), placed.filter(function (d) { return minutes(d.miles) > last; }).length]);
    var com = bars(an, 'Time ' + MODES[state.mode].noun, 'estimated', rows);
    var remoteN = shownCards.length - placed.length;
    if (remoteN) { text(com, 'p', remoteN + ' remote or unplaced, with no trip to estimate.', 'foot'); }
    anSum.textContent = shownCards.length + ' in the list' + (mp !== null ? ' · median pay ' + money(mp) : '');
  }

  /* ---- the radius: live circle, then the server's list -------------------- */
  var timer = 0, inflight = null;
  function requery(ms) { clearTimeout(timer); timer = setTimeout(function () { fetchList(); }, ms); }
  /* The list, from the server. With `pick` it is exactly the cards of one
     opened bubble: the same page asked for by card key, so every card and
     every rule on it is the server's, as for the radius. A picked list is a
     moment, not a place: it is not written into the address bar, and the
     next radius change brings the whole list back. */
  function fetchList(pick) {
    var params = new URLSearchParams(new FormData(form));
    params.set('radius', String(R));
    params.delete('anywhere');
    params.delete('only');
    var url = '?' + params.toString();
    if (pick) { params.set('only', pick.keys.join(',')); }
    var ask = '?' + params.toString();
    if (inflight) { inflight.abort(); }
    var mine = inflight = new AbortController();
    var list = document.getElementById('feed-list');
    list.setAttribute('aria-busy', 'true'); list.style.opacity = 0.55;
    fetch(ask, {signal: mine.signal, credentials: 'same-origin'})
      .then(function (r) { if (!r.ok) { throw new Error(String(r.status)); } return r.text(); })
      .then(function (html) {
        if (mine !== inflight) { return; }
        inflight = null;
        var doc = new DOMParser().parseFromString(html, 'text/html');
        (pick ? ['feed-list'] : ['summary', 'feed-list']).forEach(function (id) {
          var a = document.getElementById(id), b = doc.getElementById(id);
          if (a && b) { a.replaceWith(document.importNode(b, true)); }
        });
        var d = doc.getElementById('map-data');
        if (d) { live.cards = JSON.parse(d.textContent).cards; }
        if (pick) { pickedBanner(pick); } else { history.replaceState(null, '', url); }
        bindFeed(); applyFeed(); redraw();
      })
      .catch(function (e) {
        if (e.name === 'AbortError') { return; }
        var l = document.getElementById('feed-list');
        l.style.opacity = 1; l.removeAttribute('aria-busy');
        if (!document.getElementById('feed-stale')) {
          var p = text(l, 'p', 'The list did not refresh for ' + fmtMiles(R) + '. Press Filter to reload the page.', 'note bad');
          p.id = 'feed-stale'; l.insertBefore(p, l.firstChild);
        }
      });
  }
  /* A bubble with several postings, opened: list them all. */
  function listCluster(c) {
    var keys = [], seen = {}, where = {};
    c.m.forEach(function (q) {
      if (!seen[q.p.key]) { seen[q.p.key] = true; keys.push(q.p.key); }
      where[q.p.place] = (where[q.p.place] || 0) + 1;
    });
    var places = Object.keys(where).sort(function (a, b) { return where[b] - where[a]; });
    var label = places.length === 1 ? places[0]
      : places.slice(0, 3).map(function (n) { return n + ' (' + where[n] + ')'; }).join(', ') +
        (places.length > 3 ? ' and ' + (places.length - 3) + ' more' : '');
    fetchList({keys: keys, postings: c.m.length, label: label});
  }
  function pickedBanner(pick) {
    var list = document.getElementById('feed-list');
    var cards = list.querySelectorAll('[data-key]').length;
    var bar = node('div', {'class': 'picked', role: 'status'}, null);
    text(bar, 'b', pick.postings + (pick.postings === 1 ? ' posting' : ' postings'));
    bar.appendChild(document.createTextNode(' in ' + pick.label +
      (cards !== pick.postings ? ', on ' + cards + (cards === 1 ? ' card' : ' cards') : '')));
    var back = node('button', {type: 'button'}, bar);
    back.textContent = 'Show the whole list';
    back.addEventListener('click', function () { fetchList(); });
    list.insertBefore(bar, list.firstChild);
    bar.scrollIntoView({block: 'start', behavior: reduced ? 'auto' : 'smooth'});
  }
  slider.addEventListener('input', function () {
    R = +slider.value;
    shown.textContent = slider.value + ' miles';
    counter(); redraw(); requery(250);
  });
  slider.addEventListener('change', function () {
    requery(0);
    var z = size();
    if (R * view.s > Math.min(z.w - panelWidth(), z.h) / 2) { fly(fitView(R)); }
  });
  anywhere.addEventListener('change', function () { form.submit(); });

  function refresh() { counter(); applyFeed(); redraw(); }

  var note = document.getElementById('map-note');
  if (note) {
    note.textContent = live.points.length + ' posting(s) on the map' +
      (live.remote ? '; ' + live.remote + ' remote, with no distance to draw' : '') +
      (live.unplaced ? '; ' + live.unplaced + ' naming a place this could not find' : '') +
      '. Drag to pan, scroll to zoom. Distances are straight-line miles from ' + live.home + '.';
  }
  view = fitView(R);
  bindFeed();
  refresh();
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
{% if application and application.status in interview_rounds %}
<form method="post" action="/job/{{ job.id }}/prep" class="inline" style="margin-top:8px"
      onsubmit="var b=this.querySelector('button');b.disabled=true;b.textContent='Drafting prep…'">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <select name="round" id="prep-round" aria-label="Interview round">
    {% for r in interview_rounds %}<option value="{{ r }}" {{ 'selected' if r == application.status }}>{{ r.replace('_', ' ') }}</option>{% endfor %}
  </select>
  <button type="submit">Draft interview prep</button>
  <span class="meta">One model call; questions grounded in this posting and your profile.</span>
</form>
{% endif %}

<h2>Posting</h2>
{% if job.enrichment_note %}<p class="sub">{{ job.enrichment_note }}</p>{% endif %}
<p><a class="plain" href="{{ job.url }}" rel="noopener">Open the original posting</a>{% if job.description_origin == 'pasted' %} · <span class="meta">text pasted by you; discovery will not overwrite it</span>{% endif %}</p>
{% if thin %}
<p class="note bad">{{ thin[:1]|upper }}{{ thin[1:] }}</p>
<form class="stack" method="post" action="/job/{{ job.id }}/fill" onsubmit="var b=this.querySelector('button');b.disabled=true;b.textContent='Saving…'">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <label for="fill-location">Where the job is, as the posting says it (the stub's is often wrong)
    <input type="text" name="location" id="fill-location" value="{{ job.location or '' }}" placeholder="Austin, TX, or Remote (US)"></label>
  <label for="fill-text">Paste the full posting from the link above, requirements included
    <textarea name="text" id="fill-text" required minlength="{{ min_chars }}"></textarea></label>
  <button type="submit">Use this text for this job</button>
  <p class="meta" style="margin:0">It replaces the stub in this job; its number, application and drafts stay. Pay and score are re-read from it. Nothing is fetched.</p>
</form>
{% endif %}
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
<h2>Replies from your email{% if replies %} · {{ replies|length }}{% endif %}</h2>
<form method="post" action="/inbox/fetch" class="inline" onsubmit="var b=this.querySelector('button');b.disabled=true;b.textContent='Checking…'">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <button class="ghost" type="submit" {{ '' if mail_ready else 'disabled' }}>Check email</button>
  <span class="meta">{% if mail_ready %}Reads your mailbox read-only: nothing is marked read, nothing is sent, and nothing moves until you confirm.{% else %}Not set up: add a Gmail app password to .env (see .env.example).{% endif %}</span>
</form>
{% for r in replies %}
<div class="card">
  <div class="row1">
    <span class="flag {{ 'warn' if r.kind == 'rejection' else ('ok' if r.kind in ('interview','offer') else '') }}">{{ r.kind }}</span>
    {% if r.application_id %}<span class="title"><a class="plain" href="/job/{{ r.job_id }}">{{ r.title }}</a></span>
    <span class="co">{{ r.company }} · now {{ r.status|replace('_',' ') }}</span>
    {% else %}<span class="title">Unclear which application</span>{% endif %}
  </div>
  <div class="meta">{{ (r.received_at or '')[:10] }} · {{ r.sender_domain }} · {{ r.subject }}</div>
  <div class="inline">
    {% if r.application_id %}
    <form method="post" action="/inbox/{{ r.id }}/confirm" class="inline">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <select name="stage" aria-label="Stage for {{ r.title }}">
        {% for s in stages %}<option value="{{ s }}" {{ 'selected' if s == (r.suggested_stage or r.status) }}>{{ s|replace('_',' ') }}</option>{% endfor %}
      </select>
      <button type="submit">Confirm</button>
    </form>
    {% endif %}
    <form method="post" action="/inbox/{{ r.id }}/dismiss" class="inline">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button class="ghost" type="submit">Dismiss</button>
    </form>
  </div>
</div>
{% else %}<p class="empty">No replies waiting on you.</p>{% endfor %}
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
<details id="what-happened" style="margin-top:18px"><summary>What happened · {{ happened_line }}</summary>
{% if happened %}<ul style="margin:8px 0 0;padding-left:18px">{% for o in happened %}
  <li><a class="plain" href="/job/{{ o.job_id }}">#{{ o.job_id }} {{ o.company }}</a>: {{ o.point.replace('_', ' ') }}{% if o.closed %}, closed{% endif %}{% if o.quiet %}, quiet{% endif %}{% if o.days_since_applied is not none %} <span class="meta">· applied {{ o.days_since_applied }} day(s) ago</span>{% endif %}</li>
{% endfor %}</ul>{% endif %}
<p class="meta">Furthest point each application reached; an undone stage doesn't count, and an automatic receipt isn't hearing back. Counts by group: <code>jsa outcomes --by source_kind</code>. A report: nothing here changes anything.</p>
</details>
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

  {% if r.coach %}<div class="note" style="background:var(--surface-2)">
    <strong>Resume report</strong> <span class="meta">(advice for your profile; it does not block approving)</span>
    <ul style="margin:6px 0 0;padding-left:18px">{% for f in r.coach %}<li><strong>{{ f.id }}</strong>: {{ f.message }} <span class="meta">{{ f.where }}</span></li>{% endfor %}</ul>
  </div>{% endif %}
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
    <select name="reason" id="reason-{{ r.approval_id }}" aria-label="Kind of problem (optional)">
      <option value="">Kind of problem (optional)</option>
      {% for code in reject_reasons %}<option value="{{ code }}">{{ code.replace('_', ' ') }}</option>{% endfor %}
    </select>
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
<p class="sub">Setting up your profile? <a class="plain" href="/import">Import your resume</a> instead.</p>
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

IMPORT = """{% extends "base" %}{% block body %}
<h1>Import your resume</h1>
<p class="sub">Turns your resume into a <strong>draft</strong> profile, <code>{{ draft_name }}</code>, and shows what it would match. Your real profile is never changed: adopting the draft is a copy you make yourself.</p>
{% if msg %}<p class="note bad" role="alert">{{ msg }}</p>{% endif %}
<form class="stack" method="post" action="/import" enctype="multipart/form-data" id="import-form">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <label for="f-resume" class="drop" id="drop">
    <span class="drop-title">Drop your resume here, or choose a file</span>
    <span class="meta">{{ kinds }}, up to {{ max_mb }} MB</span>
    <input type="file" id="f-resume" name="resume" accept="{{ accept }}" required>
  </label>
  <label class="check" for="f-replace"><input type="checkbox" id="f-replace" name="replace" value="1"> Replace my existing draft</label>
  <button type="submit">Import</button>
  <p class="meta" style="margin:0">What leaves your machine: the resume's text, with your name and contact details removed first, goes to the model API set in <code>.env</code> (one call). The file itself is read in a temporary folder and deleted; it is not stored.</p>
</form>
<style>
  form.stack label.drop{display:flex;flex-direction:column;gap:6px;align-items:center;justify-content:center;
    border:2px dashed var(--rule);border-radius:8px;padding:28px 16px;text-align:center;cursor:pointer}
  .drop.over{border-color:var(--copper);background:var(--sage-soft)}
  .drop-title{font-weight:600}
  .drop input{max-width:100%}
  form.stack label.check{display:flex;gap:8px;align-items:center;font-size:13.5px}
  form.stack label.check input{width:auto;margin:0}
</style>
<script>
(function(){
  var form=document.getElementById('import-form'), drop=document.getElementById('drop'),
      input=document.getElementById('f-resume');
  form.addEventListener('submit',function(){var b=form.querySelector('button');b.disabled=true;b.textContent='Reading your resume…';});
  ['dragenter','dragover'].forEach(function(e){drop.addEventListener(e,function(ev){ev.preventDefault();drop.classList.add('over');});});
  ['dragleave','drop'].forEach(function(e){drop.addEventListener(e,function(ev){ev.preventDefault();drop.classList.remove('over');});});
  drop.addEventListener('drop',function(ev){
    if(!ev.dataTransfer||!ev.dataTransfer.files.length)return;
    input.files=ev.dataTransfer.files;
    if(form.reportValidity()){form.requestSubmit?form.requestSubmit():form.submit();}
  });
})();
</script>
{% endblock %}"""

IMPORTED = """{% extends "base" %}{% block body %}
<h1>Your draft profile</h1>
<p class="note good" role="status">Wrote <code>{{ draft_path }}</code>. Your real profile was not changed.</p>
{% if warning %}<p class="note bad" role="alert">{{ warning[0]|upper }}{{ warning[1:] }}</p>{% endif %}
<p class="sub">Imported {{ v.experience|length }} job(s), {{ v.projects|length }} project(s), {{ v.bullet_count }} bullet(s), {{ v.certifications|length }} certification(s), {{ v.education|length }} school(s) and {{ v.skills|length }} skill(s), each copied word for word from your resume.</p>

{% if v.dropped %}<h2>Left out ({{ v.dropped|length }})</h2>
<p class="sub">Nothing is reworded into your profile. These were not found word for word in your resume; copy any you want across by hand.</p>
<ul>{% for d in v.dropped %}<li><strong>{{ d.where }}:</strong> {{ d.what }} <span class="meta">({{ d.why }})</span></li>{% endfor %}</ul>{% endif %}

{% if blocking %}<h2>Fill these in, in the draft ({{ blocking|length }})</h2>
<ul>{% for f in blocking %}<li><strong>{{ f.what }}</strong><br><span class="meta">{{ f.fix }}</span></li>{% endfor %}</ul>{% endif %}

<h2>What the draft would match</h2>
{% if no_preview %}<p class="sub">No preview yet: {{ no_preview }}</p>
{% else %}<p class="sub">From jobs already in your tracker, which were found using your current profile. Run <code>jsa discover</code> after adopting the draft for a full search.</p>
{% if matches %}<ul>{% for m in matches %}<li><a class="plain" href="/job/{{ m.job_id }}">{{ m.company }}: {{ m.title }}</a> <span class="meta">{{ '%.2f'|format(m.score) }}</span></li>{% endfor %}</ul>
{% else %}<p class="sub">Nothing in the tracker scores above 0 for this draft.</p>{% endif %}{% endif %}

<h2>Next</h2>
<ol>
  <li>Review <code>{{ draft_path }}</code>: the TODO lines, and every line marked <code># suggested: check</code>.</li>
  <li>Copy it over your profile:<br><code>copy "{{ draft_path }}" "{{ live_path }}"</code></li>
  <li>Run <code>jsa doctor</code>, then <code>jsa discover</code>.</li>
</ol>
{% endblock %}"""

env = Environment(
    loader=DictLoader({"base": BASE, "matches": MATCHES, "job": JOB,
                       "prep": PREP, "pipeline": PIPELINE, "review": REVIEW,
                       "preview": PREVIEW, "add": ADD,
                       "import": IMPORT, "imported": IMPORTED}),
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


# The four colours on the Matches map (n21). A card with no application is
# "new"; a live application is grouped by how far along it is. Closed ones
# (approvals.CLOSED) are never on this page. The pill on a card still says
# the real stage -- "offer", "phone screen" -- the group is only its colour.
STATUS_GROUP = {
    "saved": "saved", "drafting": "saved", "ready": "saved",
    "applied": "applied",
    "phone_screen": "interview", "technical": "interview",
    "onsite": "interview", "offer": "interview",
}


def _annual(row: dict[str, Any]) -> tuple[int | None, int | None]:
    """The posting's pay as yearly dollars, or (None, None) when it states none.

    Hourly figures are multiplied out the same way jsa.salary does, so the
    dashboard's pay ranges compare like with like.
    """
    from .salary import HOURS_PER_YEAR

    low, high = row.get("salary_min"), row.get("salary_max")
    if low is None and high is None:
        return None, None
    # Dollars compare with dollars; a figure in another currency is not
    # averaged into a USD median (n22).
    if (row.get("salary_currency") or "USD").upper() != "USD":
        return None, None
    low = low if low is not None else high
    high = high if high is not None else low
    if row.get("salary_period") == "hour":
        return low * HOURS_PER_YEAR, high * HOURS_PER_YEAR
    return low, high


def _pipeline_rows(con: sqlite3.Connection, where: list[str],
                   params: dict[str, Any]) -> list[dict[str, Any]]:
    """Live applications, shaped like v_new_matches cards, under the page's
    own filters. Not capped per company: these are the operator's own."""
    closed = ",".join(f"'{s}'" for s in sorted(approvals.CLOSED))
    rows = con.execute(
        "SELECT m.id AS job_id, c.name AS company, m.title, m.location, "
        "       m.remote, m.match_score, m.match_reasons, m.url, m.track, "
        "       m.discovered_at, 1 AS variant_count, NULL AS dedup_key, "
        "       m.degree_required, m.clearance_required, m.years_required, "
        "       m.tech_stack, m.enrichment_note, m.enriched_at, "
        "       m.salary_min, m.salary_max, m.salary_period, m.salary_currency, "
        "       a.status AS stage "
        "  FROM applications a JOIN jobs m ON m.id = a.job_id "
        "  LEFT JOIN companies c ON c.id = m.company_id "
        f" WHERE a.archived_at IS NULL AND a.status NOT IN ({closed}) "
        f"   AND {' AND '.join(where)}", params).fetchall()
    out = []
    for row in rows:
        card = _decode(row)
        card["status"] = STATUS_GROUP.get(card["stage"], "saved")
        card["key"] = f"app:{card['job_id']}"
        out.append(card)
    return out


def _card_data(rows) -> dict[str, dict[str, Any]]:
    """What the page's script needs to know about each listed card, keyed the
    way the map's points are. Presentation only: the list itself is already
    decided."""
    out = {}
    for row in rows:
        low, high = _annual(row)
        out[row["key"]] = {
            "company": row.get("company") or "",
            "status": row["status"],
            "miles": row.get("exact_miles"),
            "remote": bool(row.get("any_remote")),
            "pay": [low, high] if low is not None else None,
            "skills": [str(s) for s in row.get("stack") or []][:12],
            "found": row.get("discovered_at") or "",
            "score": row.get("match_score") or 0,
        }
    return out


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

# The only upload is a resume (ADR 0022); real ones are well under 1 MB.
MAX_UPLOAD_BYTES = 5_000_000


def multipart_field(body: bytes, content_type: str, name: str) -> str:
    """One small text field from a multipart body ("" if absent or malformed).

    The guard needs the token before the route runs, and FastAPI's own form
    parsing would consume the body.
    """
    from python_multipart.multipart import MultipartParser, parse_options_header

    _, options = parse_options_header(content_type)
    boundary = options.get(b"boundary")
    if not boundary:
        return ""
    state: dict[str, Any] = {"field": b"", "value": b"", "name": None,
                             "data": [], "found": None}

    def on_header_field(data, start, end):
        state["field"] += data[start:end]

    def on_header_value(data, start, end):
        state["value"] += data[start:end]

    def on_header_end():
        if state["field"].lower() == b"content-disposition":
            _, opts = parse_options_header(state["value"])
            state["name"] = opts.get(b"name")
        state["field"], state["value"] = b"", b""

    def on_part_begin():
        state["name"], state["data"] = None, []

    def on_part_data(data, start, end):
        if state["name"] == name.encode():
            state["data"].append(data[start:end])

    def on_part_end():
        if state["name"] == name.encode() and state["found"] is None:
            state["found"] = b"".join(state["data"])

    try:
        parser = MultipartParser(boundary, {
            "on_part_begin": on_part_begin, "on_part_data": on_part_data,
            "on_part_end": on_part_end, "on_header_field": on_header_field,
            "on_header_value": on_header_value, "on_header_end": on_header_end})
        parser.write(body)
        parser.finalize()
    except Exception:  # noqa: BLE001 - a malformed body simply has no token
        return ""
    return (state["found"] or b"").decode("ascii", "ignore")


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
            kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if kind == "multipart/form-data":
                # An upload (the resume import). Refuse a large or unsized one
                # BEFORE reading it: this guard holds the whole body in memory.
                length = request.headers.get("content-length", "")
                if not length.isdigit() or int(length) > MAX_UPLOAD_BYTES:
                    return PlainTextResponse(
                        "That file is too large. A resume is well under "
                        f"{MAX_UPLOAD_BYTES // 1_000_000} MB.", status_code=413)
            # The raw body, not request.form(): parsing the form here consumes
            # it, and the route then receives nothing.
            raw = await request.body()
            if kind == "multipart/form-data":
                if len(raw) > MAX_UPLOAD_BYTES:  # the header said otherwise
                    return PlainTextResponse("That file is too large.",
                                             status_code=413)
                sent = multipart_field(raw, request.headers["content-type"], "csrf")
            elif kind in ("application/x-www-form-urlencoded", ""):
                sent = parse_qs(raw.decode("utf-8", "replace")).get("csrf", [""])[0]
            else:
                # No other format may slip past the token check.
                sent = ""
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
                home: str = "", radius: str = "", anywhere: str = "",
                only: str = "", q: str = ""):
        from . import mapview
        from .cli import load_regions, region_clause
        where, params = ["1=1"], {}
        if near and near in load_regions():
            where.append(f"({region_clause(load_regions(), near)})")
        if track in ("engineering", "sales"):
            # No longer on the form (Plan 7), still honoured so old links work.
            where.append("m.track = :track")
            params["track"] = track
        q = q.strip()
        if q:
            # One condition in the shared list, so the cards, the map's dots
            # and your own applications all filter by it (ADR 0015). The
            # function is registered on the connection below.
            where.append("title_matches(m.title, :q) = 1")
            params["q"] = q
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
        if q:
            from .scoring import title_matches
            con.create_function("title_matches", 2, title_matches,
                                deterministic=True)
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
            for row in rows:
                row["status"] = "new"
                row["key"] = row.get("dedup_key") or f"job:{row['job_id']}"
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
                "AND m.closed_at IS NULL AND NOT EXISTS "
                "(SELECT 1 FROM applications a WHERE a.job_id = m.id) "
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
            rows, hidden, unplaced = _by_distance(rows, origin, wanted, copies)
            rows = _per_company(rows, PER_COMPANY)[:limit]
            mine, _, _ = _by_distance(mine, origin, wanted)
        rows = sorted(mine + rows, key=lambda r: -(r.get("match_score") or 0))
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
            from . import basemap
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

    @app.get("/basemap/{tier}")
    def basemap_tier(tier: str, home: str = "", x: float = 0.0, y: float = 0.0,
                     v: str = ""):   # v: only makes the URL change with the data
        """The ground under the live map around (x, y) miles from the centre
        (n23). Same origin, same Host check as every other route: the only
        place map data comes from is this machine."""
        import math

        from . import basemap
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
        from . import intake, posting
        return render("job", "matches", title=row["title"], job=dict(row),
                      thin=posting.thin(row["description"], job_id),
                      min_chars=intake.MIN_PASTED_CHARS,
                      stack=_json_list(row["tech_stack"]),
                      application=dict(application) if application else None,
                      documents=documents, preps=preps, msg=msg, bad=bad,
                      interview_rounds=INTERVIEW_ROUNDS)

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
        # Both were printed by `jsa tailor` and dropped here.
        if result.description_note:
            parts.append("Note: the " + result.description_note + ".")
        if result.thin_note:
            parts.append("Warning: " + result.thin_note)
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

    @app.post("/job/{job_id}/fill")
    def fill_posting(job_id: int, text: str = Form(...), location: str = Form("")):
        """Paste the real posting into a stub, in place (ADR 0020)."""
        from . import intake
        from .config import Preferences
        prof = profile()
        if prof is None:
            return back_to_job(job_id, "Your profile could not be loaded, so the "
                                       "job cannot be scored.", True)
        con = connect()
        try:
            before = con.execute("SELECT match_score FROM jobs WHERE id = ?",
                                 (job_id,)).fetchone()
            if before is None:
                return not_found("job")
            filled = intake.fill(con, job_id, text, Preferences.from_profile(prof),
                                 location=location)
            con.commit()
            filled.enriched = intake.enrich(con, job_id)
            con.commit()
        except intake.IntakeError as exc:
            return back_to_job(job_id, str(exc), True)
        finally:
            con.close()
        parts = ["Posting saved to this job. Score "
                 f"{before['match_score'] or 0:.2f} -> {filled.score:.2f}."]
        if filled.enriched:
            parts.append(filled.enriched[0].upper() + filled.enriched[1:] + ".")
        parts.extend(filled.warnings)
        if filled.status:
            parts.append("Draft a new version to use it.")
        return back_to_job(job_id, " ".join(parts), False)

    def import_page(msg: str = "") -> HTMLResponse:
        from . import resume_import as ri
        return render("import", "add", title="Import your resume", msg=msg,
                      draft_name="profile/" + ri.DRAFT_NAME,
                      accept=",".join(sorted(ri.SUPPORTED)),
                      kinds=" or ".join(sorted(ri.SUPPORTED)),
                      max_mb=MAX_UPLOAD_BYTES // 1_000_000)

    @app.get("/import", response_class=HTMLResponse)
    def import_form(msg: str = ""):
        return import_page(msg)

    @app.post("/import", response_class=HTMLResponse)
    def import_resume(resume: UploadFile = File(...), replace: str = Form("")):
        """Resume -> draft profile (ADR 0022). Never writes the live profile;
        the upload lives only in a temporary directory for the import."""
        import tempfile

        from . import config
        from . import llm
        from . import resume_import as ri
        from .config import ConfigError

        name = Path(resume.filename or "").name
        suffix = Path(name).suffix.lower()
        if suffix not in ri.SUPPORTED:
            return import_page(f"{name or 'That file'} is not a "
                               f"{' or '.join(sorted(ri.SUPPORTED))} file. {ri.DOCX_ONLY}")
        data = resume.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            return import_page("That file is too large.")
        if not data.startswith(ri.SUPPORTED[suffix]):
            # Never trust the name or the browser's content type.
            return import_page(f"{name} is named {suffix} but is not one inside. "
                               f"{ri.DOCX_ONLY}")
        live = config.PROFILE_PATH
        con = connect()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / ("resume" + suffix)
                path.write_bytes(data)
                report = ri.run(path, out=live.with_name(ri.DRAFT_NAME), live=live,
                                force=bool(replace), con=con, source_name=name)
        except (ConfigError, llm.LLMError) as exc:
            return import_page(str(exc))
        except Exception as exc:  # noqa: BLE001 - e.g. the identity guard
            return import_page(f"The import stopped: {exc}")
        finally:
            con.close()
        return render("imported", "add", title="Your draft profile",
                      v=report.verified, blocking=report.blocking,
                      warning=report.warning,
                      matches=report.matches, no_preview=report.no_preview,
                      draft_path=str(report.draft_path), live_path=str(live))

    @app.post("/job/{job_id}/prep")
    def draft_prep(job_id: int, round: str = Form(...)):
        """Pressing the button is the request (ADR 0003 decision 6), as with
        Tailor: the same steps as `jsa prep`, one model call, nothing else."""
        from . import prep as prep_mod
        from .llm import LLMError
        from .tailor import UndecidedPreferenceError, require_decided_preferences

        if round not in INTERVIEW_ROUNDS:
            return back_to_job(job_id, f"{round} is not an interview round.", True)
        prof = profile()
        if prof is None:
            return back_to_job(job_id, "Your profile could not be loaded.", True)
        con = connect()
        try:
            app_row = con.execute("SELECT id FROM applications WHERE job_id = ?",
                                  (job_id,)).fetchone()
            if app_row is None:
                return back_to_job(job_id, "Save this job first.", True)
            require_decided_preferences(prof)
            prep_mod.generate(con, app_row["id"], round=round, profile=prof)
            prep_id = con.execute(
                "SELECT MAX(id) FROM interview_prep WHERE application_id = ?",
                (app_row["id"],)).fetchone()[0]
            con.commit()
        except (prep_mod.DegreeClaimError, UndecidedPreferenceError, LLMError,
                ValueError) as exc:
            return back_to_job(job_id, str(exc), True)
        finally:
            con.close()
        return RedirectResponse(f"/prep/{prep_id}", status_code=303)

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
            from . import inbox, outcomes
            replies = [dict(r) for r in inbox.pending(con)]
            happened = outcomes.all_outcomes(con)
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
                      replies=replies, mail_ready=inbox.settings() is not None,
                      happened=happened, happened_line=outcomes.summary(happened),
                      msg=msg, bad=bad)

    @app.post("/inbox/fetch")
    def inbox_fetch():
        """Read the mailbox, read-only, and store suggestions (ADR 0021)."""
        from . import inbox
        con = connect()
        try:
            report = inbox.fetch(con)
        except inbox.InboxError as exc:
            return back_to_pipeline(str(exc)[:1].upper() + str(exc)[1:], True)
        except OSError as exc:
            return back_to_pipeline(f"Could not reach the mailbox: {exc}", True)
        finally:
            con.close()
        return back_to_pipeline(
            f"Checked {report.user}: {report.searched} message(s) looked at, "
            f"{report.stored} new repl(ies) about your applications.", False)

    @app.post("/inbox/{reply_id}/confirm")
    def inbox_confirm(reply_id: int, stage: str = Form(...)):
        """Your confirmation moves the stage: the same call as the stage form."""
        from . import inbox, prep
        con = connect()
        try:
            done = inbox.confirm(con, reply_id, stage=stage)
            con.commit()
            hint = prep.suggestion(con, done.application_id, done.stage)
        except (inbox.InboxError, approvals.ApprovalError) as exc:
            return back_to_pipeline(str(exc)[:1].upper() + str(exc)[1:], True)
        finally:
            con.close()
        return back_to_pipeline(
            f"Moved from {done.previous.replace('_', ' ')} to "
            f"{done.stage.replace('_', ' ')}." + (f" {hint}." if hint else ""), False)

    @app.post("/inbox/{reply_id}/dismiss")
    def inbox_dismiss(reply_id: int):
        from . import inbox
        con = connect()
        try:
            inbox.dismiss(con, reply_id)
            con.commit()
        except inbox.InboxError as exc:
            return back_to_pipeline(str(exc)[:1].upper() + str(exc)[1:], True)
        finally:
            con.close()
        return back_to_pipeline("Dismissed. Nothing else changed.", False)

    @app.post("/job/{job_id}/stage")
    def do_stage(job_id: int, stage: str = Form(...)):
        """The same approvals call `jsa status` makes. ADR 0003 decision 5."""
        from . import prep
        con = connect()
        try:
            application_id, previous = approvals.set_stage(con, job_id, stage)
            con.commit()
            hint = prep.suggestion(con, application_id, stage)
        except approvals.ApprovalError as exc:
            return back_to_pipeline(str(exc), True)
        finally:
            con.close()
        return back_to_pipeline(
            f"Moved from {previous.replace('_', ' ')} to "
            f"{stage.replace('_', ' ')}." + (f" {hint}." if hint else ""), False)

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
                    # The resume report, as it was when this was drafted.
                    try:
                        item["coach"] = json.loads(d["coach_findings"] or "[]") if d else []
                    except (ValueError, IndexError, KeyError):
                        item["coach"] = []
                elif r["subject_type"] == "outreach":
                    o = con.execute("SELECT draft_body FROM outreach WHERE id = ?",
                                    (r["subject_id"],)).fetchone()
                    item["body"] = o["draft_body"] if o else None
                rows.append(item)
        finally:
            con.close()
        return render("review", "review", title="Review", rows=rows,
                      reject_reasons=approvals.REJECT_REASONS,
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
    def do_reject(approval_id: int = Form(...), feedback: str = Form(""),
                  reason: str = Form("")):
        con = connect()
        try:
            approvals.reject(con, approval_id, feedback, reason or None)
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
    # db.upgrade runs from the commands that WRITE, and this one mostly
    # reads, so a view changed since the last discovery run used to be
    # served as it was: n21's pay columns came back empty on the author's
    # tracker, and the page said 1 of 63 cards stated pay when 844 of 1,512
    # open postings do. Upgrade once, here, before the first page.
    db.upgrade()
    print(f"review dashboard: http://{host}:{port}  (ctrl-c to stop)")
    import threading

    from . import basemap
    threading.Thread(target=basemap.warm, name="basemap-warm", daemon=True).start()
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
