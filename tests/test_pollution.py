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
    compute_daily_max_hourly,
    compute_daily_means,
    compute_own_station_pollution_stats,
    compute_zone_pollution_stats,
    estimate_own_pollution_stats,
    find_nearest_stations,
    match_municipality_to_zones,
    match_stations_to_municipality,
    match_stations_to_zones,
    pooled_median,
    sampling_points_in_zones,
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


def test_compute_daily_max_hourly_takes_max_not_mean():
    df = pd.DataFrame([
        {'Samplingpoint': 'ES/SP_A', 'Start': datetime(2025, 1, 1, 3), 'Value': 10.0},
        {'Samplingpoint': 'ES/SP_A', 'Start': datetime(2025, 1, 1, 15), 'Value': 250.0},
    ])
    result = compute_daily_max_hourly(df).set_index(['Samplingpoint', 'date'])['daily_max']
    assert result[('ES/SP_A', datetime(2025, 1, 1).date())] == 250.0


def test_match_stations_to_zones_point_in_polygon():
    zone_a = Polygon([(0, 0), (0, 10), (10, 10), (10, 0)])
    zone_b = Polygon([(20, 20), (20, 30), (30, 30), (30, 20)])
    zones = gpd.GeoDataFrame({'zone_id': ['ZON_A', 'ZON_B']}, geometry=[zone_a, zone_b], crs='EPSG:4326')
    stations = gpd.GeoDataFrame(
        {'station_code': ['in_a', 'in_b', 'unmatched'], 'pollutant': ['PM10', 'PM10', 'PM10'],
         'samplingpoint': ['ES/SP_1', 'ES/SP_2', 'ES/SP_3']},
        geometry=[Point(5, 5), Point(25, 25), Point(100, 100)],
        crs='EPSG:4326',
    )
    joined = match_stations_to_zones(stations, zones)
    zone_by_code = joined.set_index('station_code')['zone_id']
    assert zone_by_code['in_a'] == 'ZON_A'
    assert zone_by_code['in_b'] == 'ZON_B'
    assert pd.isna(zone_by_code['unmatched'])


def test_match_municipality_to_zones_intersects_multiple():
    # Municipality straddling the boundary between two zones should match both.
    zone_a = Polygon([(0, 0), (0, 10), (10, 10), (10, 0)])
    zone_b = Polygon([(10, 0), (10, 10), (20, 10), (20, 0)])
    zones = gpd.GeoDataFrame({'zone_id': ['ZON_A', 'ZON_B']}, geometry=[zone_a, zone_b], crs='EPSG:4326')
    muni = Polygon([(5, 0), (5, 10), (15, 10), (15, 0)])
    matched = match_municipality_to_zones(zones, muni)
    assert set(matched) == {'ZON_A', 'ZON_B'}


def _zone_stations(rows):
    df = pd.DataFrame(rows, columns=['station_code', 'samplingpoint', 'pollutant', 'zone_id'])
    return gpd.GeoDataFrame(df, geometry=[Point(0, 0)] * len(rows), crs='EPSG:4326')


def test_zone_pollution_stats_blank_when_no_zone_match():
    stats = compute_zone_pollution_stats([], _zone_stations([]), {}, {})
    assert all(math.isnan(v) for v in stats.values())


def test_zone_pollution_stats_worst_station_governs():
    # Two PM10 stations in the zone: one with 1 exceedance day, one with 2.
    # The zone's reported count should be the worse (2), not a sum/average.
    zone_stations = _zone_stations([
        {'station_code': 'A', 'samplingpoint': 'ES/SP_1', 'pollutant': 'PM10', 'zone_id': 'ZON_X'},
        {'station_code': 'B', 'samplingpoint': 'ES/SP_2', 'pollutant': 'PM10', 'zone_id': 'ZON_X'},
    ])
    daily_means = {'PM10': pd.DataFrame([
        {'Samplingpoint': 'ES/SP_1', 'date': datetime(2025, 1, 1).date(), 'daily_mean': 60.0},  # fails (>50)
        {'Samplingpoint': 'ES/SP_1', 'date': datetime(2025, 1, 2).date(), 'daily_mean': 10.0},
        {'Samplingpoint': 'ES/SP_2', 'date': datetime(2025, 1, 1).date(), 'daily_mean': 60.0},  # fails
        {'Samplingpoint': 'ES/SP_2', 'date': datetime(2025, 1, 2).date(), 'daily_mean': 55.0},  # fails
    ])}
    stats = compute_zone_pollution_stats(['ZON_X'], zone_stations, daily_means, {})
    assert stats['Days failing Spain/EU PM10 limit'] == 2
    assert stats['Days failing Spain/EU PM10 limit'] != 3  # not summed across stations


def test_zone_pollution_stats_no2_uses_daily_max_not_mean():
    zone_stations = _zone_stations([
        {'station_code': 'A', 'samplingpoint': 'ES/SP_1', 'pollutant': 'NO2', 'zone_id': 'ZON_X'},
    ])
    # Daily mean is under 200, but the day should still fail via daily max.
    daily_max = {'NO2': pd.DataFrame([
        {'Samplingpoint': 'ES/SP_1', 'date': datetime(2025, 1, 1).date(), 'daily_max': 250.0},
    ])}
    stats = compute_zone_pollution_stats(['ZON_X'], zone_stations, {}, daily_max)
    assert stats['Days failing Spain/EU NO2 limit'] == 1


def test_zone_pollution_stats_blank_when_pollutant_not_measured_in_zone():
    zone_stations = _zone_stations([
        {'station_code': 'A', 'samplingpoint': 'ES/SP_1', 'pollutant': 'PM10', 'zone_id': 'ZON_X'},
    ])
    daily_means = {'PM10': pd.DataFrame([
        {'Samplingpoint': 'ES/SP_1', 'date': datetime(2025, 1, 1).date(), 'daily_mean': 60.0},
    ])}
    stats = compute_zone_pollution_stats(['ZON_X'], zone_stations, daily_means, {})
    assert stats['Days failing Spain/EU PM10 limit'] == 1
    assert math.isnan(stats['Days failing Spain/EU NO2 limit'])
    assert math.isnan(stats['Days failing WHO NO2 guideline'])


def test_pooled_median_blank_when_no_sampling_points_or_daily_data():
    empty_points = pd.Series([], dtype=object)
    daily = pd.DataFrame({'Samplingpoint': [], 'daily_mean': []})
    assert math.isnan(pooled_median(empty_points, daily))
    assert math.isnan(pooled_median(pd.Series(['ES/SP_1']), None))


def test_pooled_median_pools_across_sampling_points():
    daily = pd.DataFrame([
        {'Samplingpoint': 'ES/SP_1', 'daily_mean': 10.0},
        {'Samplingpoint': 'ES/SP_2', 'daily_mean': 100.0},
    ])
    assert pooled_median(pd.Series(['ES/SP_1', 'ES/SP_2']), daily) == 55.0


def test_sampling_points_in_zones_empty_when_no_zone_ids():
    zone_stations = _zone_stations([
        {'station_code': 'A', 'samplingpoint': 'ES/SP_1', 'pollutant': 'PM10', 'zone_id': 'ZON_X'},
    ])
    assert sampling_points_in_zones(zone_stations, [], 'PM10').empty


def test_sampling_points_in_zones_filters_by_pollutant_and_zone():
    zone_stations = _zone_stations([
        {'station_code': 'A', 'samplingpoint': 'ES/SP_1', 'pollutant': 'PM10', 'zone_id': 'ZON_X'},
        {'station_code': 'B', 'samplingpoint': 'ES/SP_2', 'pollutant': 'NO2', 'zone_id': 'ZON_X'},
        {'station_code': 'C', 'samplingpoint': 'ES/SP_3', 'pollutant': 'PM10', 'zone_id': 'ZON_Y'},
    ])
    result = sampling_points_in_zones(zone_stations, ['ZON_X'], 'PM10')
    assert list(result) == ['ES/SP_1']


def _utm_stations(rows):
    df = pd.DataFrame(rows, columns=['station_code', 'samplingpoint', 'pollutant', 'x', 'y'])
    geometry = [Point(x, y) for x, y in zip(df['x'], df['y'])]
    return gpd.GeoDataFrame(df.drop(columns=['x', 'y']), geometry=geometry, crs='EPSG:32630')


def test_find_nearest_stations_picks_k_nearest_within_radius_and_pollutant():
    stations = _utm_stations([
        {'station_code': 'near1', 'samplingpoint': 'ES/SP_1', 'pollutant': 'PM10', 'x': 500000, 'y': 4500000},
        {'station_code': 'near2', 'samplingpoint': 'ES/SP_2', 'pollutant': 'PM10', 'x': 505000, 'y': 4500000},  # 5km
        {'station_code': 'far', 'samplingpoint': 'ES/SP_3', 'pollutant': 'PM10', 'x': 700000, 'y': 4500000},  # 200km
        {'station_code': 'wrong_pollutant', 'samplingpoint': 'ES/SP_4', 'pollutant': 'NO2', 'x': 500100, 'y': 4500000},
    ])
    point = Point(500000, 4500000)
    nearest = find_nearest_stations(stations, 'PM10', point, k=3, max_distance_km=50)
    assert set(nearest['station_code']) == {'near1', 'near2'}


def test_find_nearest_stations_empty_when_none_within_radius():
    stations = _utm_stations([
        {'station_code': 'far', 'samplingpoint': 'ES/SP_1', 'pollutant': 'PM10', 'x': 700000, 'y': 4500000},
    ])
    point = Point(500000, 4500000)
    assert find_nearest_stations(stations, 'PM10', point, k=3, max_distance_km=50).empty


def test_estimate_own_pollution_stats_uses_own_station_when_present():
    # Own station AND zone both have data - own must win, not zone.
    muni_stations = _muni_stations([
        {'station_code': 'OWN', 'samplingpoint': 'ES/SP_OWN', 'pollutant': 'PM10'},
    ])
    daily_means = {'PM10': pd.DataFrame([
        {'Samplingpoint': 'ES/SP_OWN', 'date': datetime(2025, 1, 1).date(), 'daily_mean': 12.0},
        {'Samplingpoint': 'ES/SP_ZONE', 'date': datetime(2025, 1, 1).date(), 'daily_mean': 99.0},
    ])}
    zone_stations = _zone_stations([
        {'station_code': 'ZONE', 'samplingpoint': 'ES/SP_ZONE', 'pollutant': 'PM10', 'zone_id': 'ZON_X'},
    ])
    stats = estimate_own_pollution_stats(
        muni_stations, ['ZON_X'], zone_stations, daily_means, _utm_stations([]), Point(0, 0),
    )
    assert stats['PM10 median annual (µg/m³)'] == 12.0


def test_estimate_own_pollution_stats_falls_back_to_zone_median():
    muni_stations = _muni_stations([])  # no own station
    daily_means = {'PM10': pd.DataFrame([
        {'Samplingpoint': 'ES/SP_ZONE', 'date': datetime(2025, 1, 1).date(), 'daily_mean': 20.0},
    ])}
    zone_stations = _zone_stations([
        {'station_code': 'ZONE', 'samplingpoint': 'ES/SP_ZONE', 'pollutant': 'PM10', 'zone_id': 'ZON_X'},
    ])
    stats = estimate_own_pollution_stats(
        muni_stations, ['ZON_X'], zone_stations, daily_means, _utm_stations([]), Point(0, 0),
    )
    assert stats['PM10 median annual (µg/m³)'] == 20.0


def test_estimate_own_pollution_stats_falls_back_to_nearest_station_when_zone_empty():
    muni_stations = _muni_stations([])
    zone_stations = _zone_stations([])  # zone has no station for this pollutant
    nearest_stations = _utm_stations([
        {'station_code': 'NEAR', 'samplingpoint': 'ES/SP_NEAR', 'pollutant': 'PM10', 'x': 500000, 'y': 4500000},
    ])
    daily_means = {'PM10': pd.DataFrame([
        {'Samplingpoint': 'ES/SP_NEAR', 'date': datetime(2025, 1, 1).date(), 'daily_mean': 30.0},
    ])}
    stats = estimate_own_pollution_stats(
        muni_stations, ['ZON_X'], zone_stations, daily_means, nearest_stations, Point(500000, 4500000),
    )
    assert stats['PM10 median annual (µg/m³)'] == 30.0


def test_estimate_own_pollution_stats_blank_when_nothing_in_range():
    muni_stations = _muni_stations([])
    zone_stations = _zone_stations([])
    far_stations = _utm_stations([
        {'station_code': 'FAR', 'samplingpoint': 'ES/SP_FAR', 'pollutant': 'PM10', 'x': 900000, 'y': 4500000},
    ])
    daily_means = {'PM10': pd.DataFrame([
        {'Samplingpoint': 'ES/SP_FAR', 'date': datetime(2025, 1, 1).date(), 'daily_mean': 30.0},
    ])}
    stats = estimate_own_pollution_stats(
        muni_stations, ['ZON_X'], zone_stations, daily_means, far_stations, Point(500000, 4500000),
        max_distance_km=50,
    )
    assert math.isnan(stats['PM10 median annual (µg/m³)'])
