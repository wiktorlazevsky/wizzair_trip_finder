"""Find Wizz destinations a set of friend-groups can all reach on the same day.

Model: each friend is a *group* of candidate home airports (they drive to
whichever is cheapest). A destination/date works if every group has at least one
airport with a Wizz flight there that day; the group's cost is its cheapest such
airport. We rank (destination, date) options by the summed cost across groups.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from .wizzapi import WizzClient, DayPrice
from .cache import Cache


@dataclass
class GroupDayBest:
    airport: str          # cheapest airport in the group for this dest+date
    price_eur: float
    times: list[str]


@dataclass
class Option:
    dest: str
    dest_name: str
    date: str
    total_eur: float
    per_group: dict[str, GroupDayBest]   # group name -> its cheapest choice


def common_destinations(
    route_map: dict[str, dict], groups: dict[str, list[str]]
) -> set[str]:
    """Destinations every group can reach by Wizz from at least one airport."""
    if not groups:
        return set()
    per_group: list[set[str]] = []
    for airports in groups.values():
        reach: set[str] = set()
        for a in airports:
            reach |= route_map.get(a, {}).get("connections", set())
        per_group.append(reach)
    common = set.intersection(*per_group)
    origins = {a for airports in groups.values() for a in airports}
    return common - origins


def _cached_timetable(
    client: WizzClient, cache: Optional[Cache],
    origin: str, dest: str, date_from: str, date_to: str, adults: int,
) -> list[DayPrice]:
    if cache is not None:
        key = cache.key("tt", origin, dest, date_from, date_to, adults)
        hit = cache.get(key)
        if hit is not None:
            return [DayPrice(**row) for row in hit]
    rows = client.timetable(origin, dest, date_from, date_to, adults)
    if cache is not None:
        cache.set(cache.key("tt", origin, dest, date_from, date_to, adults),
                  [r.__dict__ for r in rows])
    return rows


def find_options(
    client: WizzClient,
    route_map: dict[str, dict],
    groups: dict[str, list[str]],
    dests: list[str],
    date_from: str,
    date_to: str,
    adults: int = 1,
    cache: Optional[Cache] = None,
    progress: Optional[Callable[[int, int, str, str, str], None]] = None,
) -> list[Option]:
    """Price every (group-airport -> dest) over the window and assemble options.

    Only airports that actually serve a dest (per the route map) are queried.
    """
    tasks: list[tuple[str, str, str]] = []  # (group, airport, dest)
    for dest in dests:
        for group, airports in groups.items():
            for a in airports:
                if dest in route_map.get(a, {}).get("connections", set()):
                    tasks.append((group, a, dest))

    # best[(group, dest, date)] = GroupDayBest
    best: dict[tuple[str, str, str], GroupDayBest] = {}
    for i, (group, airport, dest) in enumerate(tasks):
        rows = _cached_timetable(client, cache, airport, dest, date_from, date_to, adults)
        for r in rows:
            if r.price_eur is None:
                continue
            key = (group, dest, r.date)
            cur = best.get(key)
            if cur is None or r.price_eur < cur.price_eur:
                best[key] = GroupDayBest(airport, r.price_eur, r.times)
        if progress:
            progress(i + 1, len(tasks), group, airport, dest)

    if cache is not None:
        cache.save()

    group_names = list(groups.keys())
    options: list[Option] = []
    for dest in dests:
        dates_per_group = {
            g: {d: gb for (gg, dd, d), gb in best.items() if gg == g and dd == dest}
            for g in group_names
        }
        if not all(dates_per_group[g] for g in group_names):
            continue  # some group can't reach dest on any day in window
        shared_dates = set.intersection(
            *[set(dates_per_group[g].keys()) for g in group_names]
        )
        for d in shared_dates:
            per_group = {g: dates_per_group[g][d] for g in group_names}
            options.append(Option(
                dest=dest,
                dest_name=route_map.get(dest, {}).get("name", dest),
                date=d,
                total_eur=sum(x.price_eur for x in per_group.values()),
                per_group=per_group,
            ))

    options.sort(key=lambda o: (o.total_eur, o.date))
    return options
