#!/usr/bin/env python3
"""
Barcelona OSM vs Official (AMB xarxa_pedalable) comparison.

Replaces barcelona_vs_official.ipynb. Pipeline logic lives in
cycling_analysis/cities/barcelona.py; this script is the CLI entry point
plus the orchestration that ran loose in notebook cells at the bottom of
barcelona_vs_official.ipynb.

Note: the notebook's later "diagnostic pipeline maps" cell (re-running
intermediate pyrosm stages to plot them) was ad-hoc exploration, not part of
the reusable pipeline, and isn't ported here - see MIGRATION_NOTES.md.

Usage:
    python scripts/compare_barcelona_vs_official.py \\
        --official-path data/xarxa_pedalable_barcelona.geojson \\
        --pbf-path data/cataluna-latest.osm.pbf \\
        --output-dir output/barcelona
"""

import argparse
import os
import sys

import osmnx as ox

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cycling_analysis.cities.barcelona import (
    load_official_bcn, load_osm_bcn, compute_km_table,
    spatial_overlap_analysis, official_coverage_analysis, mismatch_matrix,
    print_comparison_table, plot_comparison, plot_bar_comparison,
    save_csvs, DEFAULT_SPATIAL_BUFFER_M,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--official-path', default='data/xarxa_pedalable_barcelona.geojson',
                         help='Path to the AMB xarxa_pedalable GeoJSON (or fetch via AMB_API in the module)')
    parser.add_argument('--pbf-path', default='data/cataluna-latest.osm.pbf')
    parser.add_argument('--output-dir', default='output/barcelona')
    parser.add_argument('--spatial-buffer-m', type=float, default=DEFAULT_SPATIAL_BUFFER_M)
    parser.add_argument('--clean-isolated', action='store_true', default=True)
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    print('=' * 72)
    print('  Barcelona OSM vs Official - full pipeline')
    print('=' * 72)

    print('\n[1/6] Fetching Barcelona municipality boundary...')
    bnd = ox.geocode_to_gdf('Barcelona, Spain', which_result=1)
    poly = bnd.geometry.iloc[0]
    lon, lat = poly.centroid.x, poly.centroid.y
    zone = int((lon + 180) // 6) + 1
    utm_crs = f"EPSG:{32600 + zone if lat >= 0 else 32700 + zone}"
    print(f'  Boundary OK | UTM CRS: {utm_crs}')

    print('\n[2/6] Loading official data (clipped to boundary)...')
    official_clipped, ref_km_clipped = load_official_bcn(args.official_path, utm_crs, clip_poly=poly)

    print('\n[3/6] Loading OSM data...')
    osm_edges = load_osm_bcn(
        args.pbf_path, bnd, utm_crs,
        clean_isolated=args.clean_isolated,
        keep_full_geometry=False,
    )

    print('\n[4/6] Computing comparison table...')
    km = compute_km_table(osm_edges, ref_km_clipped, utm_crs)
    print_comparison_table(km, 'OSM vs Official (both clipped to Barcelona)')

    print('\n[5/6] Running spatial analyses...')
    osm_status = spatial_overlap_analysis(osm_edges, official_clipped, utm_crs, args.spatial_buffer_m)

    counts = osm_status['match_status'].value_counts()
    total = len(osm_status)
    print(f'\n  Spatial overlap:')
    print(f'    Total OSM edges    : {total:,}')
    print(f'    Exact match        : {counts.get("exact_match", 0):,}')
    print(f'    Broad match        : {counts.get("broad_match", 0):,}')
    print(f'    Category mismatch  : {counts.get("category_mismatch", 0):,}')
    print(f'    OSM-only           : {counts.get("osm_only", 0):,}')
    if total:
        matched_pct = (total - counts.get('osm_only', 0)) / total * 100
        print(f'    Matched (any)      : {matched_pct:.1f}%')

    cov = official_coverage_analysis(official_clipped, osm_edges, utm_crs, args.spatial_buffer_m)
    print(f'\n  Official coverage by category:')
    print(f'  {"Category":<38} {"Off km":>7} {"Covered":>8} {"Coverage":>9}')
    print(f'  {"-"*38} {"-"*7} {"-"*8} {"-"*9}')
    for _, r in cov.iterrows():
        print(f'  {r["category"]:<38} {r["official_km"]:7.1f} {r["covered_km"]:8.1f} {r["coverage_pct"]:8.1f}%')

    print('\n  Category mismatch matrix (top 15):')
    mx = mismatch_matrix(osm_status)
    if len(mx) > 0:
        print(mx.head(15).to_string(index=False))

    km.to_csv(os.path.join(args.output_dir, 'bcn_km_comparison.csv'), index=False)
    cov.to_csv(os.path.join(args.output_dir, 'bcn_coverage.csv'), index=False)
    if len(mx) > 0:
        mx.to_csv(os.path.join(args.output_dir, 'bcn_mismatches.csv'), index=False)
    print(f'\nCSVs saved to {args.output_dir}')

    print('\n[6/6] Generating visualisations...')
    plot_comparison(
        osm_edges, official_clipped, bnd,
        scenario_label='OSM vs Official (both clipped)',
        save_path=os.path.join(args.output_dir, 'bcn_map.png'),
    )
    plot_bar_comparison(km, save_path=os.path.join(args.output_dir, 'bcn_bar_comparison.png'))

    print('\n' + '=' * 72)
    print('  Done.')
    print('=' * 72)


if __name__ == '__main__':
    main()
