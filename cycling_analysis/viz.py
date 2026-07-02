"""
Plotting shared by the BikeNEAT comparison script (compare_vs_bikeneat.py).

Ported from the "COMPARISON MAP" cell duplicated in barcelona_vs_bikeneat.ipynb
and freiburg_vs_bikeneat.ipynb (confirmed identical logic, differing only by
city name in labels/comments). The original cell built this plot using
notebook-global variables (boundary_gdf, minx/miny/maxx/maxy, etc.) captured
in a closure (`_base`); this version takes them as explicit parameters
instead so it works as a normal importable function.

Amsterdam's and Barcelona's own _vs_official comparison plots
(plot_comparison, plot_bar_comparison, etc.) stay in cycling_analysis/cities/
since those differ in structure between the two cities.
"""

import geopandas as gpd
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.gridspec import GridSpec

# BikeNEAT colour scheme (single=True categories after aggregation)
BN_COLORS = {
    'cycle_highway': '#FFD700',   # gold
    'bicycle_road':  '#8B00FF',   # violet
    'bicycle_way':   '#006400',   # dark green
    'bicycle_lane':  '#0099FF',   # blue
    'bus_lane':      '#FF6600',   # orange
    'shared_way':    '#66BB66',   # medium green
}
BN_LABELS = {
    'cycle_highway': 'Cycle Highway',
    'bicycle_road':  'Bicycle Road / Bicycle Street',
    'bicycle_way':   'Bicycle Way (dedicated path)',
    'bicycle_lane':  'Bicycle Lane (painted on road)',
    'bus_lane':      'Bus / Bike Lane',
    'shared_way':    'Shared Way (foot + cycle)',
}


def plot_bikeneat_comparison(city_label, boundary_gdf, nl06_cycling, nl06_total_km,
                              bikeneat_cycling, bikeneat_km, bikeneat_total_km,
                              output_path):
    """
    Three-panel comparison: NL06 filter map | BikeNEAT category map | summary bars.

    city_label: display name, e.g. "Freiburg im Breisgau" or "Barcelona"
    nl06_cycling / bikeneat_cycling: GeoDataFrames of the respective outputs
    bikeneat_km: Series of km by BikeNEAT category (see bikeneat.aggregate_the_no_infra_category)
    output_path: PNG path to save to
    """
    minx, miny, maxx, maxy = gpd.GeoDataFrame(
        pd.concat([nl06_cycling[['geometry']], bikeneat_cycling[['geometry']]]),
        geometry='geometry', crs='EPSG:4326'
    ).total_bounds
    px = (maxx - minx) * 0.04
    py = (maxy - miny) * 0.04

    fig = plt.figure(figsize=(20, 14))
    gs = GridSpec(2, 2, figure=fig, height_ratios=[2.5, 1.0], wspace=0.04, hspace=0.35)
    ax_nl = fig.add_subplot(gs[0, 0])
    ax_bn = fig.add_subplot(gs[0, 1])
    ax_bar = fig.add_subplot(gs[1, :])

    def _base(ax, title):
        boundary_gdf.plot(ax=ax, color='#F5F5F5', edgecolor='#BBBBBB', linewidth=1.0)
        ax.set_xlim(minx - px, maxx + px)
        ax.set_ylim(miny - py, maxy + py)
        ax.set_title(title, fontsize=12, fontweight='bold', pad=8)
        ax.set_axis_off()

    # Panel A - NL06 Filter
    _base(ax_nl, f"NL06 Filter (Amsterdam pipeline)\n{nl06_total_km:.1f} km total, no categorisation")
    nl06_cycling.plot(ax=ax_nl, color='#2196F3', linewidth=0.55, alpha=0.85)
    ax_nl.legend(
        handles=[mpatches.Patch(color='#2196F3', label=f'Cycling infrastructure ({nl06_total_km:.1f} km)')],
        loc='lower left', fontsize=8.5, framealpha=0.92
    )

    # Panel B - BikeNEAT
    _base(ax_bn, f"BikeNEAT - Lukawska et al. (2026)\n{bikeneat_total_km:.1f} km total (excl. 'no' category)")
    for cat, color in BN_COLORS.items():
        sub = bikeneat_cycling[bikeneat_cycling['bicycle_infrastructure'] == cat]
        if len(sub) > 0:
            sub.plot(ax=ax_bn, color=color, linewidth=0.55, alpha=0.90)
    patches_bn = [
        mpatches.Patch(color=BN_COLORS[cat], label=f"{BN_LABELS[cat]}  ({bikeneat_km.get(cat, 0):.1f} km)")
        for cat in BN_COLORS if cat in bikeneat_km.index
    ]
    ax_bn.legend(handles=patches_bn, loc='lower left', fontsize=7.5, framealpha=0.92, handlelength=1.4)

    # Panel C - Summary bar chart
    bar_labels, bar_vals, bar_cols = [], [], []
    bar_labels.append(f"NL06 Filter\n(total: {nl06_total_km:.1f} km)")
    bar_vals.append(nl06_total_km)
    bar_cols.append('#2196F3')
    for cat in BN_COLORS:
        km = bikeneat_km.get(cat, 0)
        if km > 0.5:
            bar_labels.append(f"BikeNEAT: {BN_LABELS[cat]}\n({km:.1f} km)")
            bar_vals.append(km)
            bar_cols.append(BN_COLORS[cat])

    bars = ax_bar.barh(bar_labels, bar_vals, color=bar_cols, edgecolor='white', height=0.55)
    ax_bar.set_xlabel('Length (km)', fontsize=10)
    ax_bar.set_title('Cycling Infrastructure - Length by Method & Category', fontsize=11, fontweight='bold')
    ax_bar.grid(axis='x', alpha=0.25, linestyle='--')
    ax_bar.invert_yaxis()
    ax_bar.spines[['top', 'right']].set_visible(False)

    for bar, val in zip(bars, bar_vals):
        ax_bar.text(bar.get_width() + max(bar_vals) * 0.01,
                    bar.get_y() + bar.get_height() / 2,
                    f'{val:.1f} km', va='center', fontsize=8.5)

    ax_bar.axhline(y=0.5, color='gray', linestyle='--', linewidth=0.7, alpha=0.5)

    delta = bikeneat_total_km - nl06_total_km
    sign = '+' if delta >= 0 else ''
    delta_pct = (delta / nl06_total_km * 100) if nl06_total_km else float('nan')
    ax_bar.text(
        0.98, 0.05,
        f"BikeNEAT vs NL06 delta: {sign}{delta:.1f} km  ({sign}{delta_pct:.1f}%)",
        transform=ax_bar.transAxes, ha='right', va='bottom',
        fontsize=9, color='#333333',
        bbox=dict(facecolor='white', edgecolor='#CCCCCC', boxstyle='round,pad=0.3')
    )

    fig.suptitle(
        f'{city_label} - Cycling Network Comparison\n'
        f'Data: OpenStreetMap (Geofabrik), both methods use the same PBF',
        fontsize=14, fontweight='bold', y=1.01
    )

    plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.show()
    print(f"\nMap saved -> {output_path}")
