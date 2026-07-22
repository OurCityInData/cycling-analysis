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

3. **`classify_area_road()`** (`cycling_analysis/area_classification.py`) — single, area-adjustable taxonomy for the country-level screening pipeline. Replaced the old un-parameterized `classify_road()` in `country.py` (deleted). Unlike #1/#2, this one IS a single function driven by a `CyclingLegalConfig` dataclass (`path_default_legal`, `layer3_maxspeed_threshold`, `sign_code_rules`) looked up per country/municipality via `get_cycling_config()` — that's a deliberate, different design choice from #1/#2 (see below), not an oversight. Output categories are a **fixed 11-item list** (`CATEGORIES` in that module) with exact strings/order that must not change casually: `Greenway, Two-way side bike lane, One-way side bike lane, Bike lane on sidewalk, Bus-bike lane, Pacified zone at 10 km/h, Pacified street at 20 km/h, Shared street at 30 km/h, Service road, 2-1 road, Mixed traffic road`. Greenway detection is **tag-only** (no spatial/polygon join, by deliberate choice — lower recall than a polygon-based approach, but tags-only was explicitly preferred): unpaved surface/tracktype, `railway=abandoned`, the NL Layer-3 path default, OR a physical-separation signal (`separation`/`buffer` tags indicating ≥1m real gap from the road — grass verge, hedge, ditch, guard rail; paint/kerb/bollard-only separation does NOT count).

**`has_cycling_infrastructure()`** IS a single shared function (identical NL06 filter used by Amsterdam and both BikeNEAT comparisons). **`has_cycling_infrastructure_bcn()`** is a separate function — it adds Barcelona's 30 km/h city-wide network rule and removes the NL Layer-3 path default.

The classification.py module docstring explains in detail why the two city classifiers (#1/#2) cannot be safely collapsed into one config-driven function — that finding does NOT apply to `classify_area_road()`: `classify_road()` was already a single un-parameterized function applied identically across 5 countries, so making it config-driven was a genuine improvement, not a repeat of the rejected #1/#2 experiment. Only two country-specific legal rules are currently documented anywhere in this repo (ported from #1/#2's divergence): NL's Layer-3 path default, and Barcelona's municipal (not national) ≤30km/h ordinance, applied via a per-municipality override in `REGION_CYCLING_CONFIG_OVERRIDES`. Germany/Belgium/Denmark have no documented special rule — don't invent one without a source.

### Data flow

Each script:
1. Loads OSM edges from a `.pbf` file via `pyrosm` (or downloads one via `io.py` helpers)
2. Runs the appropriate `has_cycling_infrastructure*()` filter
3. Classifies each passing edge with the city's `classify_cycling_path_*()` function
4. Computes km totals / spatial overlap against the official reference GeoJSON
5. Writes CSVs and plots to `output/`

The `geometry.py` module (`fix_geometry`, `clean_isolated_edges`, `remove_small_components`) is applied after loading to clean topology before comparison.

Country-level analysis (`country.py`) uses GADM boundary files (`data/gadm41_*.geojson`) to clip OSM data municipality by municipality. Each result row also carries `GADM ID` (e.g. `'ESP.1.1.1.1_1'`) — GADM's own stable per-municipality ID (`f"GID_{GADM_CONFIG[country]['level']}"`), captured alongside `Region`/`Municipality` as an identifier column, not a metric. It writes checkpoint CSVs to the working directory so long runs can be interrupted and resumed — **checkpoint schema is versioned by column set**: `run_country_analysis()` raises loudly on load if an existing `{run_name}_checkpoint.csv`'s columns don't match the current `CATEGORIES`/`AMENITY_METRIC_COLUMNS`/`POLLUTION_COLUMNS`/area schema (identifier columns `Region`/`Municipality`/`GADM ID` are excluded from this comparison), rather than silently producing a corrupted mixed-schema table. Delete/rename old checkpoint + `*_cycling_by_municipality.csv` files after a schema change. **This check only catches column-name changes, not value/methodology changes** — a municipality already marked "done" in the checkpoint is never recomputed, so a logic change that alters values under an unchanged column set (e.g. the pollution-column inference fallback below) silently keeps stale values for already-processed rows unless the checkpoint is manually moved aside first. The resume/dedup logic tracks completed municipalities by `GADM ID`, **not** by `(Region, Municipality)` name — GADM municipality names are not unique within a region (Cataluña alone has 8 same-named municipality pairs across different comarques, e.g. two `Pinós`, two `LaMolsosa`). An earlier version keyed on name and would permanently drop the second same-named municipality if a checkpoint save landed between processing the two (fixed 2026-07; see Known gaps for the 2 Cataluña municipalities already lost to this before the fix).

Each municipality row also gets bike-amenity counts (`cycling_analysis/bike_amenities.py`, via `pyrosm.OSM.get_pois()` on the same bbox-clipped extract used for roads), the 10 pollution columns (see below), and `Municipality area (km²)` (GADM polygon reprojected to UTM). `run_country_analysis()` writes three things to `output/`, alongside the human-facing CSV at repo root: two rendered PNG tables via `save_table_png()` (matplotlib's Agg canvas directly — no pyplot/global backend state) — `{run_name}_cycling_by_municipality.png` (the full table) and, for countries with pollution data wired up, `{run_name}_pollution_by_municipality.png` (Region/Municipality + just the 10 pollution columns — a Netherlands/etc. run skips this one rather than rendering an all-blank table); `save_table_png()` renders NaN cells as blank, not the literal string `"nan"` — and `{run_name}_municipalities.csv` via `save_database_csv()`, meant for later ingestion into a webapp/backend/database rather than human reading: snake_case ASCII column names (`slugify_column()`, e.g. `'PM2.5 median annual (µg/m³)'` → `'pm2_5_median_annual_ug_m3'`; a column name starting with a digit like `'2-1 road'` gets a leading underscore, since SQL generally rejects leading-digit identifiers), plus `country`/`run_name`/`pollution_year`/`generated_at` columns (constant per run, but necessary once rows from multiple runs/dates are pooled into one table) and `gadm_id` as the real join key — `region`/`municipality` display names are NOT reliable join keys (GADM strips spaces from some Spanish region/municipality names inconsistently, and accents vary by source).

### Pollution columns (Spain only)

`cycling_analysis/pollution.py` implements all 10 columns from `pollution_columns_plan.md` and is wired into `run_country_analysis()` (Spain only — see `POLLUTION_COUNTRIES` in `country.py`; every other country gets blank/NaN columns 1-10, same as a Spanish municipality with 0 stations/zone match):
- **Columns 1-4** (`estimate_own_pollution_stats()`): PM2.5/PM10/NO2 median annual µg/m³ + station count. `Station count` is always the literal count of stations physically inside the municipality (`compute_own_station_pollution_stats()`, unchanged by the rest of this paragraph). The 3 median values themselves have a **3-tier fallback**, since most municipalities have no station of their own (in a real La Rioja run, only 5/177) — own-station pooled median first; if blank, a zone-pooled median next (every station in the municipality's EEA zone(s), reusing the same zone-matching machinery as columns 5-6 — this alone fills ~100% of remaining blanks in practice); if the zone itself has zero stations for that pollutant (rare), a nearest-station pooled median last (`find_nearest_stations()`, k=3, capped at 50km). All 3 tiers use the same "pool daily values, take one median" statistic (`pooled_median()`) — deliberately consistent with the rest of the table's methodology, not a distance-weighted mean for the fallback tiers. **Inferred values are not flagged as such anywhere in the output** — a deliberate choice (confirmed with the user), not an oversight, so don't assume every value in these 3 columns is a direct station reading.
- **Columns 5-6** (`compute_zone_pollution_stats()`): 6 zone-based legal/guideline exceedance-day counts (Spain/EU + WHO, per pollutant) — "worst station in the municipality's zone(s) governs" (`LEGAL_COMPLIANCE_COLUMNS`), matching `pollution_columns_plan.md` §3-4.

Verified empirically against the live API (2026-07), correcting several assumptions in the original plan:
- The EEA download API is `https://eeadmz1-downloads-api-appservice.azurewebsites.net/` (a separate host from the `eeadmz1-downloads-webapp` the plan doc linked, which is the web UI). `POST /ParquetFile/urls` with `{"countries": [...], "pollutants": [...], "dataset": 1, "aggregationType": "hour", ...}` returns one parquet-file URL per station "sampling point"; each file holds that sampling point's complete hourly time series (not just the requested date range — datetime filters are close-to-ignored by this endpoint in practice).
- **`aggregationType: "day"` returns zero files for `dataset: 1` (the Up-To-Date/E2a unverified feed)** — that dataset is hourly-only via this endpoint, contrary to what the web UI's "Type" filter description implies. `fetch_eea_measurements()` therefore always fetches hourly data; `compute_daily_means()`/`compute_daily_max_hourly()` derive the daily means/maxes columns 1-3 and 5-6 need. This isn't extra work, it's what the plan's §5 "median across pooled daily values" already required.
- Station coordinates aren't in the measurement parquet files at all — they come from a separate EEA-wide metadata CSV (`https://discomap.eea.europa.eu/map/fme/metadata/PanEuropean_metadata.csv`, ~27MB, cached once and shared across every country). **That host 403s the default `requests` User-Agent** — `fetch_eea_stations()` sends a browser-like UA. Its `SamplingPoint` column also lacks the country prefix the measurement parquet's `Samplingpoint` column carries (`SP_38048001_10_49` vs `ES/SP_38048001_10_49`) — `fetch_eea_stations()` reattaches it so the two join. **This metadata CSV has no zone/agglomeration field at all** — contrary to what `pollution_columns_plan.md` §8 assumed was just an unfound column name, stations are matched to zones spatially (see below), not via a lookup.
- Some stations run two parallel sampling processes for the same pollutant (e.g. a continuous `automatic` analyser plus a periodic `active`/gravimetric reference sampler), each with its own `SamplingPoint` — pooling both would double-count that station in the column 1-3 median, so `fetch_eea_stations()` keeps only the `automatic` one per station+pollutant where both exist.
- Pollutant notation strings for the API's `pollutants` filter are simple (`"PM10"`, `"PM2.5"`, `"NO2"`) — the metadata CSV's `AirPollutantCode` column uses full vocabulary URIs instead (`.../pollutant/5`, `/6001`, `/8` respectively); `POLLUTANT_URI_CODES` maps between the two.
- **Zone geometries** (`fetch_zone_geometries()`) come from `https://discomap.eea.europa.eu/map/FME/AQZones/`: a single ~322MB zip (~945MB uncompressed) containing ONE GeoJSON with every EU country's zones together (`Country` property, `ZoneId` values like `ZON_ES0104`), not split per-country, and reprojected to EPSG:3857 (Web Mercator) — `fetch_zone_geometries()` streams it with `ijson` (rather than a full in-memory `gpd.read_file()`) to filter to one country, then caches the much smaller per-country result so later calls never touch the full EU file again. There's also no pollutant-specific zoning in this file, contrary to `pollution_columns_plan.md` §3's assumption — each country reports one zone/agglomeration layer that applies across pollutants, per Directive 2008/50/EC Annex I. A municipality can straddle more than one zone (verified: Barcelona spans `ZON_ES0901`/`ZON_ES0902`) — `match_municipality_to_zones()` uses `intersects`, not `within`.

`tests/test_pollution.py` covers the pure-computation functions (daily aggregation, own-station stats, zone-matching, zone compliance-day stats) with in-memory fixtures — no network. `fetch_eea_stations()`/`fetch_eea_measurements()`/`fetch_zone_geometries()` hit live EEA endpoints and were validated by hand against a real municipality (see Known gaps).

### Key files

- `cycling_analysis/constants.py` — `PLAIN_HIGHWAY`, `CYCLEWAY_USEFUL_VALUES`, `CYCLEWAY_LANE_VALUES`, `OSM_KEYS` (shared across all city modules)
- `cycling_analysis/area_classification.py` — `classify_area_road()`, `CyclingLegalConfig`, `CATEGORIES` (the country-level screening taxonomy — see Classification layer above)
- `cycling_analysis/bike_amenities.py` — `compute_bike_amenity_stats()`: bicycle parking (spots/capacity/covered) and combined shop+rental+repair-station counts, from OSM POI tags
- `cycling_analysis/bikeneat.py` — BikeNEAT predicate engine (shared by Barcelona and Freiburg); its German StVO traffic-sign logic was deliberately NOT ported into `classify_area_road()` — `CyclingLegalConfig.sign_code_rules` is an empty opt-in extension point for that, per-country, once someone verifies sign-tagging density
- `cycling_analysis/cities/amsterdam.py` and `cities/barcelona.py` — city-specific metrics and plotting (intentionally separate interfaces)
- `tests/test_classification.py` — 11 ground-truthed real-world OSM ways for the Barcelona classifier; 3 marked `xfail` (all `highway=cycleway` cases with no distinguishing signal for rural/off-road greenways)
- `tests/test_area_classification.py` — the SAME 11 real OSM ways (tags/urls copied verbatim), run through `classify_area_road()` instead for comparison; 9/11 correct, 2 xfail (one fewer than the Barcelona classifier — the tag-only `railway=abandoned` check fixes one case BCN's classifier gets wrong)
- `tests/test_bike_amenities.py` — unit tests for `compute_bike_amenity_stats()`
- `cycling_analysis/pollution.py` — `fetch_eea_stations()`, `fetch_eea_measurements()`, `fetch_zone_geometries()`, `compute_daily_means()`, `compute_daily_max_hourly()`, `match_stations_to_zones()`, `match_municipality_to_zones()`, `compute_zone_pollution_stats()`, `estimate_own_pollution_stats()` (columns 1-4, with the own/zone/nearest-station fallback), `compute_own_station_pollution_stats()`, `find_nearest_stations()`, `pooled_median()`, `sampling_points_in_zones()`: all 10 pollution columns (see Pollution columns above and `pollution_columns_plan.md`)
- `tests/test_pollution.py` — unit tests for `pollution.py`'s pure-computation functions (no network)

## Known gaps

The GIS pipelines (`load_osm_data`, `run_bikeneat`, `spatial_overlap_analysis`, etc.) have not been run end-to-end against real data since the notebook → package migration. Before trusting output for analysis, run each script once against the same inputs the original notebooks used and diff km totals against a saved notebook run. See `MIGRATION_NOTES.md` for detail. (This gap does NOT apply to `country.py`'s pipeline — that one has been run end-to-end against real Zeeland/Cataluña/La Rioja data and validated.)

`spain_cataluna_cycling_by_municipality.csv` (full Cataluña run, 2026-07-21) is missing 2 of Cataluña's 955 municipalities — `LaMolsosa` (`GID_4` `ESP.6.3.6.6_1`'s twin `ESP.6.3.11.1_1`) and `Pinós` (`ESP.6.3.6.15_1`'s twin `ESP.6.3.12.1_1`) — lost to the name-keyed resume bug described above, across a checkpoint boundary in an earlier session, before the GADM-ID fix. The bug is fixed so it can't happen again, but this specific run wasn't backfilled. Re-run `country_analysis.py --country spain --regions Cataluña` (checkpoint will only fetch the 2 missing rows) if a complete 955-row Cataluña table is needed.

Tag-only Greenway detection (`classify_area_road()`) has lower recall than a spatial polygon join would: a paved, well-maintained greenway with no `surface`/`tracktype`/`railway`/`separation` signal won't be detected (see the 2 xfails in `test_area_classification.py`). `separation`/`buffer` tags are also sparsely populated in OSM overall — don't expect the physical-separation Greenway signal to move numbers much outside well-mapped areas.

`cycling_analysis/pollution.py` (all 10 columns of `pollution_columns_plan.md`) is wired into `run_country_analysis()` and validated against Barcelona municipality (GADM `NAME_4`), 2025, E2a/unverified dataset (2026-07):
- Columns 1-4: 12 distinct stations, PM2.5 median 9.8 µg/m³, PM10 median 17.9 µg/m³, NO2 median 17.6 µg/m³ — all plausible, though the NO2 figure reads lower than commonly-cited Barcelona annual averages; that's expected, not a bug, since this pools daily values across every in-boundary station (including suburban/background ones, which run lower than the traffic stations that dominate typical published annual means) and takes a median rather than a mean (deliberately robust to short high-pollution episodes, see `pollution_columns_plan.md` §5).
- Columns 5-6 (worst-station-in-zone, zones `ZON_ES0901`/`ZON_ES0902`): Spain/EU PM2.5 33 days, PM10 8 days, NO2 0 days; WHO PM2.5 149 days, PM10 16 days, NO2 210 days — the WHO-NO2-vs-Spain/EU-NO2 gap (210 vs 0 days) is a real, well-known pattern for Barcelona (WHO's guideline is far stricter than the legal EU/Spain limit, and legally NO2 is assessed hourly not daily — see the plan's §5 NO2 asymmetry note), not a computation error.
- Not yet cross-checked against MITECO's official compliance evaluation (`pollution_columns_plan.md` §9 step 4) — that's 2023 data (last verified year available) and would need a separate historical pull, since the pipeline currently only fetches `pollution_year` (default 2025).

The columns 1-4 own/zone/nearest-station fallback (see "Pollution columns" above) was validated against a full La Rioja run (177 municipalities, 2026-07): before the fallback, only 5 municipalities (those with their own station) had non-blank PM2.5/PM10/NO2 medians; after, all 177 do, entirely via the zone tier — the nearest-station tier never fired in this run (every La Rioja municipality's zone had at least one station for every pollutant). Spot-checked Logroño (has its own PM10/NO2 station but not PM2.5): its own PM10 (16.0) and NO2 (13.8) were unchanged by the fallback, only the missing PM2.5 (7.1) came from the zone tier — confirming the own-station tier takes priority per-pollutant, not per-municipality.

`fetch_eea_measurements()` makes one live HTTP call per station-sampling-point (hundreds for all of Spain/one pollutant/one year) and is slow the first time; results are cached to `data/eea_measurements_*.parquet` and `data/eea_station_metadata.csv` afterward. `fetch_zone_geometries()`'s first call per machine downloads the ~322MB EU-wide zip (`data/AQZoneGeometries_GeojsonFiles.zip`) and streams/filters it (~15-20s) before caching the ~110MB Spain-only result (`data/eea_zones_ES.geojson`) — later calls/runs read the small cache directly.
