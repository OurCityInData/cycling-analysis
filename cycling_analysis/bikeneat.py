"""
BikeNEAT classification pipeline (Lukawska et al., 2026;
https://github.com/mirlu-tud/bikeneat), plus the loader that runs it
against a PBF extract.

This entire module was duplicated byte-for-byte (modulo comments) between
barcelona_vs_bikeneat.ipynb and freiburg_vs_bikeneat.ipynb — confirmed via
diff. Unlike classification.py's city-specific taxonomies, there is no
legitimate reason for two copies of this: the BikeNEAT algorithm itself is
country-agnostic (it works purely off generic OSM tags), so one shared
module + a per-city config (see scripts/compare_vs_bikeneat.py) replaces
both notebooks.
"""

import gc
import json

import geopandas as gpd
import pandas as pd
import pyrosm

from .constants import OSM_KEYS, OSM_KEYS_SET

# ── predicates (verbatim from BikeNEAT) ─────────────────────────────────────
is_segregated = lambda x: any(k for k, v in x.items() if 'segregated' in k and v == 'yes')
is_not_accessible = lambda x: x.get('access') == 'no'
use_sidepath = lambda x: any(k for k, v in x.items() if 'bicycle' in k and v == 'use_sidepath')
is_indoor = lambda x: x.get('indoor') == 'yes'
is_path = lambda x: x['highway'] in ['path']
is_track = lambda x: x['highway'] in ['track']
is_footway = lambda x: x['highway'] in ['footway', 'pedestrian']
can_walk_right = lambda x: (
    x.get('foot') in ['yes', 'designated']
    or any(k for k, v in x.items() if 'right:foot' in k and v in ['yes', 'designated'])
    or x.get('sidewalk') in ['yes', 'separated', 'both', 'right', 'left']
    or x.get('sidewalk:right') in ['yes', 'separated', 'both', 'right']
    or x.get('sidewalk:both') in ['yes', 'separated', 'both']
)
can_walk_left = lambda x: (
    x.get('foot') in ['yes', 'designated']
    or any(k for k, v in x.items() if 'left:foot' in k and v in ['yes', 'designated'])
    or x.get('sidewalk') in ['yes', 'separated', 'both', 'right', 'left']
    or x.get('sidewalk:left') in ['yes', 'separated', 'both', 'left']
    or x.get('sidewalk:both') in ['yes', 'separated', 'both']
)
can_bike = lambda x: x.get('bicycle') in ['yes', 'designated'] and x.get('highway') not in ['motorway', 'motorway_link']
cannot_bike = lambda x: (
    x.get('bicycle') in ['no', 'dismount', 'use_sidepath']
    or x.get('highway') in ['corridor', 'motorway', 'motorway_link', 'trunk', 'trunk_link']
    or x.get('access') in ['customers']
)
is_obligated_segregated = lambda x: (
    ('traffic_sign' in x and isinstance(x['traffic_sign'], str) and '241' in x['traffic_sign'])
    or ('traffic_sign:forward' in x and isinstance(x['traffic_sign:forward'], str) and '241' in x['traffic_sign:forward'])
)
is_obligated_shared = lambda x: (
    ('traffic_sign' in x and isinstance(x['traffic_sign'], str) and '240' in x['traffic_sign'])
    or ('traffic_sign:forward' in x and isinstance(x['traffic_sign:forward'], str) and '240' in x['traffic_sign:forward'])
)
_sign_free_bike = lambda x: (
    ('traffic_sign' in x and isinstance(x['traffic_sign'], str) and '1022-10' in x['traffic_sign'])
    or ('traffic_sign:forward' in x and isinstance(x['traffic_sign:forward'], str) and '1022-10' in x['traffic_sign:forward'])
)
_sign_buslane = lambda x: (
    ('traffic_sign' in x and isinstance(x['traffic_sign'], str) and '245' in x['traffic_sign'])
    or ('traffic_sign:forward' in x and isinstance(x['traffic_sign:forward'], str) and '245' in x['traffic_sign:forward'])
)
_sign_footway = lambda x: (
    ('traffic_sign' in x and isinstance(x['traffic_sign'], str) and ('239' in x['traffic_sign'] or '242.1' in x['traffic_sign']))
    or ('traffic_sign:forward' in x and isinstance(x['traffic_sign:forward'], str) and ('239' in x['traffic_sign:forward'] or '242.1' in x['traffic_sign:forward']))
)
is_sign_shared_way = lambda x: _sign_footway(x) and _sign_free_bike(x)
is_sign_shared_buslane = lambda x: _sign_buslane(x) and _sign_free_bike(x)
is_designated = lambda x: x.get('bicycle') == 'designated'
is_bicycle_designated_left = lambda x: (
    is_designated(x) or x.get('cycleway:left:bicycle') == 'designated'
    or x.get('cycleway:bicycle') == 'designated'
)
is_bicycle_designated_right = lambda x: (
    is_designated(x) or x.get('cycleway:right:bicycle') == 'designated'
    or x.get('cycleway:bicycle') == 'designated'
)
is_pedestrian_designated_left = lambda x: (
    x.get('foot') == 'designated' or x.get('sidewalk:left:foot') == 'designated'
    or x.get('sidewalk:foot') == 'designated'
)
is_pedestrian_designated_right = lambda x: (
    x.get('foot') == 'designated' or x.get('sidewalk:right:foot') == 'designated'
    or x.get('sidewalk:foot') == 'designated'
)
is_agricultural = lambda x: x.get('motor_vehicle') in ['agricultural', 'forestry']
is_accessible = lambda x: pd.isnull(x['access']) or not is_not_accessible(x)
is_tram = lambda x: x['tram'] == 'yes'
is_smooth = lambda x: pd.isnull(x['tracktype']) or x['tracktype'] in ['grade1', 'grade2']
is_vehicle_allowed = lambda x: pd.isnull(x.get('motor_vehicle')) or x.get('motor_vehicle') != 'no'
is_service_tag = lambda x: x['highway'] in ['service']
is_service = lambda x: (
    is_service_tag(x)
    or (is_agricultural(x) and is_accessible(x))
    or (is_path(x) and is_accessible(x))
    or (is_track(x) and is_accessible(x) and is_smooth(x) and is_vehicle_allowed(x))
) and (not is_designated(x))
can_cardrive = lambda x: x['highway'] in [
    'motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'unclassified',
    'road', 'residential', 'living_street', 'primary_link', 'secondary_link',
    'tertiary_link', 'motorway_link', 'trunk_link',
]
is_path_not_forbidden = lambda x: x['highway'] in ['cycleway', 'track', 'path'] and not cannot_bike(x)
is_bikepath_right = lambda x: (
    x.get('highway') in ['cycleway']
    or (any(k for k, v in x.items() if 'right:bicycle' in k and v == 'designated')
        and not any(k for k in x if k == 'cycleway:right:lane'))
    or x.get('cycleway') in ['track', 'sidepath', 'crossing']
    or x.get('cycleway:right') in ['track', 'sidepath', 'crossing']
    or x.get('cycleway:both') in ['track', 'sidepath', 'crossing']
    or any(k for k, v in x.items() if 'right:traffic_sign' in k and v == '237')
)
is_bikepath_left = lambda x: (
    x.get('highway') in ['cycleway']
    or (any(k for k, v in x.items() if 'left:bicycle' in k and v == 'designated')
        and not any(k for k in x if k == 'cycleway:left:lane'))
    or x.get('cycleway') in ['track', 'sidepath', 'crossing']
    or x.get('cycleway:left') in ['track', 'sidepath', 'crossing']
    or x.get('cycleway:both') in ['track', 'sidepath', 'crossing']
    or any(k for k, v in x.items() if 'left:traffic_sign' in k and v == '237')
)
is_pedestrian_right = lambda x: (
    (is_footway(x) and not can_bike(x) and not is_indoor(x))
    or (is_path(x) and can_walk_right(x) and not can_bike(x) and not is_indoor(x))
)
is_pedestrian_left = lambda x: (
    (is_footway(x) and not can_bike(x) and not is_indoor(x))
    or (is_path(x) and can_walk_left(x) and not can_bike(x) and not is_indoor(x))
)
is_shared_with_mit_right = lambda x: (
    x.get('cycleway') in ['shared_lane']
    or x.get('cycleway:right') in ['shared_lane']
    or x.get('cycleway:both') in ['shared_lane']
)
is_shared_with_mit_left = lambda x: (
    x.get('cycleway') in ['shared_lane']
    or x.get('cycleway:left') in ['shared_lane']
    or x.get('cycleway:both') in ['shared_lane']
)
is_cycle_highway = lambda x: (
    x.get('cycle_highway') == 'yes'
    or x.get('traffic_sign') == '350.1'
    or x.get('traffic_sign:forward') == '350.1'
)
is_bikeroad = lambda x: (
    x.get('bicycle_road') == 'yes'
    or x.get('cyclestreet') == 'yes'
    or x.get('traffic_sign') in ['244.1', '244.3']
    or x.get('traffic_sign:forward') in ['244.1', '244.3']
)
is_bikelane_right = lambda x: (
    x.get('cycleway') in ['lane']
    or x.get('cycleway:right') in ['lane']
    or x.get('cycleway:both') in ['lane']
    or any(k for k, v in x.items() if 'right:lane' in k and v == 'exclusive')
    or any(k for k, v in x.items() if 'right:traffic_sign' in k and v == '237')
)
is_bikelane_left = lambda x: (
    x.get('cycleway') in ['lane']
    or x.get('cycleway:left') in ['lane']
    or x.get('cycleway:both') in ['lane']
    or any(k for k, v in x.items() if 'left:lane' in k and v == 'exclusive')
    or any(k for k, v in x.items() if 'left:traffic_sign' in k and v == '237')
)
is_shared_buslane_right = lambda x: (
    x.get('cycleway') == 'share_busway'
    or x.get('cycleway:right') == 'share_busway'
    or x.get('cycleway:both') == 'share_busway'
    or is_sign_shared_buslane(x)
)
is_shared_buslane_left = lambda x: (
    x.get('cycleway') == 'share_busway'
    or x.get('cycleway:left') == 'share_busway'
    or x.get('cycleway:both') == 'share_busway'
    or is_sign_shared_buslane(x)
)


def set_value(x, single=False):
    """BikeNEAT classification function - verbatim from Lukawska et al. (2026)."""
    if not isinstance(x, dict):
        raise TypeError('rowwise OSM data should be a dict')

    conditions_b_way_right = [
        is_bikepath_right(x) and not can_walk_right(x),
        is_bikepath_right(x) and is_segregated(x),
        can_bike(x) and (is_path(x) or is_track(x)) and not can_walk_right(x),
        can_bike(x) and (is_track(x) or is_footway(x) or is_path(x)) and is_segregated(x),
        is_obligated_segregated(x),
        is_bicycle_designated_right(x) and is_pedestrian_designated_right(x) and is_segregated(x),
    ]
    conditions_b_way_left = [
        is_bikepath_left(x) and not can_walk_left(x),
        is_bikepath_left(x) and is_segregated(x),
        can_bike(x) and (is_path(x) or is_track(x)) and not can_walk_left(x),
        can_bike(x) and (is_track(x) or is_footway(x) or is_path(x)) and is_segregated(x),
        is_obligated_segregated(x),
        is_bicycle_designated_left(x) and is_pedestrian_designated_left(x) and is_segregated(x),
    ]
    conditions_shared_right = [
        is_bikepath_right(x) and can_walk_right(x) and not is_segregated(x),
        is_footway(x) and can_bike(x) and not is_segregated(x),
        (is_path(x) or is_track(x)) and can_bike(x) and can_walk_right(x) and not is_segregated(x),
        is_sign_shared_way(x),
        is_obligated_shared(x),
    ]
    conditions_shared_left = [
        is_bikepath_left(x) and can_walk_left(x) and not is_segregated(x),
        is_footway(x) and can_bike(x) and not is_segregated(x),
        (is_path(x) or is_track(x)) and can_bike(x) and can_walk_left(x) and not is_segregated(x),
        is_sign_shared_way(x),
        is_obligated_shared(x),
    ]
    conditions_mit_right = [
        can_cardrive(x) and not is_bikepath_right(x) and not is_bikeroad(x)
        and not is_footway(x) and not is_bikelane_right(x)
        and not is_shared_buslane_right(x) and not is_path(x)
        and not is_track(x) and not cannot_bike(x),
        is_shared_with_mit_right(x),
    ]
    conditions_mit_left = [
        can_cardrive(x) and not is_bikepath_left(x) and not is_bikeroad(x)
        and not is_footway(x) and not is_bikelane_left(x)
        and not is_shared_buslane_left(x) and not is_path(x)
        and not is_track(x) and not cannot_bike(x),
        is_shared_with_mit_left(x),
    ]

    if single:
        def get_infra(x):
            if is_not_accessible(x) or is_tram(x):
                return 'no'
            if is_cycle_highway(x):
                return 'cycle_highway'
            if is_bikeroad(x):
                return 'bicycle_road'
            if is_service(x):
                return 'service_misc'
            if any(conditions_b_way_left) or any(conditions_b_way_right):
                return 'bicycle_way'
            if is_bikelane_left(x) or is_bikelane_right(x):
                return 'bicycle_lane'
            if is_shared_buslane_left(x) or is_shared_buslane_right(x):
                return 'bus_lane'
            if any(conditions_shared_left) or any(conditions_shared_right):
                return 'shared_way'
            if any(conditions_mit_left) or any(conditions_mit_right):
                return 'mit_road'
            if (is_pedestrian_left(x) or is_pedestrian_right(x)) and not is_indoor(x):
                return 'pedestrian'
            if is_path_not_forbidden(x):
                return 'path_not_forbidden'
            return 'no'
    else:
        def get_infra(x):
            if is_not_accessible(x) or is_tram(x):
                return 'no'
            if is_cycle_highway(x):  return 'cycle_highway'
            if is_bikeroad(x):       return 'bicycle_road'
            if is_service(x):        return 'service_misc'
            if any(conditions_b_way_right):
                if any(conditions_b_way_left):          return 'bicycle_way_both'
                if is_bikelane_left(x):                 return 'bicycle_way_right_lane_left'
                if is_shared_buslane_left(x):           return 'bicycle_way_right_bus_left'
                if any(conditions_shared_left):         return 'bicycle_way_right_shared_left'
                if any(conditions_mit_left):            return 'bicycle_way_right_mit_left'
                if is_pedestrian_left(x):               return 'bicycle_way_right_pedestrian_left'
                return 'bicycle_way_right_no_left'
            if any(conditions_b_way_left):
                if is_bikelane_right(x):                return 'bicycle_way_left_lane_right'
                if is_shared_buslane_right(x):          return 'bicycle_way_left_bus_right'
                if any(conditions_shared_right):        return 'bicycle_way_left_shared_right'
                if any(conditions_mit_right):           return 'bicycle_way_left_mit_right'
                if is_pedestrian_right(x):              return 'bicycle_way_left_pedestrian_right'
                return 'bicycle_way_left_no_right'
            if is_bikelane_right(x):
                if is_bikelane_left(x):                 return 'bicycle_lane_both'
                if is_shared_buslane_left(x):           return 'bicycle_lane_right_bus_left'
                if any(conditions_shared_left):         return 'bicycle_lane_right_shared_left'
                if any(conditions_mit_left):            return 'bicycle_lane_right_mit_left'
                if is_pedestrian_left(x):               return 'bicycle_lane_right_pedestrian_left'
                return 'bicycle_lane_right_no_left'
            if is_bikelane_left(x):
                if is_shared_buslane_right(x):          return 'bicycle_lane_left_bus_right'
                if any(conditions_shared_right):        return 'bicycle_lane_left_shared_right'
                if any(conditions_mit_right):           return 'bicycle_lane_left_mit_right'
                if is_pedestrian_right(x):              return 'bicycle_lane_left_pedestrian_right'
                return 'bicycle_lane_left_no_right'
            if is_shared_buslane_right(x):
                if is_shared_buslane_left(x):           return 'bus_lane_both'
                if any(conditions_shared_left):         return 'bus_lane_right_shared_left'
                if any(conditions_mit_left):            return 'bus_lane_right_mit_left'
                if is_pedestrian_left(x):               return 'bus_lane_right_pedestrian_left'
                return 'bus_lane_right_no_left'
            if is_shared_buslane_left(x):
                if any(conditions_shared_right):        return 'bus_lane_left_shared_right'
                if any(conditions_mit_right):           return 'bus_lane_left_mit_right'
                if is_pedestrian_right(x):              return 'bus_lane_left_pedestrian_right'
                return 'bus_lane_left_no_right'
            if any(conditions_shared_right):
                if any(conditions_shared_left):         return 'shared_way_both'
                if any(conditions_mit_left):            return 'shared_way_right_mit_left'
                if is_pedestrian_left(x):               return 'shared_way_right_pedestrian_left'
                return 'shared_way_right_no_left'
            if any(conditions_shared_left):
                if any(conditions_mit_right):           return 'shared_way_left_mit_right'
                if is_pedestrian_right(x):              return 'shared_way_left_pedestrian_right'
                return 'shared_way_left_no_right'
            if any(conditions_mit_right):
                if any(conditions_mit_left):            return 'mit_road_both'
                if is_pedestrian_left(x):               return 'mit_road_right_pedestrian_left'
                return 'mit_road_right_no_left'
            if any(conditions_mit_left):
                if is_pedestrian_right(x):              return 'mit_road_left_pedestrian_right'
                return 'mit_road_left_no_right'
            if is_pedestrian_right(x) and not is_indoor(x):
                if is_pedestrian_left(x) and not is_indoor(x):
                    return 'pedestrian_both'
                return 'pedestrian_right_no_left'
            if is_pedestrian_left(x) and not is_indoor(x):
                return 'pedestrian_left_no_right'
            if is_path_not_forbidden(x):
                return 'path_not_forbidden'
            return 'no'

    return get_infra(x)


def aggregate_the_no_infra_category(cat):
    """Collapse non-cycling sub-categories to 'no'. Verbatim from BikeNEAT."""
    _agg = {
        "service_misc": "no",
        "bicycle_way_right_mit_left": "bicycle_way_right_no_left",
        "bicycle_way_right_pedestrian_left": "bicycle_way_right_no_left",
        "bicycle_way_left_mit_right": "bicycle_way_left_no_right",
        "bicycle_way_left_pedestrian_right": "bicycle_way_left_no_right",
        "bicycle_lane_right_mit_left": "bicycle_lane_right_no_left",
        "bicycle_lane_right_pedestrian_left": "bicycle_lane_right_no_left",
        "bicycle_lane_left_mit_right": "bicycle_lane_left_no_right",
        "bicycle_lane_left_pedestrian_right": "bicycle_lane_left_no_right",
        "bus_lane_right_mit_left": "bus_lane_right_no_left",
        "bus_lane_right_pedestrian_left": "bus_lane_right_no_left",
        "bus_lane_left_mit_right": "bus_lane_left_no_right",
        "bus_lane_left_pedestrian_right": "bus_lane_left_no_right",
        "shared_way_right_mit_left": "shared_way_right_no_left",
        "shared_way_right_pedestrian_left": "shared_way_right_no_left",
        "shared_way_left_mit_right": "shared_way_left_no_right",
        "shared_way_left_pedestrian_right": "shared_way_left_no_right",
        "mit_road_both": "no", "mit_road_right_pedestrian_left": "no",
        "mit_road_right_no_left": "no", "mit_road_left_pedestrian_right": "no",
        "mit_road_left_no_right": "no",
        "pedestrian_both": "no", "pedestrian_right_no_left": "no",
        "pedestrian_left_no_right": "no", "path_not_forbidden": "no",
        "mit_road": "no", "pedestrian": "no",
    }
    return _agg.get(cat, cat)


# Columns pyrosm adds that are not OSM tags - skipped when building tag dicts
_SKIP_COLS = {
    'id', 'geometry', 'area', 'osm_type', 'tags', 'nodes',
    'timestamp', 'version', 'changeset', 'length', 'from', 'to',
    'osmid', 'key', 'u', 'v', 'reversed',
}


def _row_to_tags(row, col_to_tag):
    """Build an OSM_KEYS-keyed dict from a GDF row, handling name normalization."""
    tags = {k: None for k in OSM_KEYS}   # ensure every key exists
    for col, tag in col_to_tag.items():
        if col in _SKIP_COLS:
            continue
        val = row.get(col)
        if val is not None and str(val).lower() not in ('nan', 'none', ''):
            tags[tag] = str(val)
    # Supplement from 'tags' column (JSON / dict with extra attrs)
    raw = row.get('tags')
    if raw is not None:
        try:
            d = json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, dict) else {})
            for k, v in d.items():
                if k in OSM_KEYS_SET and tags.get(k) is None and v not in (None, 'nan', 'none', ''):
                    tags[k] = str(v)
        except Exception:
            pass
    return tags


def run_bikeneat(pbf_path, city_polygon, utm_crs):
    """
    Load OSM from PBF, run BikeNEAT (single=True, aggregated=True), clip to city.
    Returns (clipped, cycling) GeoDataFrames; `cycling` has column
    'bicycle_infrastructure' != 'no'.
    """
    print("  Loading cycling + driving networks ...")
    osm_obj = pyrosm.OSM(pbf_path, bounding_box=list(city_polygon.bounds))
    net_bike = osm_obj.get_network(network_type="cycling")
    net_drive = osm_obj.get_network(network_type="driving")
    del osm_obj
    gc.collect()

    n_bike = len(net_bike) if net_bike is not None else 0
    n_drive = len(net_drive) if net_drive is not None else 0
    print(f"  Cycling: {n_bike} | Driving: {n_drive}")

    # Merge (BikeNEAT approach - ensures roads with cycleway:* tags are included)
    if net_drive is not None and n_drive > 0:
        merged = pd.merge(net_bike, net_drive, how='outer', on='id', suffixes=('', '_drv'))
        merged = merged.loc[:, ~merged.columns.str.endswith('_drv')]
    else:
        merged = net_bike.copy()
    del net_bike, net_drive
    gc.collect()

    # Drop rows that are metadata-only (no actual OSM tags)
    meta = {c for c in merged.columns if c in _SKIP_COLS}
    data_cols = [c for c in merged.columns if c not in meta]
    if data_cols:
        merged = merged[merged[data_cols].notna().any(axis=1)]
    merged = gpd.GeoDataFrame(merged, geometry='geometry', crs='EPSG:4326')
    print(f"  After merge + null-filter: {len(merged)} edges")

    # Build col -> OSM-tag mapping (pyrosm may store 'cycleway:left' as 'cycleway_left')
    col_to_tag = {}
    for col in merged.columns:
        col_to_tag[col] = col                    # default: same name
    for tag_key in OSM_KEYS:
        col_ver = tag_key.replace(':', '_')
        if col_ver in merged.columns:
            col_to_tag[col_ver] = tag_key        # underscore col -> colon tag

    # Classify
    print("  Classifying (BikeNEAT) ...")
    results = []
    for _, row in merged.iterrows():
        tags = _row_to_tags(row, col_to_tag)
        cat = set_value(tags, single=True)
        cat = aggregate_the_no_infra_category(cat)
        results.append(cat)

    merged['bicycle_infrastructure'] = results
    del results
    gc.collect()

    # Clip to exact city polygon
    clipped = merged[merged.geometry.intersects(city_polygon)].copy()
    del merged
    gc.collect()

    cycling = clipped[clipped['bicycle_infrastructure'] != 'no'].copy()
    print(f"  Clipped: {len(clipped)} edges | Cycling infra: {len(cycling)}")
    return clipped, cycling
