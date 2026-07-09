"""
Bicycle-parking and bike-service amenity counts for the country-level
screening pipeline (cycling_analysis/country.py's run_country_analysis()).

Uses pyrosm.OSM.get_pois() rather than get_network() - these are point/area
POI features (amenity=*, shop=bicycle), not road ways, so they need a
separate extraction call against the same bbox-clipped temp PBF the pipeline
already produces. No change to the extraction step itself is needed:
osmium extract preserves all OSM element types (nodes/ways/relations)
within the bbox, not just highway ways.
"""

import pandas as pd

BIKE_AMENITY_FILTER = {
    'amenity': ['bicycle_parking', 'bicycle_rental', 'bicycle_repair_station'],
    'shop': ['bicycle'],
}
BIKE_AMENITY_EXTRA_ATTRIBUTES = ['amenity', 'shop', 'capacity', 'covered', 'bicycle_parking']

# bicycle_parking=* sub-tag values that imply the spot is covered even when
# no explicit covered=yes tag is present (a shed/locker/building is
# inherently covered).
COVERED_BICYCLE_PARKING_VALUES = {'shed', 'lockers', 'building'}

AMENITY_METRIC_COLUMNS = [
    'Total parking spots',
    'Total parking capacity',
    'Covered parking spots',
    'Covered parking capacity',
    'Bike shops / rental / repair stations',
]


def _norm_str_col(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([''] * len(df), index=df.index)
    return df[col].astype(str).str.lower().str.strip().replace({'nan': '', 'none': ''})


def compute_bike_amenity_stats(pois) -> dict:
    """
    pois: the GeoDataFrame returned by OSM.get_pois(custom_filter=
    BIKE_AMENITY_FILTER, extra_attributes=BIKE_AMENITY_EXTRA_ATTRIBUTES), or
    None (pyrosm returns None with a UserWarning - not an empty frame - when
    a bbox has no matching POIs at all).

    Total parking spots counts LOCATIONS (one row = one spot), regardless of
    how many bikes that location holds. Total/covered parking capacity sum
    the capacity tag separately - most locations lack a capacity tag, so
    that number is a known undercount.
    """
    if pois is None or len(pois) == 0:
        return {c: 0 for c in AMENITY_METRIC_COLUMNS}

    amenity = _norm_str_col(pois, 'amenity')
    shop = _norm_str_col(pois, 'shop')
    parking = pois[amenity == 'bicycle_parking']

    capacity_col = parking['capacity'] if 'capacity' in parking.columns else pd.Series(float('nan'), index=parking.index)
    capacity = pd.to_numeric(capacity_col, errors='coerce')
    covered_mask = (
        _norm_str_col(parking, 'covered').eq('yes')
        | _norm_str_col(parking, 'bicycle_parking').isin(COVERED_BICYCLE_PARKING_VALUES)
    )

    return {
        'Total parking spots': int(len(parking)),
        'Total parking capacity': int(capacity.fillna(0).sum()),
        'Covered parking spots': int(covered_mask.sum()),
        'Covered parking capacity': int(capacity[covered_mask].fillna(0).sum()),
        'Bike shops / rental / repair stations': int(
            (shop == 'bicycle').sum()
            + amenity.isin(['bicycle_rental', 'bicycle_repair_station']).sum()
        ),
    }
