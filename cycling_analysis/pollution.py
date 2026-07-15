"""
Air-quality (pollution) columns for the country-level screening pipeline
(cycling_analysis/country.py's run_country_analysis()). See
pollution_columns_plan.md for the full column list and methodology;
this module currently implements columns 1-4 only (own-municipality
station stats) - the zone-based legal-compliance columns (5-6) are a
later phase.

Spain-only for now (see CLAUDE.md's Known gaps). Source: EEA Air Quality
Download API (https://eeadmz1-downloads-api-appservice.azurewebsites.net),
Up-To-Date/E2a (unverified) dataset - Spain's verified E1a dataset for 2025
isn't published until ~Sept/Oct 2026 (RD 102/2011's 9-month reporting lag).

Empirically verified against the live API (2026-07): the E2a dataset only
carries AggType='hour' rows via ParquetFile/urls - requesting
aggregationType='day' returns zero files, despite the "Type" filter's web-UI
description implying daily aggregates exist for every dataset. This module
therefore always fetches hourly data and computes daily means itself, which
is what columns 1-3's "median across days" definition needs anyway.

Station coordinates are NOT in the measurement parquet files - they come
from a separate EEA-wide metadata CSV
(https://discomap.eea.europa.eu/map/fme/metadata/PanEuropean_metadata.csv,
~27MB, one row per station+pollutant "sampling point", shared across every
country/pollutant so it's cached once regardless of which country calls
fetch_eea_stations()). Its SamplingPoint column (e.g. 'SP_38048001_10_49')
lacks the country prefix that the measurement parquet's Samplingpoint column
carries (e.g. 'ES/SP_38048001_10_49') - fetch_eea_stations() adds it back so
the two are joinable.
"""

import io
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

EEA_API_BASE = 'https://eeadmz1-downloads-api-appservice.azurewebsites.net/'
EEA_METADATA_CSV_URL = 'https://discomap.eea.europa.eu/map/fme/metadata/PanEuropean_metadata.csv'

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

POLLUTION_COLUMNS = [
    'PM2.5 median annual (µg/m³)',
    'PM10 median annual (µg/m³)',
    'NO2 median annual (µg/m³)',
    'Station count',
]


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

    Median is taken across the POOLED daily values of every station in the
    municipality that measures that pollutant, not a median of per-station
    medians - see pollution_columns_plan.md section 5. Blank (NaN) if the
    municipality has no station for that pollutant.
    """
    stats = {c: float('nan') for c in POLLUTION_COLUMNS}
    stats['Station count'] = muni_stations['station_code'].nunique()

    for pollutant, col in [
        ('PM2.5', 'PM2.5 median annual (µg/m³)'),
        ('PM10', 'PM10 median annual (µg/m³)'),
        ('NO2', 'NO2 median annual (µg/m³)'),
    ]:
        sampling_points = muni_stations.loc[muni_stations['pollutant'] == pollutant, 'samplingpoint']
        if sampling_points.empty:
            continue
        daily = daily_means_by_pollutant.get(pollutant)
        if daily is None or daily.empty:
            continue
        pooled = daily.loc[daily['Samplingpoint'].isin(sampling_points), 'daily_mean']
        if not pooled.empty:
            stats[col] = round(pooled.median(), 1)

    return stats
