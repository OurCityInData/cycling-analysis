# Plan: Adding pollution columns to `country_analysis.py`

Status: **all 10 columns built, validated, and wired into the pipeline output** (`run_country_analysis()` writes them to the checkpoint/CSV, plus a separate `{run_name}_pollution_by_municipality.png` table). Scope confirmed with you: Spain-only for now, EEA as primary data source (unverified E2a dataset, not waiting for verified data), daily-max-hourly proxy for NO2, RedBici doc's stated PM2.5 short-term limit used as-is, and columns 1–3 computed as a median across daily values for the year (not a median of station annual means) — with a zone/nearest-station fallback for municipalities with no station of their own, added 2026-07 (see §0's third "Done" block; supersedes §7's original "blank/NaN" decision). All open items from the earliest planning round are resolved — see §7/§8 (§8's zone-code lookup turned out not to exist as a metadata field; zones are matched to stations/municipalities spatially instead, see CLAUDE.md's Pollution columns section).

---

## 0. Progress so far / what's left for next session

**Branch:** `feat/pollution-columns`, cut off `main` (separate from the Barcelona area-classification test branch). Committed locally, **not pushed to GitHub yet** — waiting for the go-ahead.

**Done (columns 1–4, "own-municipality" stats):**
- `cycling_analysis/pollution.py` implements `fetch_eea_stations()`, `fetch_eea_measurements()`, `compute_daily_means()`, `compute_own_station_pollution_stats()` — the 4 columns (PM2.5/PM10/NO2 median annual + station count).
- Built against the **real, live EEA API** (not just the how-to PDF) — see CLAUDE.md's "Pollution columns" section for the specifics found along the way that corrected assumptions in this plan doc:
  - The E2a/unverified dataset is **hourly-only** via `ParquetFile/urls` — requesting `aggregationType: "day"` returns zero files, contrary to what the web UI's filter description implies. So daily means are always derived from hourly data in code (`compute_daily_means()`), not fetched pre-aggregated — this was already required by §5's "median across pooled daily values" definition, just confirms there's no shortcut.
  - Station coordinates live in a separate EEA-wide metadata CSV (`discomap.eea.europa.eu/map/fme/metadata/PanEuropean_metadata.csv`), which 403s the default `requests` User-Agent (needs a browser-like UA) and uses a different sampling-point ID format than the measurement files (missing the country prefix) — both handled in `fetch_eea_stations()`.
  - Some stations run two parallel sampling processes for the same pollutant (continuous automatic analyser + periodic manual reference sampler) — deduped to the automatic one to avoid double-counting that station in the pooled median.
  - Confirmed pollutant vocabulary codes: PM10 = `.../pollutant/5`, PM2.5 = `.../pollutant/6001`, NO2 = `.../pollutant/8`.
- **Manually validated against Barcelona municipality, 2025 data:** 12 distinct stations, PM2.5 median 9.8 µg/m³, PM10 median 17.9 µg/m³, NO2 median 17.6 µg/m³ — all plausible (NO2 reads lower than commonly-cited Barcelona averages, but that's expected: this pools every in-boundary station including suburban/background ones and takes a median, not a traffic-station-weighted mean).
- `pyarrow>=14.0` added to `requirements.txt` (needed to read the EEA parquet files).
- **Wired into `run_country_analysis()`** — fetched once per country run (stations + a year of hourly data), then a cheap in-memory join per municipality, matching the GADM/PBF fetch-once pattern. Bumped the checkpoint schema as expected; stale Spain checkpoints need deleting before a fresh run.

**Done (columns 5–6, zone-based legal/guideline-compliance day counts):**
- §8's "find the zone-code field" turned out to be a dead end — the EEA station metadata CSV has no zone column at all. Zones are matched **spatially** instead: `fetch_zone_geometries()` (streams the ~322MB EU-wide zone-geometry zip with `ijson`, filters to one country, caches the small per-country result), `match_stations_to_zones()` (point-in-polygon, once per country), `match_municipality_to_zones()` (`intersects`, since a municipality can straddle a zone boundary — verified: Barcelona spans `ZON_ES0901`/`ZON_ES0902`).
- Also found: this zone-geometry source has **no pollutant-specific zoning** (contrary to §3's assumption) — one zone/agglomeration layer applies across pollutants, per Directive 2008/50/EC Annex I.
- `compute_zone_pollution_stats()` implements the "worst station in zone" logic (§3) against `LEGAL_COMPLIANCE_COLUMNS` (the §4/§6 thresholds), using `compute_daily_max_hourly()` for the NO2 Spain/EU row's daily-max-hourly proxy and `compute_daily_means()` (already built for columns 1-3) for the other 5.
- **Manually validated against Barcelona** (zones ZON_ES0901/ZON_ES0902), 2025 data: Spain/EU PM2.5 33 days, PM10 8 days, NO2 0 days; WHO PM2.5 149 days, PM10 16 days, NO2 210 days. The Spain/EU-NO2-vs-WHO-NO2 gap (0 vs 210 days) is a real, well-documented pattern for Barcelona, not a bug (see CLAUDE.md's Known gaps).
- `ijson>=3.2` added to `requirements.txt` (needed for the streaming zone-geometry parse).
- `cycling_analysis.country.save_table_png()` now renders NaN cells as blank instead of the literal string `"nan"` — noticed while building the new pollution-only table PNG below; a real quality issue for a table that's expected to have blanks (no-station/no-zone-match municipalities), not scope creep.
- New output: `output/{run_name}_pollution_by_municipality.png` — a separate table (Region/Municipality + all 10 pollution columns), written after the main cycling-infrastructure table, only for countries with pollution data wired up.
- `tests/test_pollution.py` — 17 unit tests total now (columns 1-4 + 5-6 pure-computation functions: daily aggregation, own-station stats, zone matching, zone compliance-day stats), no network calls. Full suite (`pytest tests/`) passes: 38 passed, 5 xfail.
- `CLAUDE.md` updated in the same commit with the architecture + all the API gotchas above, so a future session doesn't have to rediscover them.

**Done (columns 1-3 inference — most municipalities have no own station):**
- Full pipeline run against La Rioja (177 municipalities) surfaced the real problem: only 5/177 had their own station, so columns 1-3 were blank almost everywhere — not very useful as a screening tool.
- `estimate_own_pollution_stats()` adds a 3-tier fallback per pollutant: own station (unchanged) → zone-pooled median (reusing the zone-matching already built for columns 5-6 — filled 100% of La Rioja's remaining blanks by itself) → nearest-station pooled median (`find_nearest_stations()`, k=3, capped at 50km; never fired in the La Rioja run, since every zone had a station for every pollutant). Every tier uses the same "pool daily values, take one median" statistic — confirmed with you not to switch to a distance-weighted mean for the fallback tiers, to stay consistent with the rest of the table.
- **Confirmed with you: no provenance/"data source" marker column** — inferred values silently fill columns 1-3, indistinguishable from real station medians in the output. `Station count` is unaffected (always the literal own-station count).
- Refactored `pooled_median()` and `sampling_points_in_zones()` out of the existing columns 1-4/5-6 functions so all three tiers (and columns 5-6) share the exact same pooling/zone-filter logic - no behavior change to the existing functions.
- `cycling_analysis/country.py` now precomputes `station_gdf_utm` and each municipality's `centroid_utm` once per country/run (not per municipality/pollutant) for the nearest-station tier's distance calc - avoids repeated reprojection overhead at full-Spain scale.
- Re-ran La Rioja after moving the stale checkpoint aside (schema unchanged, but *values* changed, so a checkpoint resume would have kept the old mostly-blank rows): all 177 municipalities now have non-blank PM2.5/PM10/NO2 medians. Spot-checked Logroño (own PM10/NO2 station, no own PM2.5 station): own PM10 (16.0) and NO2 (13.8) unchanged, only PM2.5 (7.1) came from the zone tier - confirms the fallback is per-pollutant, not per-municipality.
- `tests/test_pollution.py` - 10 more unit tests (`pooled_median`, `sampling_points_in_zones`, `find_nearest_stations`, `estimate_own_pollution_stats`'s 3 tiers). Full suite: 48 passed, 5 xfail.

**Done (full Cataluña run):**
- Ran the full pipeline end-to-end against Cataluña (955 municipalities via GADM) — roads + amenities + pollution together, including the columns 1-3 fallback. 953/955 municipalities completed; see CLAUDE.md's Known gaps for the 2 that were lost to a resume bug (below) before it was fixed.
- **Found and fixed a resume/checkpoint bug**: `run_country_analysis()` deduped completed municipalities by `(Region, Municipality)` name instead of `GADM ID`. GADM municipality names aren't unique within a region — Cataluña has 8 same-named pairs across different comarques (e.g. two `Pinós`) — so if a checkpoint save landed between processing two same-named municipalities, the resume logic would treat the second as "already done" and permanently skip it. Fixed in `cycling_analysis/country.py` to key on `GADM ID` throughout. Not backfilled into the existing Cataluña output per instruction — a re-run would pick up just the 2 missing rows via the now-fixed checkpoint resume.
- Pollution columns held up at Cataluña scale: 70/953 municipalities have their own station; the zone/nearest-station fallback fills the rest, consistent with the La Rioja validation.

**Left to do, roughly in order:**
1. Push the branch and open a PR once you're happy with the full 10-column output (currently sitting local-only, per your instruction not to push until told).
2. Cross-check columns 5-6 against MITECO's official 2023 compliance evaluation (§9 step 4) — not done yet; would need a separate historical `pollution_year=2023` pull since the pipeline currently targets 2025.

---

## 1. Recommended data source

**Primary: EEA Air Quality Download Service** (the European Environment Agency's official, EU-wide reporting system — Spain's legal obligation under RD 102/2011 is literally to report into this system, so it's the authoritative source, not a secondary aggregator).

- Download service: https://eeadmz1-downloads-webapp.azurewebsites.net (API: `.../swagger/index.html`)
- Data comes back as **Parquet files**, one per station+pollutant "sampling point," with columns: `Samplingpoint, Pollutant, Start, End, Value, Unit, AggType (hour/day/var), Validity, Verification, ResultTime, DataCapture`.
- Filterable by country (`ES`), pollutant, dataset, date range, and aggregation type (hour/day). Scriptable via plain HTTP POST — see the [How-To PDF](https://eeadmz1-downloads-webapp.azurewebsites.net/content/documentation/How_To_Downloads.pdf) for a working Python example (`requests.post` with a JSON filter body).
- **Zone/agglomeration geometries** (needed for the legal compliance columns) are separately downloadable, EU-wide, as GeoJSON/GeoPackage/Shapefile: https://discomap.eea.europa.eu/map/FME/AQZones/ — freshly updated (July 2026). This is the same zoning data Spain reports, so it should line up with the zone codes attached to each station.
- **Station metadata** (coordinates, zone code, station type) comes from a separate metadata flow ("data flow D"), reachable via the [Discodata SQL API](https://discodata.eea.europa.eu/) or the R package [`euroaq`](https://openair-project.github.io/euroaq/)'s `import_eea_stations()` (useful as a reference for the underlying API calls even if we implement in Python, not R).

**Secondary / cross-check: MITECO** (Spain's own ministry) publishes the *same underlying data* but also — importantly — its own **pre-computed official compliance evaluation** per station and per zone (pass/fail against RD 102/2011, already computed by the government): https://www.miteco.gob.es/en/cartografia-y-sig/ide/descargas/calidad-y-evaluacion-ambiental/informacion-de-evaluacion.html. This is gold-standard for validating our own compliance-day calculations once it's available for 2025 (see timing issue below). It's also **per-pollutant zoning** — Spain's zone boundaries differ by pollutant and by which legal value is being assessed (VLA=annual limit, VLD=daily limit, VO=target value) — so any zone-matching logic needs to be pollutant-aware, not a single static zone map.

I'd skip aqicn.org/IQAir entirely — they're real-time dashboards for humans, not built for bulk historical 2025 daily/hourly pulls, and they're not the legal source of record.

## 2. ⚠️ Timing problem — read this before picking a run date

Spain's official annual air-quality evaluation becomes "official" **9 months after the measurement year ends**. For 2025 data:

- MITECO's own official 2025 evaluation (pre-computed compliance) → not published until **~October 2026**.
- EEA's *verified* dataset (E1a) for 2025 → countries report it **by 30 September 2026**.
- Right now (mid-July 2026), only the **unverified/continuous "Up-To-Date" (E2a/UTD)** feed has 2025 data — same underlying station readings, just not yet through the formal QA/verification process.

**Decision: use E2a (unverified) now.** Confirmed — build against the unverified/continuous feed rather than waiting for Sept/Oct 2026's verified data. Worth still designing the fetch layer so it can be pointed at E1a later with just a dataset-flag change, since Spain's own re-verification could shift numbers slightly once it lands — but that's a re-run, not a rewrite.

## 3. Architecture

New module, same pattern as the existing `bike_amenities.py`: `cycling_analysis/pollution.py`, called from `country.py`'s per-municipality loop (it already has the GADM municipality polygon in hand for each row, plus the amenity/bbox pattern to reuse).

Two separate spatial joins, because the two halves of the output need different geography:

```
GADM municipality polygon
        │
        ├── point-in-polygon vs. EEA station coordinates
        │       → stations physically inside this municipality
        │       → drives columns 1–4 (own concentration + station count)
        │
        └── polygon-overlap vs. EEA zone/agglomeration geometries (per pollutant)
                → which zone(s)/agglomeration this municipality belongs to
                → pull ALL stations in that zone (or all cities in the agglomeration)
                → worst station's value governs
                → drives columns 5–6 (legal compliance days)
```

This matches the Spanish-law description in the RedBici doc exactly (worst station in the zone; agglomeration result applies to every city in it), while still letting columns 1–4 reflect the municipality's *own* air, which is a different (non-legal, more intuitive) question.

Suggested functions:
- `fetch_eea_stations(country="ES") -> DataFrame` — station metadata + coords + zone code (cached to disk like the PBFs, this doesn't need refetching per run)
- `match_stations_to_municipality(stations, municipality_polygon) -> DataFrame`
- `match_municipality_to_zones(municipality_polygon, zone_geometries, pollutant) -> list[zone_id]`
- `fetch_eea_measurements(station_ids, pollutant, year, agg_type) -> DataFrame`
- `compute_pollution_stats(municipality, ...) -> dict` (the 10 output columns for one municipality)

## 4. The 10 columns, precisely defined

| # | Column | Geography | Metric |
|---|---|---|---|
| 1 | PM2.5 median annual (µg/m³) | own-municipality stations, else zone, else nearest station (§0 update) | median across all valid daily values in 2025, pooled across every station in the municipality (falls back to a zone-pooled, then nearest-station-pooled, median if the municipality has 0 stations - see §0's 2026-07 update; not flagged as inferred) |
| 2 | PM10 median annual (µg/m³) | same fallback | same |
| 3 | NO2 median annual (µg/m³) | same fallback | same |
| 4 | Station count | own-municipality stations | count of distinct stations inside the polygon (a station counts once even if it measures multiple pollutants) |
| 5.1 | Days failing Spain/EU PM2.5 limit | zone (worst station) | days where daily mean > 25 µg/m³ (RedBici doc's stated value — see §5) |
| 5.2 | Days failing Spain/EU PM10 limit | zone (worst station) | days where the **daily mean** > 50 µg/m³ |
| 5.3 | Days failing Spain/EU NO2 limit | zone (worst station) | days where the **daily max hourly value** > 200 µg/m³ (proxy — see §5) |
| 6.1 | Days failing WHO PM2.5 guideline | zone (worst station) | days where daily mean > 15 µg/m³ |
| 6.2 | Days failing WHO PM10 guideline | zone (worst station) | days where daily mean > 45 µg/m³ |
| 6.3 | Days failing WHO NO2 guideline | zone (worst station) | days where daily mean > 25 µg/m³ (WHO's short-term NO2 guideline is already a 24h mean, not hourly — no proxy needed here) |

## 5. Methodology notes

**Columns 1–3: median across days, not median across stations (confirmed with you and your boss).** For each station, build daily values for 2025 first (average a station's hours into a daily mean — data we need anyway for columns 5–6, so no extra work). Then take the median across all valid daily values for the year — up to ~365 numbers per station, fewer if there are data gaps, which is fine since median doesn't need a fixed sample size. This is a genuinely better fit for "median annual" than my original proposal (median of a handful of per-station annual means): it's robust to a handful of extreme days (dust event, wildfire smoke, fireworks night) the way a plain annual mean isn't.
If a municipality has more than one station, we **pool every valid station-day into one list and take a single median across the pool** (default, confirmed) rather than median-of-per-station-medians. The only real risk is a station with much more valid data than another ends up weighted more heavily in the pool — worth a cheap safeguard later (e.g. require ≥75% valid days per station before it's included, using EEA's own `DataCapture` field), but not worth designing around now since most municipalities in this dataset will have 0 or 1 station anyway.

**Update (2026-07): columns 1-3 now infer a value for municipalities with 0 own stations**, rather than leaving them blank as originally planned here — in a real La Rioja run, only 5/177 municipalities had their own station, which made the "own-municipality" table mostly empty and not very useful as a screening tool. Fallback order, confirmed with you: own station (as above) → zone-pooled median (same "pool daily values, take one median" statistic, just widened to every station in the municipality's EEA zone(s) — reusing the zone-matching built for columns 5-6; this alone filled 100% of La Rioja's blanks) → nearest-station pooled median (k=3, capped at 50km) only if the zone itself has zero stations for a pollutant. **Inferred values are not flagged as such** (confirmed with you — no separate "data source" column), so don't assume every value in columns 1-3 is a direct station reading; see `cycling_analysis/pollution.py`'s `estimate_own_pollution_stats()` and CLAUDE.md's Pollution columns section.

**NO2 asymmetry:** Spain/EU's short-term NO2 standard is legally hourly (200 µg/m³, max 18 exceedance-hours/year) — there's no daily-mean version of it in law. Per your call, we'll proxy it as "day fails if its peak hour exceeded 200." WHO's short-term NO2 guideline, by contrast, actually is a 24-hour mean (25 µg/m³) — so 6.3 doesn't need the same proxy, it's a direct daily-mean comparison. Worth knowing these two NO2 columns are computed differently under the hood even though they look parallel in the table.

**PM2.5 short-term limit: using the RedBici doc's stated value as-is (confirmed).** The RedBici doc's table lists a Spain/EU "PM2,5 corto plazo" limit of 25 µg/m³. My research suggested the *currently binding* framework (RD 102/2011, in force through 2029) may only set an annual PM2.5 limit, with the daily limit not arriving until the EU's 2030 framework — but per your call, we're using the RedBici doc's number regardless, since it's the reference source you and your boss are working from. Worth a one-line footnote wherever this table is published (e.g. "per RedBici's reference table") in case someone downstream cross-checks it against the literal BOE text and it doesn't match — not a blocker, just so it's not a surprise later.

## 6. Legal/reference values to use

| Pollutant | Spain/EU annual | Spain/EU short-term | WHO 2021 annual | WHO 2021 short-term |
|---|---|---|---|---|
| PM2.5 | 25 µg/m³ | 25 µg/m³ (24h mean) — per RedBici doc | 5 µg/m³ | 15 µg/m³ (24h mean) |
| PM10 | 40 µg/m³ | 50 µg/m³ (24h mean), max 35 days/yr | 15 µg/m³ | 45 µg/m³ (24h mean) |
| NO2 | 40 µg/m³ | 200 µg/m³ (1h), max 18 exceedance-hrs/yr | 10 µg/m³ | 25 µg/m³ (24h mean) |

Sources: [BOE-A-2011-1645](https://www.boe.es/buscar/act.php?id=BOE-A-2011-1645) (Spain), [EUR-Lex OJ:L_202402881](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ:L_202402881) (EU), [WHO 2021 AQG PDF](https://iris.who.int/bitstream/handle/10665/345329/9789240034228-eng.pdf) (WHO), RedBici "Definiciones Datos por RedBici" doc (PM2.5 short-term value).

## 7. Decisions locked in (all open items from the last round are now resolved)

- **Scope:** Spain only for now. Suggest the `pollution.py` module still take a country code param even if only `"ES"` is wired up, so extending later isn't a rewrite.
- **No-station municipalities:** ~~columns 1–4 blank/NaN~~ — superseded, see §0's 2026-07 update: columns 1-3 now fall back to a zone (then nearest-station) estimate rather than staying blank; `Station count` (column 4) is still the literal own-station count. Columns 5-6 still computed from the municipality's zone, as originally decided here.
- **Dataset:** E2a (unverified), not waiting for verified E1a.
- **NO2:** daily-max-hourly proxy for Spain/EU (§5); WHO's is a direct daily-mean comparison, no proxy.
- **PM2.5 short-term limit:** RedBici doc's stated 25 µg/m³ value, used as-is.
- **Columns 1–3:** median across pooled daily values for the year, not median of per-station annual means (§5).

## 8. Remaining item (not a decision, just a lookup)

**Zone-code field name** in the EEA station metadata schema — I know it exists (zones are reported per pollutant/legal-value combination) but haven't pinned the exact column name in the Discodata/metadata API yet. A 10-minute lookup once we're actually building, not a blocker for planning.

## 9. Suggested build order (once you're ready to code)

1. ~~Write `fetch_eea_stations()` + cache; sanity-check it returns Spanish stations with coordinates and zone codes.~~ **Done** — `cycling_analysis/pollution.py`. (Zone codes specifically: still not pulled in — see §8, deferred to step 4 below since it's only needed for columns 5-6.)
2. ~~Point-in-polygon match against the existing Zeeland-style GADM municipality flow already in `country.py` — reuse, don't reinvent.~~ **Done** — `match_stations_to_municipality()`, validated against real GADM Spain (`data/gadm41_ESP_4.geojson`).
3. ~~Pull one pollutant/one year/one country of E2a daily data by hand for a known city (Barcelona) and manually sanity-check the median/station-count columns before automating.~~ **Done** — see §0 for the actual numbers.
4. Add zone geometry matching + worst-station-in-zone logic; validate against MITECO's own last *official* evaluation (2023, published Oct 2024) as ground truth, since that's a year where verified data exists on both sides. **Not started.**
5. Only then wire the hourly-based NO2 proxy and the WHO comparisons, since those are the fiddliest bits. **Not started.**
6. Add the 10 columns to `country_analysis.py`'s output, following the existing checkpoint-schema-versioning convention (`CLAUDE.md` is explicit that changing the column set requires bumping/deleting old checkpoints — this will trigger that). **Partially applicable now**: columns 1-4 are ready to wire in (see §0 item 1) without waiting for steps 4-5 above.

---

Sources: [EEA Air Quality download service](https://www.eea.europa.eu/en/datahub/datahubitem-view/778ef9f5-6293-4846-badd-56a29c70880d) · [How-To Downloads PDF](https://eeadmz1-downloads-webapp.azurewebsites.net/content/documentation/How_To_Downloads.pdf) · [euroaq R package](https://openair-project.github.io/euroaq/) · [EEA zone geometries](https://discomap.eea.europa.eu/map/FME/AQZones/) · [European Air Quality Portal download page](https://aqportal.discomap.eea.europa.eu/download-data/) · [MITECO air quality zones/stations](https://www.miteco.gob.es/en/cartografia-y-sig/ide/descargas/calidad-y-evaluacion-ambiental/informacion-de-evaluacion.html) · [BOE-A-2011-1645](https://www.boe.es/buscar/act.php?id=BOE-A-2011-1645) · [EU Directive 2024/2881](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ:L_202402881) · [WHO 2021 AQG](https://iris.who.int/bitstream/handle/10665/345329/9789240034228-eng.pdf)
