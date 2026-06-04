#!/usr/bin/env python3
"""Find Wizz Air destinations all friend-groups can reach on the same day.

Groups (each friend = several candidate home airports) live in config.json.

Examples:
    python wizz_run.py --from 2026-07-01 --to 2026-07-14
    python wizz_run.py --from 2026-07-01 --to 2026-07-14 --drop Madrid --top 30
    python wizz_run.py --from 2026-08-01 --to 2026-08-07 --map-only
"""

import argparse
import json
import sys

from flights.wizzapi import WizzClient
from flights.cache import Cache
from flights.groups_finder import common_destinations, find_options


def _load_config(path: str) -> tuple[dict[str, list[str]], dict]:
    with open(path) as f:
        cfg = json.load(f)
    groups = {g: [a.upper() for a in airs] for g, airs in cfg["groups"].items()}
    return groups, cfg.get("alternates", {})


def _apply_swap(groups: dict, alternates: dict, spec: str) -> None:
    """Replace group OLD with alternate NEW, in place. spec = 'OLD=NEW'."""
    if "=" not in spec:
        raise SystemExit(f"--swap needs OLD=NEW form, got '{spec}'")
    old, new = (s.strip() for s in spec.split("=", 1))
    if old not in groups:
        raise SystemExit(f"--swap: no group named '{old}'")
    alt = alternates.get(old, {}).get(new)
    if not alt:
        raise SystemExit(f"--swap: no alternate '{new}' defined for '{old}' in config")
    # preserve order: rebuild dict with old replaced by new
    rebuilt = {}
    for name, airs in groups.items():
        if name == old:
            rebuilt[new] = [a.upper() for a in alt]
        else:
            rebuilt[name] = airs
    groups.clear()
    groups.update(rebuilt)


def _progress(done: int, total: int, group: str, airport: str, dest: str) -> None:
    line = f"  pricing {done}/{total}  {group}:{airport} -> {dest}"
    print(line.ljust(56), end="\r", file=sys.stderr, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="date_from", required=True, help="Window start YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", required=True, help="Window end YYYY-MM-DD")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--adults", type=int, default=1)
    ap.add_argument("--london", action="store_true",
                    help="Swap the Madrid friend for London (shortcut for --swap Madrid=London)")
    ap.add_argument("--swap", action="append", default=[],
                    help="Swap a group for an alternate, e.g. --swap Madrid=London")
    ap.add_argument("--drop", action="append", default=[],
                    help="Exclude a group by name (repeatable), e.g. --drop Madrid")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--map-only", action="store_true",
                    help="Just list common destinations from the route map; no prices")
    ap.add_argument("--cache", default=".wizz_cache.json")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    groups, alternates = _load_config(args.config)
    swaps = list(args.swap)
    if args.london:
        swaps.append("Madrid=London")
    for spec in swaps:
        _apply_swap(groups, alternates, spec)
    for name in args.drop:
        groups.pop(name, None)
    if len(groups) < 2:
        raise SystemExit("Need at least 2 groups.")

    print("Connecting to Wizz Air API...", file=sys.stderr)
    client = WizzClient()
    route_map = client.route_map()

    # Flag any configured airport that isn't on the Wizz network.
    for g, airs in groups.items():
        bad = [a for a in airs if a not in route_map]
        if bad:
            print(f"  ! {g}: not Wizz airports, ignored: {', '.join(bad)}", file=sys.stderr)
            groups[g] = [a for a in airs if a in route_map]

    dests = sorted(common_destinations(route_map, groups))
    print(f"\nGroups: {', '.join(groups.keys())}")
    print(f"Common Wizz destinations (reachable from every group): {len(dests)}")
    for code in dests:
        print(f"  {code}  {route_map.get(code, {}).get('name', code)}")
    if not dests:
        print("\nNo destination is reachable from all groups. Try --drop on the "
              "most limited group (often the one with fewest Wizz routes).")
        return
    if args.map_only:
        return

    cache = None if args.no_cache else Cache(args.cache)
    print(f"\nPricing {args.date_from} -> {args.date_to} (same-day for everyone)...",
          file=sys.stderr)
    options = find_options(
        client, route_map, groups, dests,
        args.date_from, args.date_to, adults=args.adults,
        cache=cache, progress=_progress,
    )
    print(" " * 56, end="\r", file=sys.stderr)

    if not options:
        print("\nNo same-day option where every group has a Wizz flight in this "
              "window. Widen the dates or --drop a group.")
        return

    group_names = list(groups.keys())
    print(f"\nTop {min(args.top, len(options))} same-day meetups (cheapest total, "
          f"per person, EUR):\n")
    header = (f"{'#':>2}  {'Date':<10} {'Destination':<18} {'Total':>7}   "
              + "  ".join(f"{g[:9]:<9}" for g in group_names))
    print(header)
    print("-" * len(header))
    for i, o in enumerate(options[: args.top], 1):
        cells = "  ".join(
            f"{o.per_group[g].airport}{o.per_group[g].price_eur:>4.0f}".ljust(9)
            for g in group_names
        )
        name = f"{o.dest} {o.dest_name}"[:18]
        print(f"{i:>2}  {o.date:<10} {name:<18} {o.total_eur:>6.0f}€   {cells}")

    print(f"\n(cells show cheapest airport+price per group; e.g. 'WAW 46' = "
          f"from WAW for 46 EUR. Prices are per person, one-way.)")


if __name__ == "__main__":
    main()
