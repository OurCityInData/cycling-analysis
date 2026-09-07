"""
Ground-truth check for run_country_analysis()'s 'Municipality area (km²)'
column (cycling_analysis/country.py) - re-runs the exact same computation
(load_gadm_municipalities() + reproject to UTM + polygon area) against the
locally cached GADM Netherlands level-2 boundaries, and compares the result
per Zeeland municipality against real-world reference figures.

CAVEAT - GADM tracks LAND area, not "total area":
Official Dutch municipality area figures (CBS, quoted on Wikipedia infoboxes)
are published as land + water, and several Zeeland municipalities have water
area (their share of the Westerschelde/Oosterschelde estuaries) larger than
their land area - e.g. Vlissingen is ~34 km² land but ~345 km² land+water.
GADM's polygon for a Dutch gemeente tracks the onshore/land boundary (plus
some incidental inland water), not the full maritime jurisdiction. So the
ground truth below is each municipality's LAND area specifically - comparing
against the "total area" most search results surface first would be wrong by
2-10x for this province. See CLAUDE.md's Known gaps section.

Ground truth source: Wikipedia infobox area figures (CBS-sourced), fetched
2026-07-15. URLs are per-municipality below.

Expect real per-municipality deltas up to ~8%: GADM's coastline/tidal-flat
digitization doesn't exactly match CBS's official land/water classification
(e.g. how embanked polders, harbors, and inlets are drawn). This is boundary-
drawing noise, not a bug - it mostly cancels out in aggregate (see the
aggregate test below), so a generous per-municipality tolerance is correct
here; a tight one would just make this test flaky against redrawn GADM
releases without catching any real regression.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cycling_analysis.country import GADM_CONFIG, UTM_CRS_BY_COUNTRY, load_gadm_municipalities

DATA_DIR = Path(os.path.join(os.path.dirname(__file__), '..', 'data'))

# (land area km², source URL)
ZEELAND_LAND_AREA_KM2 = {
    'Borsele':            (141.57, 'https://en.wikipedia.org/wiki/Borsele'),
    'Goes':               (92.58,  'https://en.wikipedia.org/wiki/Goes'),
    'Hulst':              (201.71, 'https://en.wikipedia.org/wiki/Hulst'),
    'Kapelle':            (37.13,  'https://en.wikipedia.org/wiki/Kapelle'),
    'Middelburg':         (48.42,  'https://en.wikipedia.org/wiki/Middelburg,_Zeeland'),
    'Noord-Beveland':     (85.96,  'https://en.wikipedia.org/wiki/Noord-Beveland'),
    'Reimerswaal':        (101.80, 'https://en.wikipedia.org/wiki/Reimerswaal_(municipality)'),
    'Schouwen-Duiveland': (229.65, 'https://en.wikipedia.org/wiki/Schouwen-Duiveland'),
    'Sluis':              (279.36, 'https://en.wikipedia.org/wiki/Sluis'),
    'Terneuzen':          (250.38, 'https://en.wikipedia.org/wiki/Terneuzen'),
    'Tholen':             (146.71, 'https://en.wikipedia.org/wiki/Tholen_(municipality)'),
    'Veere':              (132.56, 'https://en.wikipedia.org/wiki/Veere'),
    'Vlissingen':         (34.31,  'https://en.wikipedia.org/wiki/Vlissingen'),
}

# Per-municipality boundary-digitization noise (see module docstring); this
# is intentionally loose. Aggregate accuracy is checked separately, tightly.
PER_MUNICIPALITY_TOLERANCE_PCT = 10

# GADM total vs. summed ground-truth land area should agree closely - random
# per-municipality boundary noise mostly cancels out in the sum.
AGGREGATE_TOLERANCE_PCT = 3

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / 'gadm41_NLD_2.geojson').exists(),
    reason='requires cached data/gadm41_NLD_2.geojson (see CLAUDE.md Setup)',
)


@pytest.fixture(scope='module')
def zeeland_computed_areas():
    """Municipality name -> GADM-computed area_km2, via the production code path."""
    gdf = load_gadm_municipalities('netherlands', DATA_DIR)
    utm_crs = UTM_CRS_BY_COUNTRY['netherlands']
    gdf['area_km2'] = gdf.to_crs(utm_crs).geometry.area.div(1e6)
    muni_col = GADM_CONFIG['netherlands']['muni_col']
    prov_col = GADM_CONFIG['netherlands']['province_col']
    zeeland = gdf[gdf[prov_col] == 'Zeeland']
    return dict(zip(zeeland[muni_col], zeeland['area_km2']))


@pytest.mark.parametrize('municipality', sorted(ZEELAND_LAND_AREA_KM2))
def test_municipality_area_matches_land_area_ground_truth(zeeland_computed_areas, municipality):
    assert municipality in zeeland_computed_areas, (
        f'{municipality} not found in GADM Netherlands level-2 data - '
        f'name mismatch between REGIONS config and GADM NAME_2 values?'
    )
    expected_km2, source = ZEELAND_LAND_AREA_KM2[municipality]
    got_km2 = zeeland_computed_areas[municipality]
    pct_diff = abs(got_km2 - expected_km2) / expected_km2 * 100
    assert pct_diff < PER_MUNICIPALITY_TOLERANCE_PCT, (
        f'{municipality}: GADM-computed area {got_km2:.1f} km² is {pct_diff:.1f}% off '
        f'from ground-truth land area {expected_km2} km² ({source})'
    )


def test_zeeland_total_area_matches_aggregate_ground_truth(zeeland_computed_areas):
    computed_total = sum(zeeland_computed_areas[m] for m in ZEELAND_LAND_AREA_KM2)
    expected_total = sum(km2 for km2, _ in ZEELAND_LAND_AREA_KM2.values())
    pct_diff = abs(computed_total - expected_total) / expected_total * 100
    assert pct_diff < AGGREGATE_TOLERANCE_PCT, (
        f'Zeeland total: GADM-computed {computed_total:.1f} km² is {pct_diff:.1f}% off '
        f'from ground-truth land area total {expected_total:.1f} km²'
    )
