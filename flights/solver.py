"""1-stop "solver": let a friend connect through a hub to widen the set of
destinations the whole group can reach on the same day, and price the cheapest
routing.

Generalizes `groups_finder.find_options` from 0 stops to <=1 stop. A friend
reaches a destination D either directly (home -> D) or via one allowed hub
(home -> H -> D). "Allowed hubs" are the highest-degree Wizz airports, which
keeps the number of priced legs tractable while still capturing the real bases
(Budapest, Warsaw, Bucharest, ...).

Timing model (strict same-day): every leg of every friend departs on the one
chosen meetup day. The Wizz timetable gives departure times but not arrival
times, so connection feasibility can't be proven; we apply a coarse min-gap
filter on the *departure* times and surface both times so a human can sanity
check the layover.
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

from .wizzapi import WizzClient
from .cache import Cache
from .groups_finder import _cached_timetable


@dataclass
class Leg:
    origin: str
    dest: str
    date: str
    price_eur: float
    times: list[str] = field(default_factory=list)


@dataclass
class Route:
    legs: list[Leg]            # 1 leg (direct) or 2 legs (1 stop)
    total_eur: float
    hub: Optional[str] = None  # set when it's a 1-stop route

    @property
    def airport(self) -> str:
        """Home airport this route departs from."""
        return self.legs[0].origin


@dataclass
class SolveOption:
    dest: str
    dest_name: str
    date: str
    total_eur: float
    per_group: dict[str, Route]   # group name -> its cheapest route


def _conn(route_map: dict, code: str) -> set[str]:
    return route_map.get(code, {}).get("connections", set())


def pick_hubs(route_map: dict, n: int = 20) -> set[str]:
    """The n highest-degree airports (most onward Wizz connections)."""
    ranked = sorted(route_map,
                    key=lambda c: len(_conn(route_map, c)), reverse=True)
    return set(ranked[:max(0, n)])


def reach_1stop(route_map: dict, airports: list[str], hubs: set[str]) -> set[str]:
    """Airports reachable from any of `airports` directly or via one allowed hub."""
    reach: set[str] = set()
    for a in airports:
        direct = _conn(route_map, a)
        reach |= direct
        for h in direct & hubs:
            reach |= _conn(route_map, h)
    return reach


def common_meetups(route_map: dict, groups: dict[str, list[str]],
                   hubs: set[str]) -> set[str]:
    """Destinations every group can reach (direct or 1-stop), minus home airports."""
    if not groups:
        return set()
    per_group = [reach_1stop(route_map, airs, hubs) for airs in groups.values()]
    common = set.intersection(*per_group)
    origins = {a for airs in groups.values() for a in airs}
    return common - origins


def _has_direct(route_map: dict, homes: list[str], dest: str) -> bool:
    return any(dest in _conn(route_map, a) for a in homes)


def _to_min(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _gap_ok(hop_times: list[str], onw_times: list[str], gap: int) -> bool:
    """True if some onward flight departs >= gap minutes after some hop flight.

    Times unknown -> allow (can't disprove). Coarse: we lack arrival times.
    """
    if not hop_times or not onw_times:
        return True
    return any(_to_min(b) - _to_min(a) >= gap
               for a in hop_times for b in onw_times)


def cheapest_journeys(
    client: WizzClient,
    route_map: dict[str, dict],
    groups: dict[str, list[str]],
    dests: list[str],
    date_from: str,
    date_to: str,
    adults: int = 1,
    hubs: Optional[set[str]] = None,
    cache: Optional[Cache] = None,
    min_gap_min: int = 90,
    progress: Optional[Callable[[int, int, str, str], None]] = None,
) -> list[SolveOption]:
    """Cheapest <=1-stop, strict-same-day routing for every group to each dest."""
    if hubs is None:
        hubs = pick_hubs(route_map)
    group_names = list(groups)

    # --- decide which directed legs we actually need to price ------------
    needed: set[tuple[str, str]] = set()
    for homes in groups.values():
        for a in homes:
            for d in dests:
                if d in _conn(route_map, a):
                    needed.add((a, d))               # direct
    for g, homes in groups.items():
        for d in dests:
            if _has_direct(route_map, homes, d):
                continue                              # group fine direct, skip hubs
            for a in homes:
                for h in _conn(route_map, a) & hubs:
                    if d in _conn(route_map, h):
                        needed.add((a, h))            # hop
                        needed.add((h, d))            # onward

    # --- price them (cached), building a date-indexed table --------------
    # idx[(o, d)] = {date: (price_eur, [times])}
    idx: dict[tuple[str, str], dict[str, tuple[float, list[str]]]] = {}
    legs = sorted(needed)
    total = len(legs)
    for i, (o, d) in enumerate(legs, 1):
        rows = _cached_timetable(client, cache, o, d, date_from, date_to, adults)
        table: dict[str, tuple[float, list[str]]] = {}
        for r in rows:
            if r.price_eur is None:
                continue
            cur = table.get(r.date)
            if cur is None or r.price_eur < cur[0]:
                table[r.date] = (r.price_eur, r.times)
        idx[(o, d)] = table
        if progress:
            progress(i, total, o, d)

    if cache is not None:
        cache.save()

    # --- assemble per (dest, date), strict same-day ----------------------
    options: list[SolveOption] = []
    for d in dests:
        dname = route_map.get(d, {}).get("name", d)
        group_routes: dict[str, dict[str, Route]] = {}   # group -> date -> Route
        for g, homes in groups.items():
            byd: dict[str, Route] = {}
            for a in homes:
                # direct
                for dt, (p, times) in idx.get((a, d), {}).items():
                    r = Route(legs=[Leg(a, d, dt, p, times)], total_eur=p)
                    cur = byd.get(dt)
                    if cur is None or r.total_eur < cur.total_eur:
                        byd[dt] = r
                # 1-stop
                for h in _conn(route_map, a) & hubs:
                    if d not in _conn(route_map, h):
                        continue
                    hop = idx.get((a, h), {})
                    onw = idx.get((h, d), {})
                    for dt, (p1, t1) in hop.items():
                        if dt not in onw:
                            continue
                        p2, t2 = onw[dt]
                        if not _gap_ok(t1, t2, min_gap_min):
                            continue
                        tot = p1 + p2
                        cur = byd.get(dt)
                        if cur is None or tot < cur.total_eur:
                            byd[dt] = Route(
                                legs=[Leg(a, h, dt, p1, t1), Leg(h, d, dt, p2, t2)],
                                total_eur=tot, hub=h,
                            )
            group_routes[g] = byd

        if not all(group_routes[g] for g in group_names):
            continue
        shared = set.intersection(*[set(group_routes[g]) for g in group_names])
        for dt in shared:
            per = {g: group_routes[g][dt] for g in group_names}
            options.append(SolveOption(
                dest=d, dest_name=dname, date=dt,
                total_eur=sum(r.total_eur for r in per.values()),
                per_group=per,
            ))

    options.sort(key=lambda o: (o.total_eur, o.date))
    return options
