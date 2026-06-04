#!/usr/bin/env python3
"""CLI: find destinations 4 (or N) friends can all fly to on the same date(s).

Examples:
    python run.py --origins WAW,KRK,BER,VIE --date 2026-07-02
    python run.py --origins WAW,KRK,BER,VIE --start 2026-07-02 --end 2026-07-05 --top 10
    python run.py --origins WAW,KRK,BER,VIE --date 2026-07-02 --dests BCN,LIS,FCO,ATH
"""

import argparse
import sys
from datetime import date, timedelta

from flights.airports import DEFAULT_DESTINATIONS, label
from flights.cache import Cache
from flights.finder import find_matches


def _date_range(start: str, end: str) -> list[str]:
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    if e < s:
        raise SystemExit("--end is before --start")
    out, cur = [], s
    while cur <= e:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _progress(done: int, total: int, quote) -> None:
    bar = f"  scanning {done}/{total}  {quote.origin}->{quote.dest}"
    print(bar.ljust(60), end="\r", file=sys.stderr, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--origins", required=True,
                    help="Comma-separated origin IATA codes, e.g. WAW,KRK,BER,VIE")
    ap.add_argument("--date", help="Single travel date YYYY-MM-DD")
    ap.add_argument("--start", help="Range start YYYY-MM-DD (use with --end)")
    ap.add_argument("--end", help="Range end YYYY-MM-DD (inclusive)")
    ap.add_argument("--dests", default=None,
                    help="Comma-separated destination IATA codes (default: built-in list)")
    ap.add_argument("--adults", type=int, default=1)
    ap.add_argument("--airlines", default=None,
                    help='Only these carriers, comma-separated substrings, '
                         'e.g. "Wizz Air" or just "wizz". Omit for all airlines.')
    ap.add_argument("--max-stops", type=int, default=None,
                    help="Limit number of stops (omit for any)")
    ap.add_argument("--top", type=int, default=20, help="Show this many cheapest matches")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--cache", default=".flights_cache.json")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    origins = [o.strip().upper() for o in args.origins.split(",") if o.strip()]
    if len(origins) < 2:
        raise SystemExit("Give at least 2 origins.")

    if args.date:
        dates = [args.date]
    elif args.start:
        dates = _date_range(args.start, args.end or args.start)
    else:
        raise SystemExit("Provide --date or --start/--end.")

    destinations = (
        [d.strip().upper() for d in args.dests.split(",") if d.strip()]
        if args.dests else DEFAULT_DESTINATIONS
    )

    airlines = (
        [a.strip() for a in args.airlines.split(",") if a.strip()]
        if args.airlines else None
    )

    cache = None if args.no_cache else Cache(args.cache)

    if airlines:
        print(f"Carrier filter: {', '.join(airlines)}", file=sys.stderr)

    for d in dates:
        print(f"\n=== {d}  |  origins: {', '.join(origins)} ===", file=sys.stderr)
        matches, quotes = find_matches(
            origins, d, destinations,
            adults=args.adults, max_stops=args.max_stops, airlines=airlines,
            cache=cache, workers=args.workers, progress=_progress,
        )
        print(" " * 60, end="\r", file=sys.stderr)  # clear progress line

        reachable = len(matches)
        scanned = len({dest for _, dest in quotes})
        print(f"\n{d}: {reachable} of {scanned} destinations reachable from all "
              f"{len(origins)} origins\n")
        if not matches:
            print("  (none — try more destinations, a different date, or --max-stops)")
            continue

        header = f"{'#':>2}  {'Dest':<22} {'Total':>8}   " + "  ".join(
            f"{o:>6}" for o in origins
        )
        print(header)
        print("-" * len(header))
        for i, m in enumerate(matches[: args.top], 1):
            cells = "  ".join(f"{m.per_origin[o]:>5.0f}€" for o in origins)
            name = f"{m.dest} {label(m.dest)}"[:22]
            print(f"{i:>2}  {name:<22} {m.total_eur:>6.0f}€   {cells}")


if __name__ == "__main__":
    main()
