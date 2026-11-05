"""Build app/data/countries.json (selectable nations) and public/static/data/world.json (map paths).

Inputs : app/data/worldometers_population_2026.csv  (population source, UN WPP 2024 via Worldometers)
         world.json produced by tools/gen_map.mjs   (Natural Earth 110m geometry, projected)
Usage  : python tools/build_country_data.py path/to/raw_world.json
"""
import csv, json, sys, pathlib
import pycountry

root = pathlib.Path(__file__).resolve().parent.parent
raw = json.load(open(sys.argv[1]))
pops = {r["country"]: int(r["population"]) for r in csv.DictReader(open(root / "app/data/worldometers_population_2026.csv", encoding="utf-8"))}
ALIAS = {
    "Russian Federation": "Russia", "Viet Nam": "Vietnam", "Iran, Islamic Republic of": "Iran", "Türkiye": "Turkey",
    "Tanzania, United Republic of": "Tanzania", "Korea, Republic of": "South Korea",
    "Korea, Democratic People's Republic of": "North Korea", "Syrian Arab Republic": "Syria",
    "Lao People's Democratic Republic": "Laos", "Bolivia, Plurinational State of": "Bolivia",
    "Venezuela, Bolivarian Republic of": "Venezuela", "Congo, The Democratic Republic of the": "DR Congo",
    "Czechia": "Czech Republic (Czechia)", "Moldova, Republic of": "Moldova", "Côte d'Ivoire": "Côte d'Ivoire",
}
EXCLUDE = {"TW", "PS", "EH", "XK"}  # disputed territories are drawn on the map but are not selectable nations
world = {"width": raw["width"], "height": raw["height"], "sphere": raw["sphere"], "countries": {}}
nations, unmatched = [], []
for num, f in raw["countries"].items():
    try:
        c = pycountry.countries.get(numeric=num)
    except Exception:
        c = None
    if not c:
        continue
    a2 = c.alpha_2
    world["countries"][a2] = {"d": f["d"], "c": f["c"]}
    if a2 in EXCLUDE:
        continue
    name = getattr(c, "common_name", None) or c.name
    key = ALIAS.get(c.name, ALIAS.get(name, name))
    if key not in pops:
        unmatched.append((a2, c.name)); continue
    disp = {"Czech Republic (Czechia)": "Czechia", "Congo": "Republic of the Congo"}.get(key, key)
    flag = "".join(chr(0x1F1E6 + ord(ch) - 65) for ch in a2)
    nations.append({"code": a2, "name": disp, "population": pops[key], "flag": flag})
nations.sort(key=lambda n: n["name"])
json.dump({"source": "Worldometers (UN World Population Prospects 2024 Revision), 2026 column", "retrieved": "2026-10-28",
           "url": "https://www.worldometers.info/world-population/population-by-country/", "nations": nations},
          open(root / "app/data/countries.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
(root / "public/static/data").mkdir(parents=True, exist_ok=True)
json.dump(world, open(root / "public/static/data/world.json", "w"), separators=(",", ":"))
print(len(nations), "selectable nations;", len(world["countries"]), "map shapes; unmatched:", unmatched)
