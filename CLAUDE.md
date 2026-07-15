# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
pip install -r requirements.txt
# Also requires osmium CLI (used to bbox-crop large PBF extracts):
brew install osmium-tool   # macOS
apt-get install osmium-tool  # Linux
```

## Commands

```bash
# Run all tests
pytest tests/ -v

# Run a single test file
pytest tests/test_classification.py -v

# Amsterdam vs. official Fietsnetwerk
python scripts/compare_amsterdam_vs_official.py \
    --official-path data/geojson_lnglat.geojson \
    --pbf-path data/netherlands-latest.osm.pbf

# Barcelona vs. official (AMB)
python scripts/compare_barcelona_vs_official.py \
    --official-path data/xarxa_pedalable_barcelona.geojson \
    --pbf-path data/cataluna-latest.osm.pbf

# BikeNEAT comparison (downloads its own PBF)
python scripts/compare_vs_bikeneat.py --city freiburg
python scripts/compare_vs_bikeneat.py --city barcelona

# Country-level screening (resumable — safe to Ctrl-C and re-run)
python scripts/country_analysis.py --country spain --regions Cataluña
```

## Git workflow

- Repo lives at `github.com/OurCityInData/cycling-analysis` (org-owned, not a personal account). `main` is branch-protected: no direct pushes, PRs required.
- Work on a branch (`fix/...`, `feat/...`, `chore/...`), commit locally in small coherent chunks with clean messages that explain *why* not *what*, then push and open a PR when the change is ready — pushing after every single local commit isn't necessary, push/PR when it's coherent enough to review or you want it backed up remotely.
- Update this file (`CLAUDE.md`) whenever architecture, taxonomies, gotchas, or known gaps change — in the same commit/PR as the code change, not after. This file is the source of truth for future sessions; let it drift and it actively misleads.

## Architecture

### Classification layer (most important)

There are **three independent road taxonomies** — do not try to merge them:

1. **`classify_cycling_path_amsterdam()`** (`classification.py`) — NL06 taxonomy: no speed-based categories, has a "Bicycle on the road" / "Moped/Bicycle path" split, and treats `highway=path` with a Layer-3 cycling default (legal under NL law without an explicit `bicycle` tag).

2. **`classify_cycling_path_bcn()`** (`classification.py`) — Barcelona/AMB taxonomy: NO Layer-3 path default (illegal under Spanish/Catalan law), but adds maxspeed-driven categories ("Calmed zone at 10 km/h", "Shared street at 30 km/h") that don't exist in Amsterdam's scheme.

3. **`classify_area_road()`** (`cycling_analysis/area_classification.py`) — single, area-adjustable taxonomy for the country-level screening pipeline. Replaced the old un-parameterized `classify_road()` in `country.py` (deleted). Unlike #1/#2, this one IS a single function driven by a `CyclingLegalConfig` dataclass (`path_default_legal`, `layer3_maxspeed_threshold`, `sign_code_rules`, `legal_evidence_sign_codes`) looked up per country/municipality via `get_cycling_config()` — that's a deliberate, different design choice from #1/#2 (see below), not an oversight. Output categories are a **fixed 11-item list** (`CATEGORIES` in that module) with exact strings/order that must not change casually: `Greenway, Two-way side bike lane, One-way side bike lane, Bike lane on sidewalk, Bus-bike lane, Pacified zone at 10 km/h, Pacified street at 20 km/h, Shared street at 30 km/h, Service road, 2-1 road, Mixed traffic road`. Greenway detection is **tag-only** (no spatial/polygon join, by deliberate choice — lower recall than a polygon-based approach, but tags-only was explicitly preferred): unpaved surface/tracktype, `railway=abandoned`, OR a physical-separation signal (`separation`/`buffer` tags indicating ≥1m real gap from the road — grass verge, hedge, ditch, guard rail; paint/kerb/bollard-only separation does NOT count). Note: `path_default_legal` (the NL06-derived "Layer 3" blanket default) is currently `False` for every country in this taxonomy — see `CyclingLegalConfig.path_default_legal`'s docstring for why the "Dutch law" citation didn't hold up and was dropped rather than kept mislabeled; it remains a real extension point, just unpopulated. The step-1 path-legality gate (excludes an untagged `highway=path` when `path_default_legal` is False) has two narrower exceptions instead: `_has_unpaved_or_abandoned_evidence()` (independent physical evidence — railway=abandoned, unpaved surface/tracktype — is proof enough for Greenway purposes even without general cycling-legality proof; measured against `data/cataluna-latest.osm.pbf`, 2026-07: of 106,007 untagged `highway=path` ways in Catalonia, 14,813 carry this evidence and are now correctly classified as `Greenway` instead of discarded), and `legal_evidence_sign_codes` (see below — currently NL's only active rescue for an untagged path/track).

**`has_cycling_infrastructure()`** IS a single shared function (identical NL06 filter used by Amsterdam and both BikeNEAT comparisons). **`has_cycling_infrastructure_bcn()`** is a separate function — it adds Barcelona's 30 km/h city-wide network rule and removes the NL Layer-3 path default.

The classification.py module docstring explains in detail why the two city classifiers (#1/#2) cannot be safely collapsed into one config-driven function — that finding does NOT apply to `classify_area_road()`: `classify_road()` was already a single un-parameterized function applied identically across 5 countries, so making it config-driven was a genuine improvement, not a repeat of the rejected #1/#2 experiment. Two country-specific legal rules are currently active in this repo (a third, NL's `path_default_legal` "Layer 3" blanket default, was researched and dropped — see `CyclingLegalConfig`'s docstring — rather than kept under an uncited "Dutch law" label):
- **Spain**: `layer3_maxspeed_threshold=30` nationwide, per RD 970/2020 (Real Decreto 970/2020, BOE-A-2020-13969, in force since 2021-05-11) — any way with a signed `maxspeed <= 30` counts as legal cycling infrastructure even without an explicit `bicycle` tag. This was originally modeled as a Barcelona-only municipal override; `REGION_CYCLING_CONFIG_OVERRIDES` is now empty (kept as an extension point) since RD 970/2020 is a national rule covering the same threshold everywhere.
- **Netherlands**: `legal_evidence_sign_codes=('nl:g11', 'nl:g12a', 'nl:g13')` — RVV 1990 (Reglement verkeersregels en verkeerstekens) G11/G12a/G13 bicycle-path signs, verified against `data/netherlands-latest.osm.pbf` (2026-07: ~194k ways carry a `traffic_sign` tag, ~81% one of these three codes) and treated as legal-cycling evidence equivalent to `bicycle=yes`, NOT a hard category jump — see `CyclingLegalConfig`'s docstring for why G13 (used on ~20k ordinary Dutch paths) specifically isn't force-mapped to `'Greenway'`.

Germany/Belgium/Denmark have no documented special rule — don't invent one without a source.

### Data flow

Each script:
1. Loads OSM edges from a `.pbf` file via `pyrosm` (or downloads one via `io.py` helpers)
2. Runs the appropriate `has_cycling_infrastructure*()` filter
3. Classifies each passing edge with the city's `classify_cycling_path_*()` function
4. Computes km totals / spatial overlap against the official reference GeoJSON
5. Writes CSVs and plots to `output/`

The `geometry.py` module (`fix_geometry`, `clean_isolated_edges`, `remove_small_components`) is applied after loading to clean topology before comparison.

Country-level analysis (`country.py`) uses GADM boundary files (`data/gadm41_*.geojson`) to clip OSM data municipality by municipality. It writes checkpoint CSVs to the working directory so long runs can be interrupted and resumed — **checkpoint schema is versioned by column set**: `run_country_analysis()` raises loudly on load if an existing `{run_name}_checkpoint.csv`'s columns don't match the current `CATEGORIES`/`AMENITY_METRIC_COLUMNS`/area schema, rather than silently producing a corrupted mixed-schema table. Delete/rename old checkpoint + `*_cycling_by_municipality.csv` files after a schema change.

Each municipality row also gets bike-amenity counts (`cycling_analysis/bike_amenities.py`, via `pyrosm.OSM.get_pois()` on the same bbox-clipped extract used for roads) and `Municipality area (km²)` (GADM polygon reprojected to UTM). `run_country_analysis()` also writes a rendered PNG table (`save_table_png()` in `country.py`, via matplotlib's Agg canvas directly — no pyplot/global backend state) to `output/{run_name}_cycling_by_municipality.png` alongside the CSV.

### Key files

- `cycling_analysis/constants.py` — `PLAIN_HIGHWAY`, `CYCLEWAY_USEFUL_VALUES`, `CYCLEWAY_LANE_VALUES`, `OSM_KEYS` (shared across all city modules)
- `cycling_analysis/area_classification.py` — `classify_area_road()`, `CyclingLegalConfig`, `CATEGORIES` (the country-level screening taxonomy — see Classification layer above)
- `cycling_analysis/bike_amenities.py` — `compute_bike_amenity_stats()`: bicycle parking (spots/capacity/covered) plus separate `Bike shops` / `Bike rental stations` / `Bike repair stations` counts, from OSM POI tags
- `cycling_analysis/bikeneat.py` — BikeNEAT predicate engine (shared by Barcelona and Freiburg); its German StVO traffic-sign logic was deliberately NOT ported into `classify_area_road()` — `CyclingLegalConfig.sign_code_rules` is an empty opt-in extension point for that, per-country, once someone verifies sign-tagging density. Not to be confused with `legal_evidence_sign_codes` (populated for the Netherlands): `sign_code_rules` does a hard category jump, `legal_evidence_sign_codes` is soft legal-cycling evidence folded into the normal cascade.
- `cycling_analysis/cities/amsterdam.py` and `cities/barcelona.py` — city-specific metrics and plotting (intentionally separate interfaces)
- `tests/test_classification.py` — 11 ground-truthed real-world OSM ways for the Barcelona classifier; 3 marked `xfail` (all `highway=cycleway` cases with no distinguishing signal for rural/off-road greenways)
- `tests/test_area_classification.py` — the original 11 real OSM ways from the Barcelona classifier's ground truth (tags/urls copied verbatim), plus 5 more Barcelona ways, 2 real Amsterdam ways (Han Lammersbrug, Polonceau-kade — both `highway=footway` + `bicycle=yes`), and 2 real NL ways exercising `legal_evidence_sign_codes` (Noordelijk Slingepad — `highway=track` + `NL:G11`, no `bicycle` tag; Stormzwaluw — `highway=footway` + `NL:G11`, no `bicycle` tag; both fall through to `None` without the sign-evidence field) added later, run through `classify_area_road()`; 17/20 correct, 3 xfail: "Rijwielpad Noordvoort" (ground truth per domain owner: Greenway — `highway=cycleway` with `NL:G13`, no other tag-only Greenway signal; `legal_evidence_sign_codes` deliberately does NOT force G13 to `'Greenway'`, since ~20k other NL ways carry G13 and most are ordinary paved paths, not greenways, so it falls through to `'Two-way side bike lane'` via the plain step-3 rule instead — a known, accepted gap, not a bug to "fix" by force-mapping the sign), "Shared greenway near Rimini" (tag-only Greenway gap, see Known gaps below), and "Avinguda Diagonal" (no `oneway` tag at all, so the two-way default fires instead of one-way). "Carrer de Sants" was previously xfail here too (mislabeled ground truth expecting "Shared street at 30 km/h") but is now a pass at "Bus-bike lane" — `cycleway:both=share_busway` is a genuine bus-bike shared lane, and `classify_area_road()`'s step-6 match was correct all along
- `tests/test_bike_amenities.py` — unit tests for `compute_bike_amenity_stats()`
- `tests/test_municipality_area.py` — validates `country.py`'s `Municipality area (km²)` (GADM polygon reprojected to UTM) against real-world reference figures for the 13 Zeeland municipalities, sourced from Wikipedia/CBS. Skips (doesn't fail) when `data/gadm41_NLD_2.geojson` isn't cached locally, so it's a no-op in CI. **Important finding**: GADM's polygon tracks LAND area, not the "total area" (land+water) figure that's usually the first result for "<municipality> area" — several Zeeland municipalities have water area (their share of the Westerschelde/Oosterschelde) several times larger than their land area (e.g. Vlissingen: ~34 km² land vs. ~345 km² land+water). The `Municipality area (km²)` column is therefore a land-area estimate; treat it as such in any density/normalization calc downstream. Per-municipality GADM-vs-CBS agreement has up to ~8% noise from coastline/tidal-flat digitization differences, but aggregates to within ~1.3% at the province level — the test tolerances (10% per-municipality, 3% aggregate) reflect that.

## Known gaps

The GIS pipelines (`load_osm_data`, `run_bikeneat`, `spatial_overlap_analysis`, etc.) have not been run end-to-end against real data since the notebook → package migration. Before trusting output for analysis, run each script once against the same inputs the original notebooks used and diff km totals against a saved notebook run. See `MIGRATION_NOTES.md` for detail. (This gap does NOT apply to `country.py`'s pipeline — that one has been run end-to-end against real Zeeland/Cataluña/La Rioja data and validated.)

Tag-only Greenway detection (`classify_area_road()`) has lower recall than a spatial polygon join would: a paved, well-maintained greenway with no `surface`/`tracktype`/`railway`/`separation` signal won't be detected (see the "Rijwielpad Noordvoort" and "Shared greenway near Rimini" xfails in `test_area_classification.py` — the former's ground truth is confirmed Greenway by the domain owner despite lacking any of those tag signals, and its `NL:G13` sign is deliberately NOT force-mapped to `'Greenway'` since that would misclassify most of the ~20k other NL ways carrying that same sign). `separation`/`buffer` tags are also sparsely populated in OSM overall — don't expect the physical-separation Greenway signal to move numbers much outside well-mapped areas.

Spain's `traffic_sign` and `cycle_network` tags were checked empirically against `data/cataluna-latest.osm.pbf` (2026-07) as candidates for a Spain-specific sign-code `CyclingLegalConfig` rule and found insufficient: only 228 `traffic_sign`-tagged ways total, dominated by generic `hazard` signs, zero vía-ciclista codes (R407/S31/etc.) found; `cycle_network` is 99.6% `ES:BCN`-only (1,720/1,727 ways), redundant with Spain's national RD 970/2020 `layer3_maxspeed_threshold` rule (see Classification layer above). Don't re-add a Spain sign-code or network rule without new tagging data — this was verified, not assumed.


