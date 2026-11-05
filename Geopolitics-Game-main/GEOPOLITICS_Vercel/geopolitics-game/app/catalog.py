"""Country catalogue + the population -> starting soldiers table.

Population data: Worldometers (UN World Population Prospects 2024 revision), 2026 column,
retrieved 2026-10-28. The raw rows live in data/worldometers_population_2026.csv and
`tools/build_country_data.py` regenerates data/countries.json from them, so the data is reproducible.
"""
import json
import pathlib

_DATA_FILE = pathlib.Path(__file__).parent / "data" / "countries.json"

# (inclusive upper bound in people, starting soldiers)
POPULATION_TABLE = [
    (5_000_000, 1), (10_000_000, 2), (25_000_000, 3), (50_000_000, 4),
    (75_000_000, 5), (100_000_000, 6), (125_000_000, 7), (150_000_000, 8),
]
MAX_SOLDIERS = 9  # more than 150 million


def soldiers_for_population(people: float) -> int:
    for limit, soldiers in POPULATION_TABLE:
        if people <= limit:
            return soldiers
    return MAX_SOLDIERS


with open(_DATA_FILE, encoding="utf-8") as fh:
    _raw = json.load(fh)

POPULATION_SOURCE = {k: _raw[k] for k in ("source", "retrieved", "url")}
COUNTRIES = [{**n, "soldiers": soldiers_for_population(n["population"])} for n in _raw["nations"]]
CATALOG = {c["code"]: c for c in COUNTRIES}
