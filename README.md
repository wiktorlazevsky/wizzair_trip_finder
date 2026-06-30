# Wizz Air Trip Finder

Find **Wizz Air** destinations that a group of friends — each starting from
different home airports — can all fly to on the **same day**, ranked by cheapest
total price. Optionally lets one person **connect via a hub** (1 stop) to unlock
far more shared destinations.

Each friend is a *cluster* of candidate home airports (they take whichever is
cheapest). Uses Wizz Air's own free public API — no key, prices in EUR.

## Get it

```bash
git clone https://github.com/wiktorlazevsky/wizzair_trip_finder.git
cd wizzair_trip_finder
```

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Web dashboard (easiest)

```bash
.venv/bin/python dashboard.py     # open http://localhost:8000
```

- Add/remove people and search their airports by code or city (click to add).
- Pick a date range, then **Search**.
- "Connections: Up to 1 stop" lets a friend route home → hub → destination
  (same-day), expanding the common destination list a lot.
- First search is slow (it prices every route); results are cached after.

### Share with friends (free, temporary link)

Keep `dashboard.py` running, then in another terminal:

```bash
cloudflared tunnel --url http://localhost:8000
```

Send the `*.trycloudflare.com` link it prints. Works only while both your Mac
and this command stay running.

## Command line

```bash
.venv/bin/python wizz_run.py --from 2026-07-01 --to 2026-07-14
.venv/bin/python wizz_run.py --from 2026-07-01 --to 2026-07-14 --london   # swap Madrid friend for London
.venv/bin/python wizz_run.py --from 2026-07-01 --to 2026-07-14 --map-only # list destinations, no prices
```

Edit `config.json` to change the friend groups and their airports.

## Notes

- Prices are per person, one-way, normalized to EUR.
- Only airports on the Wizz network work (non-Wizz codes are flagged + ignored).
- Caches live in `.wizz_cache.json` (delete to force fresh prices).
