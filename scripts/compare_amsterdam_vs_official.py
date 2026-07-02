#!/usr/bin/env python3
"""
Amsterdam OSM vs Official Fietsnetwerk - two-scenario comparison.

Replaces amsterdam_vs_official.ipynb. All the actual pipeline logic lives in
cycling_analysis/cities/amsterdam.py; this script is just the CLI entry
point (argparse instead of hand-edited notebook constants) plus the
scenario-A/scenario-B orchestration that was main() in the notebook.

Usage:
    python scripts/compare_amsterdam_vs_official.py \\
        --official-path data/geojson_lnglat.geojson \\
        --pbf-path data/netherlands-latest.osm.pbf \\
        --output-dir output/amsterdam
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cycling_analysis.cities.amsterdam import (
    CAT_ORDER, DEDICATED_CATS, SHARED_CATS,
    load_official_data, load_osm_data,
    plot_bar_comparison_two, plot_comparison,
    run_scenario, save_csv,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--official-path', default='data/geojson_lnglat.geojson',
                         help='Path to the official Fietsnetwerk Amsterdam GeoJSON')
    parser.add_argument('--pbf-path', default='data/netherlands-latest.osm.pbf',
                         help='Path to a Netherlands (or Amsterdam-covering) OSM PBF extract')
    parser.add_argument('--output-dir', default='output/amsterdam')
    parser.add_argument('--spatial-buffer-m', type=float, default=15)
    args = parser.parse_args()

    print("Amsterdam OSM vs Official Fietsnetwerk - Two-Scenario Comparison")
    print("=" * 68)

    official_gdf = load_official_data(args.official_path)
    if official_gdf is None:
        sys.exit(1)

    print("\n  Loading OSM (strict clip)...")
    osm_strict, boundary_gdf, utm_crs = load_osm_data(args.pbf_path, clip_mode='strict')
    if osm_strict is None:
        sys.exit(1)

    print("\n  Loading OSM (keep full geometry)...")
    osm_keep, _, _ = load_osm_data(args.pbf_path, clip_mode='keep', show_viz=False)

    sep = "\n" + "=" * 68
    print(sep)
    print("  SCENARIO A: Both datasets clipped to osmnx municipality boundary")
    print("  Official km = Lengte_m of features within boundary")
    print("  OSM km      = geometry cut at boundary edge")
    print("=" * 68)
    (a_osm_km, a_off_km, a_osm_ded, a_off_ded,
     a_osm_shr, a_off_shr, a_joined, a_coverage,
     a_osm_gdf, a_off_gdf) = run_scenario(
        'A', official_gdf, osm_strict, osm_keep, boundary_gdf, utm_crs,
        spatial_buffer_m=args.spatial_buffer_m)

    print(sep)
    print("  SCENARIO B: Official full (unclipped); OSM edges kept whole")
    print("  Official km = full Lengte_m")
    print("  OSM km      = full geometry of edges touching boundary")
    print("=" * 68)
    (b_osm_km, b_off_km, b_osm_ded, b_off_ded,
     b_osm_shr, b_off_shr, b_joined, b_coverage,
     b_osm_gdf, b_off_gdf) = run_scenario(
        'B', official_gdf, osm_strict, osm_keep, boundary_gdf, utm_crs,
        spatial_buffer_m=args.spatial_buffer_m)

    print(sep)
    print("  SIDE-BY-SIDE SUMMARY")
    print("=" * 68)
    print(f"  {'Category':<33} {'A-OSM':>8} {'A-Off':>8} {'B-OSM':>8} {'B-Off':>8}")
    print(f"  {'-'*65}")
    all_cats = set(list(a_osm_km.keys()) + list(b_osm_km.keys()))
    for cat in CAT_ORDER:
        if cat not in all_cats:
            continue
        ao, af = a_osm_km.get(cat, 0), a_off_km.get(cat, 0)
        bo, bf = b_osm_km.get(cat, 0), b_off_km.get(cat, 0)
        if max(ao, af, bo, bf) < 0.5:
            continue
        m = '*' if cat in DEDICATED_CATS else ('.' if cat in SHARED_CATS else ' ')
        print(f"  {m} {cat:<33} {ao:>8.1f} {af:>8.1f} {bo:>8.1f} {bf:>8.1f}")
    print(f"  {'-'*65}")
    print(f"  * DEDICATED  {a_osm_ded:>8.1f} {a_off_ded:>8.1f} {b_osm_ded:>8.1f} {b_off_ded:>8.1f}")
    print(f"  . SHARED     {a_osm_shr:>8.1f} {a_off_shr:>8.1f} {b_osm_shr:>8.1f} {b_off_shr:>8.1f}")

    os.makedirs(args.output_dir, exist_ok=True)
    plot_comparison(a_osm_gdf, a_off_gdf, boundary_gdf, utm_crs, args.output_dir, suffix='_scenario_A')
    plot_comparison(b_osm_gdf, b_off_gdf, boundary_gdf, utm_crs, args.output_dir, suffix='_scenario_B')
    plot_bar_comparison_two(a_osm_km, b_osm_km, a_off_km, b_off_km, args.output_dir)

    print("\n  Saving output files...")
    save_csv(a_joined, a_coverage, a_osm_km, a_off_km,
             os.path.join(args.output_dir, 'scenario_A'), official_gdf=a_off_gdf)
    save_csv(b_joined, b_coverage, b_osm_km, b_off_km,
             os.path.join(args.output_dir, 'scenario_B'), official_gdf=b_off_gdf)

    import pandas as pd
    rows = []
    for cat in CAT_ORDER:
        ao, af = a_osm_km.get(cat, 0), a_off_km.get(cat, 0)
        bo, bf = b_osm_km.get(cat, 0), b_off_km.get(cat, 0)
        if max(ao, af, bo, bf) < 0.1:
            continue
        rows.append({
            'category': cat,
            'type': 'dedicated' if cat in DEDICATED_CATS else 'shared' if cat in SHARED_CATS else 'other',
            'A_osm_km': round(ao, 2), 'A_official_km': round(af, 2), 'A_diff_km': round(ao - af, 2),
            'B_osm_km': round(bo, 2), 'B_official_km': round(bf, 2), 'B_diff_km': round(bo - bf, 2),
        })
    pd.DataFrame(rows).to_csv(
        os.path.join(args.output_dir, 'amsterdam_two_scenario_summary.csv'), index=False)
    print(f"  Two-scenario summary: {args.output_dir}/amsterdam_two_scenario_summary.csv")
    print(f"\nComparison complete. Outputs in: {args.output_dir}")


if __name__ == '__main__':
    main()
