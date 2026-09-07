"""Unit tests for compute_bike_amenity_stats() - no PBF/network I/O."""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cycling_analysis.bike_amenities import compute_bike_amenity_stats


def test_none_input_returns_all_zero():
    stats = compute_bike_amenity_stats(None)
    assert stats == {
        'Total parking spots': 0,
        'Total parking capacity': 0,
        'Covered parking spots': 0,
        'Covered parking capacity': 0,
        'Bike shops': 0,
        'Bike rental stations': 0,
        'Bike repair stations': 0,
    }


def test_empty_dataframe_returns_all_zero():
    empty = pd.DataFrame(columns=['amenity', 'shop', 'capacity', 'covered', 'bicycle_parking'])
    stats = compute_bike_amenity_stats(empty)
    assert all(v == 0 for v in stats.values())


def test_mixed_rows():
    df = pd.DataFrame([
        {'amenity': 'bicycle_parking', 'covered': 'yes', 'capacity': '10', 'bicycle_parking': 'stands'},
        {'amenity': 'bicycle_parking', 'covered': None, 'capacity': None, 'bicycle_parking': 'stands'},
        {'amenity': 'bicycle_parking', 'covered': None, 'capacity': '4', 'bicycle_parking': 'shed'},
        {'amenity': None, 'shop': 'bicycle'},
        {'amenity': 'bicycle_rental'},
        {'amenity': 'bicycle_repair_station'},
    ])
    stats = compute_bike_amenity_stats(df)

    assert stats['Total parking spots'] == 3
    assert stats['Total parking capacity'] == 14  # 10 + 0 (missing) + 4
    assert stats['Covered parking spots'] == 2  # covered=yes row + bicycle_parking=shed row
    assert stats['Covered parking capacity'] == 14  # 10 (covered=yes) + 4 (shed)
    assert stats['Bike shops'] == 1
    assert stats['Bike rental stations'] == 1
    assert stats['Bike repair stations'] == 1


def test_garbage_capacity_value_coerced_to_zero_not_crash():
    df = pd.DataFrame([
        {'amenity': 'bicycle_parking', 'capacity': 'not-a-number', 'covered': 'no'},
    ])
    stats = compute_bike_amenity_stats(df)
    assert stats['Total parking spots'] == 1
    assert stats['Total parking capacity'] == 0
    assert stats['Covered parking spots'] == 0


def test_missing_optional_columns_dont_crash():
    # No 'capacity', 'covered', 'bicycle_parking', or 'shop' columns at all.
    df = pd.DataFrame([{'amenity': 'bicycle_parking'}])
    stats = compute_bike_amenity_stats(df)
    assert stats['Total parking spots'] == 1
    assert stats['Total parking capacity'] == 0
    assert stats['Covered parking spots'] == 0
    assert stats['Bike shops'] == 0
    assert stats['Bike rental stations'] == 0
    assert stats['Bike repair stations'] == 0
