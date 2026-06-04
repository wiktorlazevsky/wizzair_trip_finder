"""Thin wrapper around fast-flights with our own result parser.

Two problems with stock fast-flights that we fix here:

1. Consent wall: the default request hits Google's EU cookie-consent page and
   returns no flights. We patch the fetch path to send a `SOCS` consent cookie.

2. Stale selectors: Google rotates its obfuscated CSS class names. The parser
   shipped with fast-flights only still matches the *price* node, so airline,
   times and duration come back empty. We re-parse the HTML ourselves with the
   current selectors so we can recover the airline - which we need in order to
   filter to a specific carrier (e.g. Wizz Air).

The CSS classes below WILL eventually rotate again and break parsing; when that
happens, re-inspect a live response and update `_SEL`.
"""

import re
import time
import random
from dataclasses import dataclass, asdict
from typing import Optional, Iterable

from selectolax.lexbor import LexborHTMLParser
from fast_flights.primp import Client
from fast_flights import core
from fast_flights.filter import TFSData
from fast_flights.flights_impl import FlightData, Passengers

# Pre-accepted Google consent cookie -> serves results instead of the
# "Before you continue to Google" interstitial.
_SOCS_COOKIE = "CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg"

_IMPERSONATIONS = ["chrome_126", "chrome_124", "chrome_120", "edge_122", "safari_17_2_1"]

_FLIGHTS_URL = "https://www.google.com/travel/flights"

# Current obfuscated selectors (verified against a live response). Update when
# Google rotates them.
_SEL = {
    "row_container": 'div[jsname="IWWDBc"], div[jsname="YdtKid"]',
    "row": "ul.Rk10dc li",
    "airline": "div.sSHqwe.tPgKwe.ogfYpf",
    "times": "span.mv1WYe div",
    "duration": "div.gvkrdb.AdWm1c.tPgKwe.ogfYpf",
    "stops": ".BbR8Ec .ogfYpf",
    "price": "div.YMlIz.FpEdX",
}


def _patched_fetch(params: dict):
    """Replacement for fast_flights.core.fetch that defeats the consent wall."""
    last_err: Optional[Exception] = None
    for impersonate in random.sample(_IMPERSONATIONS, len(_IMPERSONATIONS)):
        try:
            client = Client(impersonate=impersonate, verify=False)
            res = client.get(_FLIGHTS_URL, params=params, cookies={"SOCS": _SOCS_COOKIE})
            if res.status_code == 200 and "Before you continue" not in res.text:
                return res
            last_err = AssertionError(f"{res.status_code} / consent wall ({impersonate})")
        except Exception as e:
            last_err = e
        time.sleep(0.4)
    raise last_err or RuntimeError("fetch failed")


# Patch once on import so every fast-flights "common" call uses our fetch.
core.fetch = _patched_fetch


_PRICE_RE = re.compile(r"[\d]+(?:\.[\d]+)?")


def _parse_price(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    m = _PRICE_RE.search(raw.replace(",", ""))
    return float(m.group()) if m else None


@dataclass
class FlightRow:
    airline: str
    price_eur: Optional[float]
    departure: str
    arrival: str
    duration: str
    stops: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RouteQuote:
    origin: str
    dest: str
    date: str
    price_eur: Optional[float]   # cheapest matching fare in EUR, None if none
    n_flights: int               # number of matching flights
    airline: Optional[str] = None  # carrier of the cheapest matching flight

    @property
    def available(self) -> bool:
        return self.price_eur is not None

    def to_dict(self) -> dict:
        return asdict(self)


def _first_text(node, selector: str) -> str:
    found = node.css_first(selector)
    return found.text(strip=True) if found else ""


def _parse_flights(html: str) -> list[FlightRow]:
    parser = LexborHTMLParser(html)
    rows: list[FlightRow] = []
    for container in parser.css(_SEL["row_container"]):
        for item in container.css(_SEL["row"]):
            price = _parse_price(_first_text(item, _SEL["price"]))
            airline = _first_text(item, _SEL["airline"])
            if price is None and not airline:
                continue  # "view more" / spacer rows
            times = item.css(_SEL["times"])
            rows.append(
                FlightRow(
                    airline=airline,
                    price_eur=price,
                    departure=times[0].text(strip=True) if len(times) > 0 else "",
                    arrival=times[1].text(strip=True) if len(times) > 1 else "",
                    duration=_first_text(item, _SEL["duration"]),
                    stops=_first_text(item, _SEL["stops"]),
                )
            )
    return rows


def _matches_airline(row_airline: str, wanted: list[str]) -> bool:
    a = row_airline.lower()
    return any(w in a for w in wanted)


def fetch_flights(
    origin: str,
    dest: str,
    date: str,
    adults: int = 1,
    max_stops: Optional[int] = None,
    retries: int = 3,
) -> list[FlightRow]:
    """All parsed flights for origin->dest on `date`. Raises on fetch failure.

    Google occasionally serves a degraded HTML variant where prices parse but
    airline names don't. Since the airline is essential for carrier filtering,
    we retry (each fetch rotates browser impersonation) when we see priced rows
    with no airlines at all.
    """
    tfs = TFSData.from_interface(
        flight_data=[FlightData(date=date, from_airport=origin, to_airport=dest)],
        trip="one-way",
        seat="economy",
        passengers=Passengers(adults=adults),
        max_stops=max_stops,
    )
    params = {
        "tfs": tfs.as_b64().decode("utf-8"),
        "hl": "en",
        "tfu": "EgQIABABIgA",
        "curr": "EUR",
    }
    rows: list[FlightRow] = []
    for _ in range(max(1, retries)):
        res = core.fetch(params)
        rows = _parse_flights(res.text)
        priced = [r for r in rows if r.price_eur is not None]
        if not priced:
            return []  # genuinely no flights on this route/date
        if any(r.airline for r in priced):
            return rows  # good response, airlines present
        # degraded variant: prices but no airlines -> retry with a new fetch
    return rows


def quote_route(
    origin: str,
    dest: str,
    date: str,
    adults: int = 1,
    max_stops: Optional[int] = None,
    airlines: Optional[Iterable[str]] = None,
) -> RouteQuote:
    """Cheapest one-way economy fare (EUR) for origin->dest on `date`.

    If `airlines` is given (e.g. ["Wizz Air"] or ["wizz"]), only flights whose
    carrier name contains one of those strings (case-insensitive) are considered;
    the route is treated as unavailable if that carrier has no flight.

    Never raises: failures / no-match yield price_eur=None.
    """
    try:
        rows = fetch_flights(origin, dest, date, adults, max_stops)
    except Exception:
        return RouteQuote(origin, dest, date, None, 0, None)

    if airlines:
        wanted = [a.lower() for a in airlines]
        rows = [r for r in rows if _matches_airline(r.airline, wanted)]

    priced = [r for r in rows if r.price_eur is not None]
    if not priced:
        return RouteQuote(origin, dest, date, None, 0, None)

    best = min(priced, key=lambda r: r.price_eur)
    return RouteQuote(origin, dest, date, best.price_eur, len(priced), best.airline)
