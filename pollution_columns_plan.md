# Plan: Adding pollution columns to `country_analysis.py`

Status: **planning only — no code changes made.** Scope confirmed with you: Spain-only for now, EEA as primary data source (unverified E2a dataset, not waiting for verified data), blank-but-zone-filled for no-station municipalities, daily-max-hourly proxy for NO2, RedBici doc's stated PM2.5 short-term limit used as-is, and columns 1–3 computed as a median across daily values for the year (not a median of station annual means). All open items from the last round are now resolved — see §7/§8.

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
| 1 | PM2.5 median annual (µg/m³) | own-municipality stations | median across all valid daily values in 2025, pooled across every station in the municipality (or blank if 0 stations) |
| 2 | PM10 median annual (µg/m³) | own-municipality stations | same |
| 3 | NO2 median annual (µg/m³) | own-municipality stations | same |
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
- **No-station municipalities:** columns 1–4 blank/NaN; columns 5–6 still computed from the municipality's zone.
- **Dataset:** E2a (unverified), not waiting for verified E1a.
- **NO2:** daily-max-hourly proxy for Spain/EU (§5); WHO's is a direct daily-mean comparison, no proxy.
- **PM2.5 short-term limit:** RedBici doc's stated 25 µg/m³ value, used as-is.
- **Columns 1–3:** median across pooled daily values for the year, not median of per-station annual means (§5).

## 8. Remaining item (not a decision, just a lookup)

**Zone-code field name** in the EEA station metadata schema — I know it exists (zones are reported per pollutant/legal-value combination) but haven't pinned the exact column name in the Discodata/metadata API yet. A 10-minute lookup once we're actually building, not a blocker for planning.

## 9. Suggested build order (once you're ready to code)

1. Write `fetch_eea_stations()` + cache; sanity-check it returns Spanish stations with coordinates and zone codes.
2. Point-in-polygon match against the existing Zeeland-style GADM municipality flow already in `country.py` — reuse, don't reinvent.
3. Pull one pollutant/one year/one country of E2a daily data by hand for a known city (Barcelona) and manually sanity-check the median/station-count columns before automating.
4. Add zone geometry matching + worst-station-in-zone logic; validate against MITECO's own last *official* evaluation (2023, published Oct 2024) as ground truth, since that's a year where verified data exists on both sides.
5. Only then wire the hourly-based NO2 proxy and the WHO comparisons, since those are the fiddliest bits.
6. Add the 10 columns to `country_analysis.py`'s output, following the existing checkpoint-schema-versioning convention (`CLAUDE.md` is explicit that changing the column set requires bumping/deleting old checkpoints — this will trigger that).

---

Sources: [EEA Air Quality download service](https://www.eea.europa.eu/en/datahub/datahubitem-view/778ef9f5-6293-4846-badd-56a29c70880d) · [How-To Downloads PDF](https://eeadmz1-downloads-webapp.azurewebsites.net/content/documentation/How_To_Downloads.pdf) · [euroaq R package](https://openair-project.github.io/euroaq/) · [EEA zone geometries](https://discomap.eea.europa.eu/map/FME/AQZones/) · [European Air Quality Portal download page](https://aqportal.discomap.eea.europa.eu/download-data/) · [MITECO air quality zones/stations](https://www.miteco.gob.es/en/cartografia-y-sig/ide/descargas/calidad-y-evaluacion-ambiental/informacion-de-evaluacion.html) · [BOE-A-2011-1645](https://www.boe.es/buscar/act.php?id=BOE-A-2011-1645) · [EU Directive 2024/2881](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ:L_202402881) · [WHO 2021 AQG](https://iris.who.int/bitstream/handle/10665/345329/9789240034228-eng.pdf)
