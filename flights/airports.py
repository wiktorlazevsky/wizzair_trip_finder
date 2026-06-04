"""Candidate destination airports.

fast-flights needs an explicit destination per query (there is no real
"everywhere" search), so we scan this curated list. Edit it freely - add the
places you'd actually consider, remove ones you wouldn't. Codes are IATA.
"""

# IATA -> human label. Popular European + a few near-Europe leisure spots.
DESTINATIONS: dict[str, str] = {
    "BCN": "Barcelona",
    "MAD": "Madrid",
    "AGP": "Malaga",
    "VLC": "Valencia",
    "PMI": "Palma de Mallorca",
    "LIS": "Lisbon",
    "OPO": "Porto",
    "FCO": "Rome",
    "MXP": "Milan",
    "NAP": "Naples",
    "VCE": "Venice",
    "CTA": "Catania",
    "ATH": "Athens",
    "JTR": "Santorini",
    "HER": "Crete (Heraklion)",
    "SKG": "Thessaloniki",
    "CDG": "Paris",
    "NCE": "Nice",
    "LYS": "Lyon",
    "AMS": "Amsterdam",
    "BRU": "Brussels",
    "BER": "Berlin",
    "MUC": "Munich",
    "VIE": "Vienna",
    "ZRH": "Zurich",
    "PRG": "Prague",
    "BUD": "Budapest",
    "KRK": "Krakow",
    "WAW": "Warsaw",
    "CPH": "Copenhagen",
    "OSL": "Oslo",
    "ARN": "Stockholm",
    "HEL": "Helsinki",
    "DUB": "Dublin",
    "EDI": "Edinburgh",
    "LON": "London (all)",
    "IST": "Istanbul",
    "AYT": "Antalya",
    "SPU": "Split",
    "DBV": "Dubrovnik",
    "ZAD": "Zadar",
    "TIA": "Tirana",
    "MLA": "Malta",
    "TFS": "Tenerife",
    "LPA": "Gran Canaria",
    "FAO": "Faro (Algarve)",
    "RAK": "Marrakesh",
    "TLV": "Tel Aviv",
}

DEFAULT_DESTINATIONS = list(DESTINATIONS.keys())


def label(code: str) -> str:
    return DESTINATIONS.get(code, code)
