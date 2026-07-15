"""
Unit tests for the pure-computation parts of cycling_analysis/pollution.py -
no network I/O (fetch_eea_stations/fetch_eea_measurements hit the live EEA
API and are validated manually, same as the rest of the GIS pipeline - see
CLAUDE.md's Known gaps).
"""

import math
import os
import sys
from datetime import datetime

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, Polygon

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cycling_analysis.pollution import (
    compute_daily_means,
    compute_own_station_pollution_stats,
    match_stations_to_municipality,
)


def test_compute_daily_means_empty_input():
    empty = pd.DataFrame(columns=['Samplingpoint', 'Start', 'Value'])
    result = compute_daily_means(empty)
    assert list(result.columns) == ['Samplingpoint', 'date', 'daily_mean']
    assert len(result) == 0


def test_compute_daily_means_groups_by_station_and_date():
    df = pd.DataFrame([
        {'Samplingpoint': 'ES/SP_A', 'Start': datetime(2025, 1, 1, 3), 'Value': 10.0},
        {'Samplingpoint': 'ES/SP_A', 'Start': datetime(2025, 1, 1, 15), 'Value': 20.0},
        {'Samplingpoint': 'ES/SP_A', 'Start': datetime(2025, 1, 2, 3), 'Value': 100.0},
        {'Samplingpoint': 'ES/SP_B', 'Start': datetime(2025, 1, 1, 3), 'Value': 5.0},
    ])
    result = compute_daily_means(df).set_index(['Samplingpoint', 'date'])['daily_mean']

    assert result[('ES/SP_A', datetime(2025, 1, 1).date())] == 15.0  # mean(10, 20)
    assert result[('ES/SP_A', datetime(2025, 1, 2).date())] == 100.0
    assert result[('ES/SP_B', datetime(2025, 1, 1).date())] == 5.0


def _muni_stations(rows):
    df = pd.DataFrame(rows, columns=['station_code', 'samplingpoint', 'pollutant'])
    return gpd.GeoDataFrame(df, geometry=[Point(0, 0)] * len(rows), crs='EPSG:4326')


def test_own_station_stats_no_stations_is_all_blank():
    muni_stations = _muni_stations([])
    stats = compute_own_station_pollution_stats(muni_stations, {})
    assert stats['Station count'] == 0
    assert math.isnan(stats['PM2.5 median annual (µg/m³)'])
    assert math.isnan(stats['PM10 median annual (µg/m³)'])
    assert math.isnan(stats['NO2 median annual (µg/m³)'])


def test_own_station_stats_station_count_dedups_by_station_not_by_pollutant():
    # Same physical station reporting 2 pollutants counts once.
    muni_stations = _muni_stations([
        {'station_code': 'ES0001A', 'samplingpoint': 'ES/SP_1', 'pollutant': 'PM10'},
        {'station_code': 'ES0001A', 'samplingpoint': 'ES/SP_2', 'pollutant': 'NO2'},
        {'station_code': 'ES0002A', 'samplingpoint': 'ES/SP_3', 'pollutant': 'PM10'},
    ])
    stats = compute_own_station_pollution_stats(muni_stations, {})
    assert stats['Station count'] == 2


def test_own_station_stats_pools_daily_values_across_stations_before_median():
    # Two stations measuring PM10 in the same municipality: pooling their
    # daily values and taking one median should NOT equal the median of each
    # station's own median (pollution_columns_plan.md section 5) - this test
    # picks numbers where the two approaches would disagree if pooling were
    # broken.
    muni_stations = _muni_stations([
        {'station_code': 'ES0001A', 'samplingpoint': 'ES/SP_1', 'pollutant': 'PM10'},
        {'station_code': 'ES0002A', 'samplingpoint': 'ES/SP_2', 'pollutant': 'PM10'},
    ])
    daily_pm10 = pd.DataFrame([
        {'Samplingpoint': 'ES/SP_1', 'date': datetime(2025, 1, 1).date(), 'daily_mean': 10.0},
        {'Samplingpoint': 'ES/SP_1', 'date': datetime(2025, 1, 2).date(), 'daily_mean': 10.0},
        {'Samplingpoint': 'ES/SP_2', 'date': datetime(2025, 1, 1).date(), 'daily_mean': 100.0},
    ])
    stats = compute_own_station_pollution_stats(muni_stations, {'PM10': daily_pm10})

    pooled_values = [10.0, 10.0, 100.0]
    expected_pooled_median = sorted(pooled_values)[1]  # 10.0
    assert stats['PM10 median annual (µg/m³)'] == expected_pooled_median
    assert stats['PM10 median annual (µg/m³)'] != 55.0  # median of per-station medians (10, 100)


def test_own_station_stats_blank_when_pollutant_not_measured_here():
    muni_stations = _muni_stations([
        {'station_code': 'ES0001A', 'samplingpoint': 'ES/SP_1', 'pollutant': 'PM10'},
    ])
    daily_pm10 = pd.DataFrame([
        {'Samplingpoint': 'ES/SP_1', 'date': datetime(2025, 1, 1).date(), 'daily_mean': 10.0},
    ])
    stats = compute_own_station_pollution_stats(muni_stations, {'PM10': daily_pm10})
    assert stats['PM10 median annual (µg/m³)'] == 10.0
    assert math.isnan(stats['NO2 median annual (µg/m³)'])
    assert math.isnan(stats['PM2.5 median annual (µg/m³)'])


def test_match_stations_to_municipality_point_in_polygon():
    square = Polygon([(0, 0), (0, 10), (10, 10), (10, 0)])
    stations = gpd.GeoDataFrame(
        {'station_code': ['inside', 'outside']},
        geometry=[Point(5, 5), Point(50, 50)],
        crs='EPSG:4326',
    )
    matched = match_stations_to_municipality(stations, square)
    assert list(matched['station_code']) == ['inside']
