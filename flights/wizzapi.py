"""Client for Wizz Air's public website API (free, no key).

Three endpoints, all unofficial but openly served by wizzair.com:

1. Version discovery: GET https://wizzair.com/static/metadata.json is an HTML
   page that embeds  "apiUrl":"https://be.wizzair.com/<VERSION>/Api" . The
   version string rotates, so we scrape it at runtime instead of hardcoding.

2. Route network: GET {api}/asset/map?languageCode=en-gb -> every Wizz city and
   the destinations it connects to. Lets us compute reachable destinations with
   pure set logic before requesting any prices.

3. Prices: POST {api}/search/timetable with a date range -> cheapest price per
   day for a route, plus that day's departure times.

Operational notes:
- Prices come back in the departure country's currency (EUR from Madrid, GBP
  from London, PLN from Warsaw...), so we normalize everything to EUR using live
  ECB rates from frankfurter.app (free, no key), with a static fallback.
- The timetable endpoint throttles aggressive callers by returning an empty
  result. We space calls out and retry empties a couple of times.
- The route map lists *marketed* routes; a route can be in the map yet have no
  bookable flights in a given window. The timetable is the source of truth for
  actual availability.
"""

import re
import json
import time
import threading
from dataclasses import dataclass, field
from typing import Optional

from fast_flights.primp import Client

_METADATA_URL = "https://wizzair.com/static/metadata.json"
_FX_URL = "https://api.frankfurter.app/latest?from=EUR"
_FX_FALLBACK = {  # 1 EUR -> X, rough static backup if frankfurter is down
    "GBP": 0.86, "PLN": 4.25, "RON": 5.05, "HUF": 395.0,
    "BGN": 1.96, "CZK": 25.0, "SEK": 11.3, "NOK": 11.7, "CHF": 0.95,
    "DKK": 7.46, "MKD": 61.5, "RSD": 117.0, "MDL": 19.5, "GEL": 3.0,
    "ALL": 99.0, "BAM": 1.96, "UAH": 45.0, "AED": 4.0, "USD": 1.08,
}
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://wizzair.com",
    "Referer": "https://wizzair.com/",
}


@dataclass
class DayPrice:
    origin: str
    dest: str
    date: str               # YYYY-MM-DD
    price_eur: Optional[float]
    price_native: float
    currency: str
    times: list[str] = field(default_factory=list)  # ["08:10", "21:35"]


_IMPERSONATE = "chrome_126"


def _new_http() -> Client:
    return Client(impersonate=_IMPERSONATE, verify=False)


class WizzClient:
    def __init__(self, min_interval: float = 0.8):
        # NB: the timetable endpoint returns HTTP 400 on the *second* request
        # made from the same client (some session state gets poisoned), so every
        # timetable call below uses a brand-new client. self._http is only used
        # for the one-shot setup calls (version, FX, route map).
        self._http = _new_http()
        self.base = self._discover_base()
        self._fx = self._load_fx()
        self._lock = threading.Lock()
        self._min_interval = min_interval
        self._last_call = 0.0

    # --- setup -----------------------------------------------------------
    def _discover_base(self) -> str:
        html = self._http.get(_METADATA_URL).text
        # Slashes in the embedded JSON are unicode-escaped: https://...
        m = re.search(r'"apiUrl":"([^"]+?Api)"', html)
        if not m:
            raise RuntimeError("Could not find Wizz apiUrl in metadata.json")
        return m.group(1).replace("\\u002F", "/").replace("\\/", "/")

    def _load_fx(self) -> dict[str, float]:
        try:
            rates = json.loads(self._http.get(_FX_URL).text)["rates"]
            rates["EUR"] = 1.0
            return rates
        except Exception:
            d = dict(_FX_FALLBACK)
            d["EUR"] = 1.0
            return d

    def _to_eur(self, amount: float, currency: str) -> Optional[float]:
        if currency == "EUR":
            return amount
        rate = self._fx.get(currency)
        return round(amount / rate, 2) if rate else None

    def _throttle(self) -> None:
        with self._lock:
            wait = self._min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()

    # --- endpoints -------------------------------------------------------
    def route_map(self) -> dict[str, dict]:
        """iata -> {"name": str, "connections": set[str]}."""
        data = json.loads(self._http.get(self.base + "/asset/map?languageCode=en-gb").text)
        out: dict[str, dict] = {}
        for city in data.get("cities", []):
            out[city["iata"]] = {
                "name": city.get("shortName", "").strip().replace("\n", " "),
                "connections": {c["iata"] for c in city.get("connections", [])},
            }
        return out

    def timetable(
        self, origin: str, dest: str, date_from: str, date_to: str,
        adults: int = 1, retries: int = 3,
    ) -> list[DayPrice]:
        """Cheapest fare per day for origin->dest within [date_from, date_to].

        Returns [] for routes with no bookable service in the window. Empty
        responses are retried (they're usually throttling, not a true empty).
        """
        body = {
            "flightList": [{
                "departureStation": origin,
                "arrivalStation": dest,
                "from": date_from,
                "to": date_to,
            }],
            "priceType": "regular",
            "adultCount": adults,
            "childCount": 0,
            "infantCount": 0,
        }
        for attempt in range(max(1, retries)):
            self._throttle()
            try:
                resp = _new_http().post(self.base + "/search/timetable",
                                        json=body, headers=_HEADERS)
            except Exception:
                time.sleep(0.8 * (attempt + 1))
                continue
            if resp.status_code != 200:
                time.sleep(0.8 * (attempt + 1))  # transient error, retry
                continue
            try:
                data = json.loads(resp.text)
            except Exception:
                time.sleep(0.8 * (attempt + 1))
                continue
            # A clean 200 with no flights means genuinely no service -> trust it.
            rows = [f for f in data.get("outboundFlights", [])
                    if f.get("departureStation") == origin]
            return [self._to_day_price(origin, dest, f) for f in rows]
        return []

    def _to_day_price(self, origin: str, dest: str, f: dict) -> DayPrice:
        amount = f["price"]["amount"]
        currency = f["price"]["currencyCode"]
        return DayPrice(
            origin=origin,
            dest=dest,
            date=f["departureDate"][:10],
            price_eur=self._to_eur(amount, currency),
            price_native=amount,
            currency=currency,
            times=[t[11:16] for t in f.get("departureDates", [])],
        )
