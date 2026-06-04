"""Core logic: find destinations all origins can reach on a given date."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Optional

from .client import RouteQuote, quote_route
from .cache import Cache


@dataclass
class Match:
    dest: str
    date: str
    total_eur: float                 # sum of cheapest fares from every origin
    per_origin: dict[str, float]     # origin -> cheapest fare EUR
    airlines: dict[str, str]         # origin -> carrier of that cheapest fare


def _airlines_key(airlines: Optional[list[str]]) -> str:
    return ",".join(sorted(a.lower() for a in airlines)) if airlines else ""


def _quote_cached(
    origin: str, dest: str, date: str, adults: int, max_stops: Optional[int],
    airlines: Optional[list[str]], cache: Optional[Cache],
) -> RouteQuote:
    if cache is not None:
        key = cache.key(origin, dest, date, adults, max_stops, _airlines_key(airlines))
        hit = cache.get(key)
        if hit is not None:
            return RouteQuote(**hit)
    quote = quote_route(origin, dest, date, adults, max_stops, airlines)
    if cache is not None:
        cache.set(
            cache.key(origin, dest, date, adults, max_stops, _airlines_key(airlines)),
            quote.to_dict(),
        )
    return quote


def find_matches(
    origins: list[str],
    date: str,
    destinations: list[str],
    adults: int = 1,
    max_stops: Optional[int] = None,
    airlines: Optional[list[str]] = None,
    cache: Optional[Cache] = None,
    workers: int = 6,
    progress: Optional[Callable[[int, int, RouteQuote], None]] = None,
) -> tuple[list[Match], dict[tuple[str, str], RouteQuote]]:
    """Quote every origin x destination for one date.

    Returns (matches, all_quotes). `matches` are destinations reachable from
    ALL origins, sorted by cheapest combined price. `all_quotes` is the full
    grid keyed by (origin, dest) for inspection / partial results.

    If `airlines` is given, only those carriers count toward reachability/price.
    """
    dests = [d for d in destinations if d not in origins]
    pairs = [(o, d) for d in dests for o in origins]

    quotes: dict[tuple[str, str], RouteQuote] = {}

    def task(pair: tuple[str, str]) -> tuple[tuple[str, str], RouteQuote]:
        origin, dest = pair
        return pair, _quote_cached(origin, dest, date, adults, max_stops, airlines, cache)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(task, p) for p in pairs]
        for i, fut in enumerate(as_completed(futures)):
            pair, quote = fut.result()
            quotes[pair] = quote
            if progress:
                progress(i + 1, len(futures), quote)

    if cache is not None:
        cache.save()

    matches: list[Match] = []
    for dest in dests:
        origin_quotes = [quotes[(o, dest)] for o in origins]
        if all(q.available for q in origin_quotes):
            per_origin = {o: quotes[(o, dest)].price_eur for o in origins}
            airlines_by_origin = {o: quotes[(o, dest)].airline or "" for o in origins}
            matches.append(
                Match(
                    dest=dest,
                    date=date,
                    total_eur=sum(per_origin.values()),
                    per_origin=per_origin,
                    airlines=airlines_by_origin,
                )
            )

    matches.sort(key=lambda m: m.total_eur)
    return matches, quotes
