"""
Air-quality (pollution) columns for the country-level screening pipeline
(cycling_analysis/country.py's run_country_analysis()). See
pollution_columns_plan.md for the full column list and methodology. This
module implements all 10 columns: 1-4 (own-municipality station stats) and
5-6 (zone-based legal/guideline-compliance day counts).

Spain-only for now (see CLAUDE.md's Known gaps). Source: EEA Air Quality
Download API (https://eeadmz1-downloads-api-appservice.azurewebsites.net),
Up-To-Date/E2a (unverified) dataset - Spain's verified E1a dataset for 2025
isn't published until ~Sept/Oct 2026 (RD 102/2011's 9-month reporting lag).

Empirically verified against the live API (2026-07): the E2a dataset only
carries AggType='hour' rows via ParquetFile/urls - requesting
aggregationType='day' returns zero files, despite the "Type" filter's web-UI
description implying daily aggregates exist for every dataset. This module
therefore always fetches hourly data and computes daily means/maxes itself,
which is what columns 1-3 and 5-6's definitions need anyway.

Station coordinates are NOT in the measurement parquet files - they come
from a separate EEA-wide metadata CSV
(https://discomap.eea.europa.eu/map/fme/metadata/PanEuropean_metadata.csv,
~27MB, one row per station+pollutant "sampling point", shared across every
country/pollutant so it's cached once regardless of which country calls
fetch_eea_stations()). Its SamplingPoint column (e.g. 'SP_38048001_10_49')
lacks the country prefix that the measurement parquet's Samplingpoint column
carries (e.g. 'ES/SP_38048001_10_49') - fetch_eea_stations() adds it back so
the two are joinable. That metadata CSV has NO zone/agglomeration field at
all, contrary to pollution_columns_plan.md section 8's assumption that one
just needed locating - stations are matched to zones spatially instead (see
fetch_zone_geometries()/match_stations_to_zones() below), not via a lookup
column.

Zone geometries (needed for columns 5-6) come from a separate EEA endpoint,
https://discomap.eea.europa.eu/map/FME/AQZones/: a single ~322MB zip
(~945MB uncompressed) containing ONE GeoJSON with every EU country's zones
together (`Country` property), not split per-country. There's also no
pollutant-specific zoning in this file, contrary to
pollution_columns_plan.md section 3's assumption - each country reports one
zone/agglomeration layer (`ZoneId`, e.g. 'ZON_ES0104') that applies across
pollutants, per Directive 2008/50/EC Annex I. Geometries are in EPSG:3857
(Web Mercator) in the source file. fetch_zone_geometries() streams through
the ~1GB JSON with ijson rather than a full in-memory parse (a plain
gpd.read_file() on the whole EU file would be needlessly slow/memory-heavy
for a per-country need), filters to the requested country, then caches the
much smaller per-country GeoDataFrame so later calls never touch the full
EU file again.
"""

import io
import zipfile
from pathlib import Path

import geopandas as gpd
import ijson
import pandas as pd
import requests
from shapely.geometry import shape

EEA_API_BASE = 'https://eeadmz1-downloads-api-appservice.azurewebsites.net/'
EEA_METADATA_CSV_URL = 'https://discomap.eea.europa.eu/map/fme/metadata/PanEuropean_metadata.csv'
EEA_ZONES_ZIP_URL = 'https://discomap.eea.europa.eu/map/FME/AQZones/AQZoneGeometries_GeojsonFiles.zip'

# Verified against the EEA pollutant vocabulary
# (https://dd.eionet.europa.eu/vocabulary/aq/pollutant/csv), 2026-07. The
# notation strings (dict keys) are passed directly as the download API's
# `pollutants` filter value; the URIs (values) are how the metadata CSV's
# AirPollutantCode column identifies the same pollutants.
POLLUTANT_URI_CODES = {
    'PM2.5': 'http://dd.eionet.europa.eu/vocabulary/aq/pollutant/6001',
    'PM10': 'http://dd.eionet.europa.eu/vocabulary/aq/pollutant/5',
    'NO2': 'http://dd.eionet.europa.eu/vocabulary/aq/pollutant/8',
}

DATASET_UTD = 1  # Unverified, continuously-transmitted Up-To-Date/E2a data

# discomap.eea.europa.eu returns 403 for requests's default User-Agent string
# (verified 2026-07) - harmless browser-like UA works fine.
_REQUEST_HEADERS = {'User-Agent': 'Mozilla/5.0'}


# Columns 5.1-6.3: (pollutant, comparison basis, threshold µg/m³, column name).
# 'daily_mean' vs 'daily_max' matters only for the NO2 Spain/EU row - see
# pollution_columns_plan.md section 5: NO2's Spain/EU short-term standard is
# legally hourly (200 µg/m³, no daily-mean version), so a day "fails" if its
# peak hour exceeded the threshold; every other row is a direct daily-mean
# comparison. Values from pollution_columns_plan.md section 6 (Spain/EU:
# BOE-A-2011-1645 / EU Directive 2024/2881; WHO: 2021 AQG); PM2.5 short-term
# uses the RedBici reference doc's stated value, not BOE's (see plan §5).
LEGAL_COMPLIANCE_COLUMNS = [
    ('PM2.5', 'daily_mean', 25.0, 'Days failing Spain/EU PM2.5 limit'),
    ('PM10', 'daily_mean', 50.0, 'Days failing Spain/EU PM10 limit'),
    ('NO2', 'daily_max', 200.0, 'Days failing Spain/EU NO2 limit'),
    ('PM2.5', 'daily_mean', 15.0, 'Days failing WHO PM2.5 guideline'),
    ('PM10', 'daily_mean', 45.0, 'Days failing WHO PM10 guideline'),
    ('NO2', 'daily_mean', 25.0, 'Days failing WHO NO2 guideline'),
]

# Columns 1-3: (pollutant, column name) - shared by compute_own_station_pollution_stats()
# and estimate_own_pollution_stats()'s zone/nearest-station fallback tiers, so
# the two can't drift apart on which pollutants/column names exist.
POLLUTION_MEDIAN_COLUMNS = [
    ('PM2.5', 'PM2.5 median annual (µg/m³)'),
    ('PM10', 'PM10 median annual (µg/m³)'),
    ('NO2', 'NO2 median annual (µg/m³)'),
]

POLLUTION_COLUMNS = (
    [col for _, col in POLLUTION_MEDIAN_COLUMNS]
    + ['Station count']
    + [col for *_, col in LEGAL_COMPLIANCE_COLUMNS]
)


def fetch_eea_stations(country: str, data_dir: Path) -> gpd.GeoDataFrame:
    """
    Station+sampling-point metadata (coordinates, EoI code, pollutant) for
    `country` (ISO alpha-2, e.g. 'ES'), filtered to PM2.5/PM10/NO2 and to
    sampling points not permanently retired before 2025 (a handful of rows
    carry an ObservationDateEnd from years ago - keeping them would inflate
    the municipality "Station count" column with stations no longer
    reporting anything).
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(exist_ok=True, parents=True)
    cache = data_dir / 'eea_station_metadata.csv'

    if not cache.exists():
        r = requests.get(EEA_METADATA_CSV_URL, timeout=300, headers=_REQUEST_HEADERS)
        r.raise_for_status()
        cache.write_bytes(r.content)
    meta = pd.read_csv(cache, sep='\t', low_memory=False)

    uri_to_pollutant = {v: k for k, v in POLLUTANT_URI_CODES.items()}
    meta = meta[
        (meta['Countrycode'] == country)
        & (meta['AirPollutantCode'].isin(uri_to_pollutant))
    ].copy()
    meta['pollutant'] = meta['AirPollutantCode'].map(uri_to_pollutant)

    # Keep only the most recent metadata row per SamplingPoint (equipment
    # changes create multiple historical rows for the same physical sampling
    # point), then drop sampling points that were retired before 2025.
    meta = meta.sort_values('ObservationDateBegin').drop_duplicates('SamplingPoint', keep='last')
    still_active = meta['ObservationDateEnd'].isna() | (
        pd.to_datetime(meta['ObservationDateEnd']).dt.year >= 2025
    )
    meta = meta[still_active]

    # A single station can run two parallel sampling processes for the same
    # pollutant (e.g. a continuous 'automatic' analyser plus a periodic
    # 'active'/gravimetric reference-method sampler) - each gets its own
    # SamplingPoint, which would double-count that station's air in the
    # column 1-3 pooled median otherwise. Keep the continuous one per
    # station+pollutant; it matches this module's hourly-fetch approach.
    meta['_measurement_priority'] = (meta['MeasurementType'] != 'automatic').astype(int)
    meta = meta.sort_values('_measurement_priority').drop_duplicates(
        ['AirQualityStationEoICode', 'pollutant'], keep='first'
    )

    samplingpoint = country + '/' + meta['SamplingPoint']
    gdf = gpd.GeoDataFrame(
        {
            'station_code': meta['AirQualityStationEoICode'].values,
            'samplingpoint': samplingpoint.values,
            'pollutant': meta['pollutant'].values,
        },
        geometry=gpd.points_from_xy(meta['Longitude'], meta['Latitude']),
        crs='EPSG:4326',
    )
    return gdf


def match_stations_to_municipality(stations_gdf: gpd.GeoDataFrame, municipality_polygon) -> gpd.GeoDataFrame:
    """Point-in-polygon match. Both geometries must be in EPSG:4326 (GADM's native CRS)."""
    return stations_gdf[stations_gdf.within(municipality_polygon)]


def fetch_zone_geometries(country: str, data_dir: Path) -> gpd.GeoDataFrame:
    """
    EEA air-quality zone/agglomeration boundaries for `country` (ISO alpha-2,
    e.g. 'ES') - the geography columns 5-6 are computed against (see this
    module's docstring for why this needs a streaming filter rather than a
    plain gpd.read_file(), and why there's one zone layer, not a
    pollutant-specific one). Reprojected to EPSG:4326 to match GADM. Cached
    to `{data_dir}/eea_zones_{country}.geojson`; the ~322MB source zip is
    also cached (`{data_dir}/AQZoneGeometries_GeojsonFiles.zip`) so a cache
    clear only re-downloads, never re-fetches, on a second country.
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(exist_ok=True, parents=True)
    cache = data_dir / f'eea_zones_{country}.geojson'
    if cache.exists():
        return gpd.read_file(cache)

    zip_path = data_dir / 'AQZoneGeometries_GeojsonFiles.zip'
    if not zip_path.exists():
        r = requests.get(EEA_ZONES_ZIP_URL, headers=_REQUEST_HEADERS, timeout=600, stream=True)
        r.raise_for_status()
        with open(zip_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=4 * 1024 * 1024):
                f.write(chunk)

    zone_ids, geoms = [], []
    with zipfile.ZipFile(zip_path) as z:
        json_name = [n for n in z.namelist() if n.lower().endswith('.json')][0]
        with z.open(json_name) as f:
            for feat in ijson.items(f, 'features.item'):
                props = feat['properties']
                if props.get('Country') != country:
                    continue
                zone_ids.append(props['ZoneId'])
                geoms.append(shape(feat['geometry']))

    gdf = gpd.GeoDataFrame(
        {'zone_id': zone_ids}, geometry=geoms, crs='EPSG:3857'
    ).to_crs('EPSG:4326')
    gdf.to_file(cache, driver='GeoJSON')
    return gdf


def match_stations_to_zones(stations_gdf: gpd.GeoDataFrame, zones_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Attach the EEA zone each station falls in (point-in-polygon), once per
    country - stations metadata has no zone field (see module docstring), so
    this is a spatial join rather than a lookup. A station outside every
    zone polygon (a handful, near coastlines/borders - verified 2026-07
    against Spain: 6 of 1445) gets zone_id=NaN and is simply excluded from
    every municipality's zone-based columns, same as a station with no
    matching pollutant.
    """
    joined = gpd.sjoin(stations_gdf, zones_gdf[['zone_id', 'geometry']], how='left', predicate='within')
    return joined.drop(columns='index_right')


def match_municipality_to_zones(zones_gdf: gpd.GeoDataFrame, municipality_polygon) -> list:
    """
    Zones overlapping `municipality_polygon` - intersects, not within/covers,
    since a municipality can straddle a zone boundary (e.g. Barcelona spans
    ZON_ES0901 and ZON_ES0902 - verified 2026-07). Both geometries must be
    in EPSG:4326 (GADM's native CRS).
    """
    return zones_gdf.loc[zones_gdf.intersects(municipality_polygon), 'zone_id'].tolist()


def fetch_eea_measurements(country: str, pollutant: str, year: int, data_dir: Path,
                            dataset: int = DATASET_UTD) -> pd.DataFrame:
    """
    Hourly measurements for every `country` station reporting `pollutant`,
    for `year`, from EEA's E2a (unverified) dataset by default.

    Cached to disk per country/pollutant/year/dataset - this is a slow call
    (one HTTP GET per station-sampling-point; hundreds for Spain), so
    callers should fetch once per country/pollutant/year and reuse across
    the municipality loop rather than re-fetching per municipality.

    Returns columns: Samplingpoint, Start (hourly timestamp), Value - already
    filtered to Validity > 0 (EEA's observationvalidity vocabulary: 1-4 are
    all "valid" variants, negative codes mean invalid/missing).
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(exist_ok=True, parents=True)
    safe_pollutant = pollutant.replace('.', '')
    cache = data_dir / f'eea_measurements_{country}_{safe_pollutant}_{year}_ds{dataset}.parquet'
    if cache.exists():
        return pd.read_parquet(cache)

    resp = requests.post(
        f'{EEA_API_BASE}ParquetFile/urls',
        json={
            'countries': [country],
            'cities': [],
            'pollutants': [pollutant],
            'dataset': dataset,
            'source': 'Custom script',
            'dateTimeStart': f'{year}-01-01T00:00:00Z',
            'dateTimeEnd': f'{year}-12-31T23:59:59Z',
            'aggregationType': 'hour',
        },
        timeout=120,
    )
    resp.raise_for_status()
    urls = [line for line in resp.text.splitlines()[1:] if line.strip()]

    frames = []
    for url in urls:
        r = requests.get(url, timeout=120)
        if r.status_code != 200:
            continue
        df = pd.read_parquet(io.BytesIO(r.content), columns=['Samplingpoint', 'Start', 'Value', 'Validity'])
        df = df[(df['Validity'] > 0) & (df['Start'].dt.year == year)]
        frames.append(df[['Samplingpoint', 'Start', 'Value']])

    result = (
        pd.concat(frames, ignore_index=True) if frames
        else pd.DataFrame(columns=['Samplingpoint', 'Start', 'Value'])
    )
    result.to_parquet(cache)
    return result


def compute_daily_means(hourly_df: pd.DataFrame) -> pd.DataFrame:
    """One row per (Samplingpoint, date): mean of that sampling point's valid hourly values for the day."""
    if hourly_df.empty:
        return pd.DataFrame(columns=['Samplingpoint', 'date', 'daily_mean'])
    df = hourly_df.copy()
    df['date'] = df['Start'].dt.date
    return (
        df.groupby(['Samplingpoint', 'date'])['Value']
        .mean()
        .reset_index()
        .rename(columns={'Value': 'daily_mean'})
    )


def compute_daily_max_hourly(hourly_df: pd.DataFrame) -> pd.DataFrame:
    """
    One row per (Samplingpoint, date): max of that sampling point's valid
    hourly values for the day. Only needed for the NO2 Spain/EU short-term
    row in LEGAL_COMPLIANCE_COLUMNS - see that constant's comment.
    """
    if hourly_df.empty:
        return pd.DataFrame(columns=['Samplingpoint', 'date', 'daily_max'])
    df = hourly_df.copy()
    df['date'] = df['Start'].dt.date
    return (
        df.groupby(['Samplingpoint', 'date'])['Value']
        .max()
        .reset_index()
        .rename(columns={'Value': 'daily_max'})
    )


def sampling_points_in_zones(zone_stations: gpd.GeoDataFrame, zone_ids: list, pollutant: str) -> pd.Series:
    """
    samplingpoint values for stations measuring `pollutant` whose zone_id is
    one of `zone_ids` - shared by compute_zone_pollution_stats() (columns 5-6)
    and estimate_own_pollution_stats()'s zone fallback tier (columns 1-3), so
    both use the exact same "which stations count as being in this
    municipality's zone" definition.
    """
    if not zone_ids:
        return zone_stations['samplingpoint'].iloc[0:0]
    in_zone = zone_stations[zone_stations['zone_id'].isin(zone_ids)]
    return in_zone.loc[in_zone['pollutant'] == pollutant, 'samplingpoint']


def pooled_median(sampling_points: pd.Series, daily_df: pd.DataFrame) -> float:
    """
    Median across the POOLED daily values (a `daily_mean`/`daily_max`-style
    column) of every sampling point in `sampling_points`, not a median of
    per-station medians - see pollution_columns_plan.md section 5. NaN if
    `sampling_points` is empty or none of them have any rows in `daily_df`.
    """
    if sampling_points.empty or daily_df is None or daily_df.empty:
        return float('nan')
    value_col = 'daily_mean' if 'daily_mean' in daily_df.columns else 'daily_max'
    pooled = daily_df.loc[daily_df['Samplingpoint'].isin(sampling_points), value_col]
    return round(pooled.median(), 1) if not pooled.empty else float('nan')


def compute_zone_pollution_stats(zone_ids: list, zone_stations: gpd.GeoDataFrame,
                                  daily_means_by_pollutant: dict, daily_max_by_pollutant: dict) -> dict:
    """
    The 6 zone-based legal/guideline-compliance columns for one municipality
    (pollution_columns_plan.md sections 3-4): "worst station in the zone
    governs" - for each LEGAL_COMPLIANCE_COLUMNS row, every station whose
    zone_id is one of `zone_ids` (there can be more than one - see
    match_municipality_to_zones()) is checked, and the MAX exceedance-day
    count among them is reported (not summed or averaged across stations).

    zone_ids: match_municipality_to_zones() output for this municipality.
    zone_stations: match_stations_to_zones() output covering ALL of the
        country's stations (not pre-filtered to zone_ids or a municipality) -
        callers compute this once per country, like daily_means_by_pollutant.
    daily_means_by_pollutant / daily_max_by_pollutant: {'PM2.5': df, ...},
        each covering all of the country's sampling points - computed once
        per country from the same hourly fetch columns 1-4 already need.

    Blank (NaN) if zone_ids is empty (municipality didn't spatially match
    any zone) or no station measuring that pollutant falls in any of them.
    """
    stats = {col: float('nan') for *_, col in LEGAL_COMPLIANCE_COLUMNS}
    if not zone_ids:
        return stats

    for pollutant, basis, threshold, col in LEGAL_COMPLIANCE_COLUMNS:
        sampling_points = sampling_points_in_zones(zone_stations, zone_ids, pollutant)
        if sampling_points.empty:
            continue
        daily = (daily_means_by_pollutant if basis == 'daily_mean' else daily_max_by_pollutant).get(pollutant)
        if daily is None or daily.empty:
            continue
        value_col = 'daily_mean' if basis == 'daily_mean' else 'daily_max'
        in_zone_daily = daily[daily['Samplingpoint'].isin(sampling_points)]
        if in_zone_daily.empty:
            continue
        per_station_fail_days = (
            in_zone_daily.assign(fails=in_zone_daily[value_col] > threshold)
            .groupby('Samplingpoint')['fails'].sum()
        )
        stats[col] = int(per_station_fail_days.max())

    return stats


def compute_own_station_pollution_stats(muni_stations: gpd.GeoDataFrame,
                                         daily_means_by_pollutant: dict) -> dict:
    """
    The 4 "own-municipality" pollution columns for one municipality.

    muni_stations: output of match_stations_to_municipality() for this
        municipality - all pollutants pooled together (station_code,
        samplingpoint, pollutant, geometry columns).
    daily_means_by_pollutant: {'PM2.5': df, 'PM10': df, 'NO2': df}, each the
        compute_daily_means() output for that pollutant covering ALL of the
        country's sampling points (not pre-filtered to this municipality) -
        callers fetch/compute this once per country, not per municipality.

    Blank (NaN) if the municipality has no station for that pollutant - see
    estimate_own_pollution_stats() for the zone/nearest-station fallback that
    fills most of these blanks in practice (most municipalities have no
    station of their own).
    """
    stats = {c: float('nan') for c in POLLUTION_COLUMNS}
    stats['Station count'] = muni_stations['station_code'].nunique()

    for pollutant, col in POLLUTION_MEDIAN_COLUMNS:
        sampling_points = muni_stations.loc[muni_stations['pollutant'] == pollutant, 'samplingpoint']
        daily = daily_means_by_pollutant.get(pollutant)
        stats[col] = pooled_median(sampling_points, daily)

    return stats


def find_nearest_stations(stations_gdf_utm: gpd.GeoDataFrame, pollutant: str, point_utm,
                           k: int = 3, max_distance_km: float = 50) -> gpd.GeoDataFrame:
    """
    The k nearest `pollutant` stations to `point_utm`, capped at
    `max_distance_km` (possibly fewer, possibly empty if none are within
    range). Last-resort fallback tier in estimate_own_pollution_stats(), used
    only when a municipality's own stations AND its zone's stations both have
    no data for a pollutant (rare - see that function's docstring).

    Both `stations_gdf_utm` and `point_utm` must already be projected to a
    real-distance (UTM) CRS by the caller - reprojecting per call would repeat
    the same pyproj transform for every blank pollutant/municipality at
    full-country scale (thousands of calls), so callers project once per
    country/run instead (see cycling_analysis.country.run_country_analysis()).
    """
    candidates = stations_gdf_utm[stations_gdf_utm['pollutant'] == pollutant]
    if candidates.empty:
        return candidates
    distances = candidates.geometry.distance(point_utm)
    within_radius = distances[distances <= max_distance_km * 1000]
    return candidates.loc[within_radius.nsmallest(k).index]


def estimate_own_pollution_stats(muni_stations: gpd.GeoDataFrame, zone_ids: list,
                                  zone_stations: gpd.GeoDataFrame, daily_means_by_pollutant: dict,
                                  stations_gdf_utm: gpd.GeoDataFrame, municipality_centroid_utm,
                                  k_nearest: int = 3, max_distance_km: float = 50) -> dict:
    """
    Columns 1-4, with fallback for the common case where a municipality has
    no station of its own (e.g. 172/177 in a real La Rioja run): for each of
    the 3 median columns still blank after compute_own_station_pollution_stats()
    (Tier 1), try a zone-pooled median next (Tier 2 - every station in the
    municipality's own zone(s), reusing the same zone-matching machinery
    built for columns 5-6; this alone fills ~100% of blanks in practice,
    since a zone spans many municipalities). Only if the zone itself has zero
    stations for that pollutant (rare) does this fall back further to a
    nearest-station estimate (Tier 3, capped at `max_distance_km`).

    Every tier uses the same "pool daily values, take one median" statistic
    (see pooled_median()) - deliberately consistent with columns 1-3's
    existing methodology (pollution_columns_plan.md section 5), rather than a
    distance-weighted mean for the last tier, which would make that tier less
    robust to outlier days than the rest of the table for no good reason.

    Inferred values are NOT flagged as such anywhere in the output (a
    deliberate choice, not an oversight - see CLAUDE.md's Pollution columns
    section); `Station count` is unaffected by any of this and stays the
    literal own-station count.

    zone_ids / zone_stations: match_municipality_to_zones() /
        match_stations_to_zones() output, as used for columns 5-6.
    stations_gdf_utm: fetch_eea_stations() output, reprojected ONCE per
        country/run to a UTM CRS by the caller (see find_nearest_stations()).
    municipality_centroid_utm: this municipality's centroid, in the SAME UTM
        CRS as `stations_gdf_utm` - computed once per municipality by the
        caller, not per pollutant/tier.
    """
    stats = compute_own_station_pollution_stats(muni_stations, daily_means_by_pollutant)

    for pollutant, col in POLLUTION_MEDIAN_COLUMNS:
        if not pd.isna(stats[col]):
            continue
        daily = daily_means_by_pollutant.get(pollutant)

        zone_points = sampling_points_in_zones(zone_stations, zone_ids, pollutant)
        estimate = pooled_median(zone_points, daily)

        if pd.isna(estimate):
            nearest = find_nearest_stations(
                stations_gdf_utm, pollutant, municipality_centroid_utm, k_nearest, max_distance_km
            )
            estimate = pooled_median(nearest['samplingpoint'], daily)

        stats[col] = estimate

    return stats
