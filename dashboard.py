#!/usr/bin/env python3
"""Tiny zero-dependency web dashboard for the Wizz same-day meetup finder.

Run:  python dashboard.py        # then open http://localhost:8000

People (clusters) and their candidate airports are edited live in the browser;
config.json only seeds the initial set. Reuses the same WizzClient + finder as
wizz_run.py. The Wizz client (version discovery, FX, route map) is built once on
first request and kept warm; prices are disk-cached, so repeats are near-instant.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import sys

from flights.wizzapi import WizzClient
from flights.cache import Cache
from flights.groups_finder import common_destinations, find_options
from flights.solver import pick_hubs, common_meetups, cheapest_journeys

CONFIG = "config.json"
CACHE_FILE = ".wizz_cache.json"
PORT = 8000


# --- shared Wizz state, built once, guarded by a lock --------------------
_lock = threading.Lock()
_state = {"client": None, "route_map": None}


def _get_client() -> tuple[WizzClient, dict]:
    with _lock:
        if _state["client"] is None:
            client = WizzClient()
            _state["client"] = client
            _state["route_map"] = client.route_map()
        return _state["client"], _state["route_map"]


def _seed_groups() -> dict[str, list[str]]:
    with open(CONFIG) as f:
        cfg = json.load(f)
    return {g: [a.upper() for a in airs] for g, airs in cfg["groups"].items()}


def _all_airports(route_map: dict) -> list[dict]:
    out = [{"code": code, "name": info.get("name", "") or code}
           for code, info in route_map.items()]
    out.sort(key=lambda a: a["code"])
    return out


def _progress(done: int, total: int, origin: str, dest: str) -> None:
    line = f"  pricing {done}/{total}  {origin} -> {dest}"
    print(line.ljust(56), end="\r", file=sys.stderr, flush=True)


def _search(groups: dict[str, list[str]], date_from: str, date_to: str,
            adults: int, max_stops: int = 0, hub_count: int = 20) -> dict:
    groups = {g: [a.upper() for a in airs] for g, airs in groups.items() if airs}
    if len(groups) < 2:
        return {"error": "Need at least 2 people, each with at least one airport."}

    client, route_map = _get_client()
    warnings = []
    for g, airs in list(groups.items()):
        bad = [a for a in airs if a not in route_map]
        if bad:
            warnings.append(f"{g}: not Wizz airports, ignored: {', '.join(bad)}")
            groups[g] = [a for a in airs if a in route_map]
    groups = {g: airs for g, airs in groups.items() if airs}
    if len(groups) < 2:
        return {"error": "Fewer than 2 people have a valid Wizz airport.",
                "warnings": warnings}

    group_names = list(groups.keys())
    direct = sorted(common_destinations(route_map, groups))
    if max_stops >= 1:
        hubs = pick_hubs(route_map, hub_count)
        dests = sorted(common_meetups(route_map, groups, hubs))
    else:
        hubs = set()
        dests = direct

    result = {
        "groups": group_names,
        "warnings": warnings,
        "max_stops": max_stops,
        "direct_count": len(direct),
        "common": [{"code": c, "name": route_map.get(c, {}).get("name", c)}
                   for c in dests],
        "options": [],
    }
    if not dests:
        return result

    cache = Cache(CACHE_FILE)
    if max_stops >= 1:
        options = cheapest_journeys(
            client, route_map, groups, dests, date_from, date_to,
            adults=adults, hubs=hubs, cache=cache, progress=_progress)
        print(" " * 56, end="\r", file=sys.stderr)
        for o in options:
            result["options"].append({
                "date": o.date, "dest": o.dest, "dest_name": o.dest_name,
                "total": round(o.total_eur),
                "legs": {g: _route_json(o.per_group[g]) for g in group_names},
            })
    else:
        options = find_options(client, route_map, groups, dests,
                               date_from, date_to, adults=adults, cache=cache)
        for o in options:
            gb = o.per_group
            result["options"].append({
                "date": o.date, "dest": o.dest, "dest_name": o.dest_name,
                "total": round(o.total_eur),
                "legs": {
                    g: {
                        "total": round(gb[g].price_eur),
                        "hub": None,
                        "legs": [{
                            "from": gb[g].airport, "to": o.dest,
                            "price": round(gb[g].price_eur), "times": gb[g].times,
                        }],
                    }
                    for g in group_names
                },
            })
    return result


def _route_json(r) -> dict:
    return {
        "total": round(r.total_eur),
        "hub": r.hub,
        "legs": [{
            "from": leg.origin, "to": leg.dest,
            "price": round(leg.price_eur), "times": leg.times,
        } for leg in r.legs],
    }


# --- HTML page -----------------------------------------------------------
PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wizz Meetup Finder</title>
<style>
  :root { --pink:#c6007e; --ink:#1a1a2e; --bg:#f4f4f8; --line:#e3e3ee; }
  * { box-sizing:border-box; }
  body { margin:0; font:15px/1.5 system-ui,sans-serif; color:var(--ink); background:var(--bg); }
  header { background:var(--pink); color:#fff; padding:18px 24px; }
  header h1 { margin:0; font-size:20px; }
  header p { margin:4px 0 0; opacity:.9; font-size:13px; }
  main { max-width:960px; margin:0 auto; padding:24px; }
  .card { background:#fff; border:1px solid var(--line); border-radius:12px;
          padding:16px; margin-top:20px; }
  .card h2 { margin:0 0 12px; font-size:15px; }
  .row { display:flex; flex-wrap:wrap; gap:16px; align-items:flex-end; }
  label.fld { display:flex; flex-direction:column; font-size:12px; font-weight:600;
          color:#555; gap:4px; }
  input[type=date], input[type=number], input[type=text] { font:inherit;
          padding:7px 9px; border:1px solid var(--line); border-radius:8px; }
  button { font:inherit; font-weight:600; border:0; border-radius:8px;
           cursor:pointer; }
  button.primary { background:var(--pink); color:#fff; padding:10px 20px; }
  button.ghost { background:#f0e6f0; color:var(--pink); padding:6px 12px; }
  button:disabled { opacity:.5; cursor:default; }

  .person { border:1px solid var(--line); border-radius:10px; padding:12px;
            margin-bottom:12px; background:#fafaff; }
  .person-head { display:flex; align-items:center; gap:8px; margin-bottom:10px; }
  .person-head input { font-weight:700; font-size:15px; border:1px solid transparent;
            background:transparent; padding:4px 6px; border-radius:6px; flex:0 0 auto;
            width:180px; }
  .person-head input:focus { border-color:var(--line); background:#fff; outline:none; }
  .person-head .del { margin-left:auto; background:none; color:#b00; font-size:13px;
            padding:4px 8px; }
  .chips { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px; min-height:8px; }
  .chip { background:#f0e6f0; color:var(--pink); border-radius:999px; padding:3px 8px 3px 10px;
          font-size:12px; font-weight:600; display:inline-flex; align-items:center; gap:6px; }
  .chip b { font-weight:700; }
  .chip .x { cursor:pointer; font-weight:700; opacity:.6; }
  .chip .x:hover { opacity:1; }
  .picker { position:relative; max-width:340px; }
  .picker input { width:100%; }
  .menu { position:absolute; z-index:10; left:0; right:0; top:100%; margin-top:4px;
          background:#fff; border:1px solid var(--line); border-radius:8px;
          max-height:240px; overflow:auto; box-shadow:0 8px 24px rgba(0,0,0,.1);
          display:none; }
  .menu.open { display:block; }
  .menu div { padding:7px 10px; cursor:pointer; font-size:13px; }
  .menu div:hover, .menu div.active { background:#f0e6f0; }
  .menu .code { font-weight:700; color:var(--pink); }
  .menu .muted { color:#888; }

  .pills { display:flex; flex-wrap:wrap; gap:6px; }
  .pill { background:#f0e6f0; color:var(--pink); border-radius:999px; padding:3px 10px;
          font-size:12px; font-weight:600; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:8px 6px; border-bottom:1px solid var(--line); }
  th { font-size:11px; text-transform:uppercase; letter-spacing:.04em; color:#888; }
  td.total { font-weight:700; color:var(--pink); white-space:nowrap; }
  .leg { white-space:nowrap; }
  .leg b { font-weight:700; }
  .muted { color:#888; }
  .warn { color:#b00; font-size:13px; }
</style>
</head>
<body>
<header>
  <h1>Wizz Air Meetup Finder</h1>
  <p>Build your group, then find same-day destinations everyone can fly to (EUR, per person, one-way).</p>
</header>
<main>
  <div class="card">
    <h2>People &amp; their airports</h2>
    <div id="people"></div>
    <button class="ghost" id="addPerson" type="button">+ Add person</button>
  </div>

  <div class="card">
    <h2>When</h2>
    <div class="row">
      <label class="fld">From <input type="date" id="from" required></label>
      <label class="fld">To <input type="date" id="to" required></label>
      <label class="fld">Adults <input type="number" id="adults" value="1" min="1" max="9" style="width:64px"></label>
      <label class="fld">Connections
        <select id="stops" style="font:inherit;padding:7px 9px;border:1px solid var(--line);border-radius:8px">
          <option value="0">Direct only</option>
          <option value="1">Up to 1 stop</option>
        </select>
      </label>
      <label class="fld" id="hubsFld" style="display:none">Hubs
        <input type="number" id="hubs" value="20" min="5" max="60" style="width:64px">
      </label>
      <button class="primary" id="go" type="button">Search</button>
    </div>
    <p class="muted" id="stopsNote" style="margin:10px 0 0;display:none">
      1-stop lets a person connect via a hub (e.g. MAD&rarr;BUD&rarr;dest) &mdash; far more shared
      destinations, but the first run prices many routes and can take several minutes.
      Same-day only; we show both departure times so you can confirm the layover.
    </p>
  </div>

  <div id="out"></div>
</main>
<script>
const $ = s => document.querySelector(s);
let AIRPORTS = [];            // [{code,name}]
let AMAP = {};               // code -> name
let people = [];             // [{name, airports:[code]}]

// default dates: next month, one-week window
const d0 = new Date(); d0.setDate(d0.getDate()+30);
const d1 = new Date(d0); d1.setDate(d1.getDate()+7);
const iso = d => d.toISOString().slice(0,10);
$("#from").value = iso(d0); $("#to").value = iso(d1);

init();
async function init() {
  // Load config (people) and airports independently so one failing doesn't
  // blank the page.
  try {
    const cfg = await fetch("/api/config").then(r=>r.json());
    people = Object.entries((cfg && cfg.groups) || {}).map(([name, airports]) => ({name, airports}));
  } catch (e) { people = []; }
  if (!people.length) people = [{name:"Friend 1", airports:[]}, {name:"Friend 2", airports:[]}];
  renderPeople();

  try {
    const aps = await fetch("/api/airports").then(r=>r.json());
    if (Array.isArray(aps)) {
      AIRPORTS = aps;
      AMAP = Object.fromEntries(aps.map(a=>[a.code, a.name]));
    } else {
      throw new Error((aps && aps.error) || "bad airport list");
    }
  } catch (e) {
    $("#out").innerHTML = '<div class="card warn">Could not load the airport '+
      'list from Wizz ('+e.message+'). You can still type codes, but search '+
      'suggestions are off. Try reloading in a moment.</div>';
  }
}

$("#addPerson").addEventListener("click", () => {
  people.push({name: "Friend " + (people.length+1), airports: []});
  renderPeople();
});

function renderPeople() {
  const root = $("#people");
  root.innerHTML = "";
  people.forEach((p, pi) => root.appendChild(personEl(p, pi)));
}

function personEl(p, pi) {
  const wrap = document.createElement("div");
  wrap.className = "person";

  const head = document.createElement("div");
  head.className = "person-head";
  const name = document.createElement("input");
  name.value = p.name;
  name.addEventListener("input", () => { p.name = name.value; });
  const del = document.createElement("button");
  del.className = "del"; del.type = "button"; del.textContent = "Remove";
  del.addEventListener("click", () => { people.splice(pi,1); renderPeople(); });
  head.appendChild(name); head.appendChild(del);
  wrap.appendChild(head);

  const chips = document.createElement("div");
  chips.className = "chips";
  p.airports.forEach((code, ci) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.innerHTML = '<b>'+code+'</b> '+(AMAP[code]||"")+' <span class="x">&times;</span>';
    chip.querySelector(".x").addEventListener("click", () => {
      p.airports.splice(ci,1); renderPeople();
    });
    chips.appendChild(chip);
  });
  wrap.appendChild(chips);

  wrap.appendChild(pickerEl(p));
  return wrap;
}

function pickerEl(p) {
  const box = document.createElement("div");
  box.className = "picker";
  const inp = document.createElement("input");
  inp.type = "text";
  inp.placeholder = "Search airport (code or city)…";
  const menu = document.createElement("div");
  menu.className = "menu";
  box.appendChild(inp); box.appendChild(menu);

  let active = -1, matches = [];
  const close = () => { menu.classList.remove("open"); active = -1; };
  const add = code => {
    if (!p.airports.includes(code)) p.airports.push(code);
    renderPeople();
  };

  function refresh() {
    const q = inp.value.trim().toUpperCase();
    if (!q) { close(); return; }
    matches = AIRPORTS.filter(a =>
      !p.airports.includes(a.code) &&
      (a.code.includes(q) || a.name.toUpperCase().includes(q))
    ).slice(0, 30);
    if (!matches.length) { close(); return; }
    menu.innerHTML = matches.map((a,i) =>
      '<div data-i="'+i+'" class="'+(i===active?"active":"")+'">'+
      '<span class="code">'+a.code+'</span> <span class="muted">'+a.name+'</span></div>'
    ).join("");
    menu.querySelectorAll("div").forEach(d => {
      d.addEventListener("mousedown", e => { e.preventDefault(); add(matches[+d.dataset.i].code); });
    });
    menu.classList.add("open");
  }

  const addTyped = () => {
    const code = inp.value.trim().toUpperCase();
    if (code.length >= 3) { add(code); }   // server validates against Wizz network
  };
  inp.addEventListener("input", () => { active = -1; refresh(); });
  inp.addEventListener("focus", refresh);
  inp.addEventListener("blur", () => setTimeout(close, 120));
  inp.addEventListener("keydown", e => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (menu.classList.contains("open") && active >= 0) add(matches[active].code);
      else addTyped();                      // works even if suggestions are off
      return;
    }
    if (!menu.classList.contains("open")) return;
    if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active+1, matches.length-1); refresh(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active-1, 0); refresh(); }
    else if (e.key === "Escape") { close(); }
  });
  return box;
}

// --- stops toggle UI ---
const stopsSel = $("#stops");
function syncStops() {
  const on = stopsSel.value === "1";
  $("#hubsFld").style.display = on ? "" : "none";
  $("#stopsNote").style.display = on ? "" : "none";
}
stopsSel.addEventListener("change", syncStops);
syncStops();

// --- search ---
const out = $("#out");
$("#go").addEventListener("click", async () => {
  const go = $("#go");
  const groups = {};
  for (const p of people) {
    const nm = (p.name||"").trim() || "Friend";
    if (p.airports.length) groups[nm] = p.airports;
  }
  if (Object.keys(groups).length < 2) {
    out.innerHTML = '<div class="card warn">Add at least 2 people, each with an airport.</div>';
    return;
  }
  const stops = +stopsSel.value;
  go.disabled = true; go.textContent = "Searching…";
  const wait = stops >= 1
    ? "Pricing direct + connecting routes… first run can take several minutes (cached after)."
    : "Pricing every route over the window… first run can take ~30s.";
  out.innerHTML = '<div class="card muted">'+wait+'</div>';
  try {
    const r = await fetch("/api/search", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({
        from: $("#from").value, to: $("#to").value,
        adults: +$("#adults").value || 1, groups,
        max_stops: stops, hub_count: +$("#hubs").value || 20,
      }),
    });
    render(await r.json());
  } catch (err) {
    out.innerHTML = '<div class="card warn">Request failed: ' + err + '</div>';
  } finally {
    go.disabled = false; go.textContent = "Search";
  }
});

function legCell(cell) {
  // cell = {total, hub, legs:[{from,to,price,times}]}
  const t = arr => (arr && arr.length) ? ' <span class="muted">'+arr.join(" ")+'</span>' : '';
  if (!cell.hub) {
    const l = cell.legs[0];
    return '<b>'+l.from+'</b> '+l.price+'€'+t(l.times);
  }
  const parts = cell.legs.map(l =>
    '<b>'+l.from+'&rarr;'+l.to+'</b> '+l.price+'€'+t(l.times));
  return parts.join(' <span class="muted">/</span> ') +
         ' <span class="muted">= '+cell.total+'€ via '+cell.hub+'</span>';
}

function render(d) {
  if (d.error) {
    out.innerHTML = '<div class="card warn">'+d.error+'</div>';
    return;
  }
  let html = "";
  if (d.warnings && d.warnings.length)
    html += '<div class="card"><div class="warn">'+d.warnings.join("<br>")+'</div></div>';

  const heading = d.max_stops >= 1
    ? 'Common destinations with 1 stop ('+d.common.length+')'
    : 'Common Wizz destinations ('+d.common.length+')';
  html += '<div class="card"><h2>'+heading+'</h2>';
  if (d.max_stops >= 1)
    html += '<p class="muted" style="margin-top:-4px">'+d.direct_count+
            ' reachable directly &rarr; '+d.common.length+' once one person may connect via a hub.</p>';
  if (d.common.length)
    html += '<div class="pills">'+d.common.map(c=>'<span class="pill">'+c.code+' '+(c.name||"")+'</span>').join("")+'</div>';
  else
    html += '<p class="muted">No destination reachable by all people. Try removing the most limited person, or give them more airports.</p>';
  html += '</div>';

  if (d.options && d.options.length) {
    html += '<div class="card"><h2>Same-day meetups ('+d.options.length+'), cheapest first</h2>';
    html += '<table><thead><tr><th>Date</th><th>Destination</th><th>Total</th>';
    html += d.groups.map(g=>'<th>'+g+'</th>').join("");
    html += '</tr></thead><tbody>';
    for (const o of d.options) {
      html += '<tr><td>'+o.date+'</td><td><b>'+o.dest+'</b> '+(o.dest_name||"")+'</td>';
      html += '<td class="total">'+o.total+'€</td>';
      for (const g of d.groups) {
        html += '<td class="leg">'+legCell(o.legs[g])+'</td>';
      }
      html += '</tr>';
    }
    html += '</tbody></table></div>';
  } else if (d.common.length) {
    html += '<div class="card muted">No day in this window has a Wizz flight for every person. Widen the dates.</div>';
  }
  out.innerHTML = html;
}
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quieter console
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif path == "/api/config":
            try:
                self._json(200, {"groups": _seed_groups()})
            except Exception as e:
                self._json(200, {"groups": {}, "error": str(e)})
        elif path == "/api/airports":
            try:
                _, route_map = _get_client()
                self._json(200, _all_airports(route_map))
            except Exception as e:
                self._json(500, {"error": str(e)})
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/search":
            self._send(404, b"not found", "text/plain")
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
            data = _search(
                groups=payload.get("groups", {}),
                date_from=payload.get("from", ""),
                date_to=payload.get("to", ""),
                adults=int(payload.get("adults", 1) or 1),
                max_stops=int(payload.get("max_stops", 0) or 0),
                hub_count=int(payload.get("hub_count", 20) or 20),
            )
        except Exception as e:
            data = {"error": str(e)}
        self._json(200, data)


def main() -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Dashboard: http://localhost:{PORT}  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
