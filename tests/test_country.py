"""
Unit tests for the pure-computation parts of cycling_analysis/country.py - no
GIS/network I/O (the country-level GIS pipeline itself is validated manually
against real data, not unit-tested - see CLAUDE.md's Known gaps).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cycling_analysis.country import slugify_column


def test_slugify_column_basic_words():
    assert slugify_column('Region') == 'region'
    assert slugify_column('Municipality') == 'municipality'
    assert slugify_column('Total Cycling Path km') == 'total_cycling_path_km'


def test_slugify_column_strips_units_and_symbols():
    assert slugify_column('Municipality area (km²)') == 'municipality_area_km2'
    assert slugify_column('PM2.5 median annual (µg/m³)') == 'pm2_5_median_annual_ug_m3'
    assert slugify_column('Bike shops / rental / repair stations') == 'bike_shops_rental_repair_stations'


def test_slugify_column_leading_digit_gets_prefixed():
    # SQL generally rejects identifiers starting with a digit.
    assert slugify_column('2-1 road') == '_2_1_road'


def test_slugify_column_is_idempotent():
    once = slugify_column('Days failing Spain/EU PM2.5 limit')
    assert slugify_column(once) == once
