#!/usr/bin/env python3
"""
Compare the shared NL06 filter against the BikeNEAT classifier for one city.

Replaces barcelona_vs_bikeneat.ipynb and freiburg_vs_bikeneat.ipynb, which
were the same pipeline copy-pasted with the city name changed (confirmed via
diff - see cycling_analysis/bikeneat.py's module docstring). Add a new city
here by adding an entry to CITY_CONFIGS; no other code changes needed.

Usage:
    python scripts/compare_vs_bikeneat.py --city freiburg
    python scripts/compare_vs_bikeneat.py --city barcelona --data-dir ./data
"""

import argparse
import os
import sys
import warnings

import geopandas as gpd
import osmnx as ox
import matplotlib
matplotlib.use('Agg')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
warnings.filterwarnings('ignore')

from cycling_analysis.bikeneat import run_bikeneat
from cycling_analysis.classification import has_cycling_infrastructure
from cycling_analysis.io import download_pbf
from cycling_analysis.viz import plot_bikeneat_comparison

CITY_CONFIGS = {
    'barcelona': {
        'city_name': 'Barcelona',
        'country': 'Spain',
        'pbf_url': 'https://download.geofabrik.de/europe/spain/cataluna-latest.osm.pbf',
        'pbf_filename': 'cataluna-latest.osm.pbf',
        'output_map': 'barcelona_bikeneat_vs_nl06.png',
    },
    'freiburg': {
        'city_name': 'Freiburg im Breisgau',
        'country': 'Germany',
        'pbf_url': ('https://download.geofabrik.de/europe/germany/'
                     'baden-wuerttemberg/freiburg-regbez-latest.osm.pbf'),
        'pbf_filename': 'freiburg-regbez-latest.osm.pbf',
        'output_map': 'freiburg_bikeneat_vs_nl06.png',
    },
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--city', required=True, choices=sorted(CITY_CONFIGS.keys()))
    parser.add_argument('--data-dir', default='data', help='Where to cache the PBF (default: ./data)')
    parser.add_argument('--output-dir', default='output', help='Where to save the comparison map (default: ./output)')
    args = parser.parse_args()

    cfg = CITY_CONFIGS[args.city]
    os.makedirs(args.data_dir, exist_ok=True)
    os.makedirs(args.output_dir, exist_ok=True)
    pbf_path = os.path.join(args.data_dir, cfg['pbf_filename'])
    output_map = os.path.join(args.output_dir, cfg['output_map'])

    print(f"Fetching boundary for {cfg['city_name']}, {cfg['country']} ...")
    boundary_gdf = ox.geocode_to_gdf(f"{cfg['city_name']}, {cfg['country']}", which_result=1)
    city_poly = boundary_gdf.geometry.iloc[0]

    lon, lat = city_poly.centroid.x, city_poly.centroid.y
    utm_zone = int((lon + 180) // 6) + 1
    utm_crs = f"EPSG:{32600 + utm_zone if lat >= 0 else 32700 + utm_zone}"
    print(f"  UTM: {utm_crs}")

    download_pbf(cfg['pbf_url'], pbf_path)

    print(f"\nRunning BikeNEAT on {cfg['city_name']} ...")
    _, bikeneat_cycling = run_bikeneat(pbf_path, city_poly, utm_crs)
    bn_proj = bikeneat_cycling.to_crs(utm_crs)
    bikeneat_km = bn_proj.groupby('bicycle_infrastructure').geometry.apply(
        lambda g: g.length.sum() / 1000)
    bikeneat_total_km = bn_proj.geometry.length.sum() / 1000

    print(f"\nRunning NL06 filter on {cfg['city_name']} ...")
    from pyrosm import OSM
    osm_obj = OSM(pbf_path, bounding_box=list(city_poly.bounds))
    raw = osm_obj.get_network(network_type="cycling")
    mask = raw.apply(has_cycling_infrastructure, axis=1)
    nl06_cycling = gpd.GeoDataFrame(raw[mask].copy(), geometry='geometry', crs='EPSG:4326')
    nl06_cycling = nl06_cycling[nl06_cycling.geometry.intersects(city_poly)].copy()
    nl06_proj = nl06_cycling.to_crs(utm_crs).copy()
    nl06_total_km = nl06_proj.geometry.length.sum() / 1000

    print(f"\n{'=' * 55}")
    print(f"  NL06 total:      {nl06_total_km:.1f} km")
    print(f"  BikeNEAT total:  {bikeneat_total_km:.1f} km")
    print(f"{'=' * 55}")

    plot_bikeneat_comparison(
        cfg['city_name'], boundary_gdf, nl06_cycling, nl06_total_km,
        bikeneat_cycling, bikeneat_km, bikeneat_total_km, output_map,
    )


if __name__ == '__main__':
    main()
