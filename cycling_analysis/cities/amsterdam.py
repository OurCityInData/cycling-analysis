"""
Amsterdam vs. official Fietsnetwerk comparison.

Ported from amsterdam_vs_official.ipynb. Uses the shared geometry cleanup
(cycling_analysis.geometry) and the shared NL06 filter
(cycling_analysis.classification.has_cycling_infrastructure), but keeps its
own classification taxonomy (classify_cycling_path_amsterdam) and its own
metrics/plotting functions rather than a forced-generic interface shared
with Barcelona — see cycling_analysis/classification.py's module docstring
for why.
"""

import os
import subprocess
import sys

import geopandas as gpd
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..classification import classify_cycling_path_amsterdam, has_cycling_infrastructure
from ..geometry import fix_geometry, remove_small_components

# ── Amsterdam / NL06 taxonomy ────────────────────────────────────────────────
CAT_ORDER = [
    'Moped/Bicycle path', 'Bike path', 'Cycle path - no mopeds',
    'One-way side bike lane', 'Two-way side bike lane',
    'Bike lane on sidewalk', 'Bus-bike lane', 'Greenway (parks)',
    'Contraflow cycling', 'Bicycle street',
    'Bicycle on the road',
    'Shared street at 30 km/h', 'Shared space',
    'Pacified street at 20 km/h', 'Pacified zone at 10 km-h',
    'Other / unclassified',
]
DEDICATED_CATS = {
    'Moped/Bicycle path',
    'Bike path',
    'Cycle path - no mopeds',
    'One-way side bike lane',
    'Two-way side bike lane',
    'Bike lane on sidewalk',
    'Bus-bike lane',
    'Greenway (parks)',
    'Contraflow cycling',
    'Bicycle street',
}
SHARED_CATS = {
    'Shared street at 30 km/h',
    'Shared space',
    'Pacified street at 20 km/h',
    'Pacified zone at 10 km-h',
}
CATEGORY_COLORS = {
    'Moped/Bicycle path':         '#E53935',
    'Bike path':                  '#43A047',
    'Cycle path - no mopeds':     '#1E88E5',
    'One-way side bike lane':     '#FB8C00',
    'Two-way side bike lane':     '#F4511E',
    'Bike lane on sidewalk':      '#8E24AA',
    'Bus-bike lane':              '#FFB300',
    'Greenway (parks)':           '#00897B',
    'Contraflow cycling':         '#D81B60',
    'Bicycle street':             '#00E676',
    'Bicycle on the road':        '#795548',
    'Shared street at 30 km/h':   '#00BCD4',
    'Shared space':               '#80DEEA',
    'Pacified street at 20 km/h': '#8BC34A',
    'Pacified zone at 10 km-h':   '#CDDC39',
    'Other / unclassified':       '#BDBDBD',
}
# ── Official Fietsnetwerk Amsterdam data mapping ─────────────────────────────
OFFICIAL_TYPW_MAP = {
    # Dutch Soort value                          → internal category
    "Fietspad":                                  "Bike path",
    "Fietspad (onverplicht)":                    "Bike path",
    "Fietspad (snorfiets niet toegestaan)":      "Cycle path - no mopeds",
    "Brom-/Fietspad":                            "Moped/Bicycle path",
    "Fietsstrook":                               "One-way side bike lane",
    "Fietsstraat":                               "Bicycle street",
    "Fiets op rijbaan":                          "Bicycle on the road",
    "Shared space":                              "Shared space",
    "Fietsoversteek":                            "Other / unclassified",
    "Verbinding":                                "Other / unclassified",
    # English labels (fallback for WFS endpoint variant)
    "Bike path":                                 "Bike path",
    "Cycle path - mopeds not allowed":           "Cycle path - no mopeds",
    "Bike path - optional":                      "Bike path",
    "Moped/Bicycle path":                        "Moped/Bicycle path",
    "Bicycle lane":                              "One-way side bike lane",
    "Bicycle street":                            "Bicycle street",
    "Bicycle on the road":                       "Bicycle on the road",
    "Bicycle crossing":                          "Other / unclassified",
    "Connection":                                "Other / unclassified",
}
OFFICIAL_SOORTCODE_MAP = {
    10: "Bike path",
    11: "Moped/Bicycle path",
    12: "Bike path",
    13: "Cycle path - no mopeds",
    20: "One-way side bike lane",
    30: "Bicycle street",
    40: "Bicycle on the road",
    50: "Shared space",
    51: "Other / unclassified",
    53: "Other / unclassified",
}
DEFAULT_SPATIAL_BUFFER_M = 15

def load_official_data(path):
    """
    Load official Fietsnetwerk Amsterdam GeoJSON (geojson_lnglat.geojson).

    Expected fields:
      Soort      : Dutch cycle type name  (primary classification key)
      Soortcode  : Numeric code (fallback if Soort absent)
      Lengte_m   : Official length in metres (used for reference totals)
      Richtingen : 1=one-way, 2=two-way
      Verharding : Surface type (Asfalt-Rood, Klinkers, Tegels, ...)
      HoofdPlus  : Network level (H=Hoofdnet, P=Plusnet)
      Stadsdeel  : City district code
    """
    print(f"\n{'='*60}")
    print(f"Loading official data: {path}")

    if not os.path.exists(path):
        print(f"  ✗ File not found: {path}")
        print("  Upload geojson_lnglat.geojson to your Drive and set OFFICIAL_PATH.")
        return None

    # Handle ZIP bundle (Shapefile)
    if path.endswith('.zip'):
        tmpdir = tempfile.mkdtemp()
        with zipfile.ZipFile(path, 'r') as z:
            z.extractall(tmpdir)
            print(f"  Extracted: {z.namelist()}")
        shp_files = []
        for root, dirs, files in os.walk(tmpdir):
            shp_files += [os.path.join(root, f) for f in files if f.endswith('.shp')]
        if not shp_files:
            print("  ✗ No .shp found in ZIP")
            return None
        gdf = gpd.read_file(shp_files[0])
    else:
        gdf = gpd.read_file(path)

    print(f"  Features  : {len(gdf)}")
    print(f"  CRS       : {gdf.crs}")
    print(f"  Columns   : {list(gdf.columns)}")

    # Identify type column: prefer Soort, fall back to Soortcode
    if 'Soort' in gdf.columns:
        type_col  = 'Soort'
        type_map  = OFFICIAL_TYPW_MAP
        print(f"  Type col  : 'Soort' (Dutch labels)")
    elif 'Soortcode' in gdf.columns:
        type_col  = 'Soortcode'
        type_map  = {str(k): v for k, v in OFFICIAL_SOORTCODE_MAP.items()}
        gdf['Soort'] = gdf['Soortcode'].map(OFFICIAL_SOORTCODE_MAP)
        print(f"  Type col  : 'Soortcode' (numeric fallback)")
    else:
        # Try English labels (WFS variant)
        for c in gdf.columns:
            vals = set(gdf[c].dropna().astype(str).str.strip().unique())
            if len(vals & set(OFFICIAL_TYPW_MAP.keys())) >= 3:
                type_col = c
                type_map = OFFICIAL_TYPW_MAP
                print(f"  Type col  : {c!r} (English labels, WFS variant)")
                break
        else:
            print(f"  ✗ Cannot identify type column.")
            print(f"  Columns: {list(gdf.columns)}")
            print(f"  Sample row: {gdf.iloc[0].to_dict()}")
            return None

    # Value distribution
    print(f"\n  {type_col} distribution:")
    vc = gdf[type_col].value_counts()
    for k, v in vc.items():
        cat   = type_map.get(str(k), "→ UNMAPPED")
        lengte = gdf[gdf[type_col]==k]['Lengte_m'].sum()/1000 if 'Lengte_m' in gdf.columns else 0
        print(f"    {str(k):<48} {v:>5} features  {lengte:>6.1f} km  → {cat}")

    unmapped = set(gdf[type_col].dropna().astype(str).unique()) - set(type_map.keys())
    if unmapped:
        print(f"  ⚠ Unmapped values: {unmapped}")

    # Apply mapping
    gdf['road_category'] = (gdf[type_col]
                             .astype(str)
                             .map(type_map)
                             .fillna('Other / unclassified'))
    gdf['official_type'] = gdf[type_col].astype(str)

    # Preserve extra fields for richer analysis
    for col in ['Richtingen', 'Verharding', 'HoofdPlus', 'Stadsdeel', 'Lengte_m']:
        if col not in gdf.columns:
            gdf[col] = None

    # Ensure WGS84
    if gdf.crs is None:
        gdf = gdf.set_crs('EPSG:4326')
    else:
        gdf = gdf.to_crs('EPSG:4326')

    total_lengte = gdf['Lengte_m'].sum() / 1000 if 'Lengte_m' in gdf.columns else 0
    print(f"\n  Official data loaded ✓  ({len(gdf)} features, "
          f"{total_lengte:.1f} km official total)")
    return gdf

def load_osm_data(pbf_path, city_name='amsterdam', country='Netherlands', clip_mode='strict',
                   show_viz=True, clean_isolated=True):
    """
    Load, filter, clip and classify Amsterdam OSM cycling network.
    Mirrors the NL06 final main() pipeline exactly.
    """
    import osmnx as ox
    from pyrosm import OSM

    print(f"\n{'='*60}")
    print(f"Loading OSM data for {city_name}")

    if not os.path.exists(pbf_path):
        print(f"  ✗ PBF not found: {pbf_path}")
        return None, None, None

    # 1. City boundary
    print("  Fetching city boundary...")
    boundary_gdf = ox.geocode_to_gdf(f"{city_name}, {country}", which_result=1)
    polygon = boundary_gdf.geometry.iloc[0]

    # UTM CRS
    lon, lat = polygon.centroid.x, polygon.centroid.y
    utm_zone = int((lon + 180) // 6) + 1
    epsg = 32600 + utm_zone if lat >= 0 else 32700 + utm_zone
    utm_crs = f'EPSG:{epsg}'
    area_km2 = boundary_gdf.to_crs(utm_crs).geometry.area.iloc[0] / 1e6
    print(f"  UTM CRS: {utm_crs}, Area: {area_km2:.1f} km²")

    # 2. Osmium bbox extract
    bbox = polygon.bounds
    temp_pbf = f"/tmp/temp_{city_name}.osm.pbf"
    cmd = (f"osmium extract -b {bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]} "
           f"{pbf_path} -o {temp_pbf} --overwrite")
    print("  Running osmium extract...")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ✗ osmium failed: {result.stderr}")
        return None, None, None

    # 3. Read cycling network
    print("  Reading cycling network from PBF...")
    osm = OSM(temp_pbf)
    nodes, edges = osm.get_network(network_type="cycling", nodes=True)
    edges = edges[edges.geometry.geom_type.isin(['LineString','MultiLineString'])].copy()
    edges = edges[edges.geometry.notna()].copy()
    edges['geometry'] = edges['geometry'].apply(fix_geometry)
    edges = edges[edges.geometry.notna()].copy()
    print(f"  After linemerge: {len(edges)} edges")

    # ── VIZ 1 — Raw cycling edges, before has_cycling_infrastructure filter ──
    if show_viz:
        fig, ax = plt.subplots(figsize=(10, 10))
        edges.plot(ax=ax, color='steelblue', linewidth=0.4, alpha=0.6)
        boundary_gdf.to_crs(edges.crs).plot(ax=ax, facecolor='none', edgecolor='black', linewidth=1.5)
        ax.set_title(f'Raw OSM cycling edges — before filtering\n({len(edges):,} edges)', fontsize=13)
        ax.axis('off')
        plt.tight_layout()
        plt.show()
    # ─────────────────────────────────────────────────────────────────────────

    # 4. Filter
    before = len(edges)
    edges = edges[edges.apply(has_cycling_infrastructure, axis=1)].copy()
    print(f"  After cycling filter: {len(edges)} (removed {before - len(edges)})")

    # 5. Clip to boundary
    if edges.index.name is not None:
        edges = edges.reset_index(drop=False)
    poly_gs = gpd.GeoSeries([polygon], crs=boundary_gdf.crs)
    if poly_gs.crs != edges.crs:
        poly_gs = poly_gs.to_crs(edges.crs)
    clip_poly = poly_gs.iloc[0]
    edges = edges[edges.geometry.intersects(clip_poly)].copy()
    if clip_mode == 'strict':
        edges['geometry'] = edges.geometry.intersection(clip_poly)
        edges = edges[~edges.geometry.is_empty].copy()
    print(f"  After clip ({clip_mode}): {len(edges)} edges")

    # 6. Remove isolated fragments (connected component filter)
    # Builds the actual network topology graph and drops any subgraph whose
    # total length is below min_component_km (default 0.5 km).  This catches
    # dot clusters in parks that are internally connected but isolated from the
    # main cycling network — which proximity-based cleaning misses entirely.
    if clean_isolated:
        edges = remove_small_components(edges, utm_crs, min_component_km=0.5, snap_m=2.0)

    # ── VIZ 2 — Filtered & clipped edges, before classification ──────────────
    if show_viz:
        fig, ax = plt.subplots(figsize=(10, 10))
        edges.plot(ax=ax, color='darkorange', linewidth=0.5, alpha=0.7)
        boundary_gdf.to_crs(edges.crs).plot(ax=ax, facecolor='none', edgecolor='black', linewidth=1.5)
        ax.set_title(f'OSM cycling edges — after filter & clip, before classification\n({len(edges):,} edges)', fontsize=13)
        ax.axis('off')
        plt.tight_layout()
        plt.show()
    # ─────────────────────────────────────────────────────────────────────────

    # 7. Classify
    print("  Classifying edges...")
    edges['road_category'] = edges.apply(classify_cycling_path_amsterdam, axis=1)
    edges_proj = edges.to_crs(utm_crs)
    edges['length_km'] = edges_proj.geometry.length / 1000.0

    # Cleanup
    if os.path.exists(temp_pbf):
        os.remove(temp_pbf)

    print(f"  OSM data ready: {len(edges)} edges")
    return edges, boundary_gdf, utm_crs

def compute_km_table(gdf, utm_crs, source_label, use_lengte_m=False):
    """
    Compute km per category from a GeoDataFrame.

    use_lengte_m=True  → use official Lengte_m field (exact, no CRS error)
    use_lengte_m=False → compute from geometry (for OSM data)
    """
    if use_lengte_m and 'Lengte_m' in gdf.columns:
        raw = gdf.groupby('road_category')['Lengte_m'].sum() / 1000.0
    else:
        proj = gdf.to_crs(utm_crs).copy()
        proj['length_km'] = proj.geometry.length / 1000.0
        raw = proj.groupby('road_category')['length_km'].sum()
    result = {cat: raw.get(cat, 0.0) for cat in CAT_ORDER}
    for cat in raw.index:
        if cat not in CAT_ORDER:
            result[cat] = raw[cat]
    total_ded = sum(v for k, v in result.items() if k in DEDICATED_CATS)
    total_shr = sum(v for k, v in result.items() if k in SHARED_CATS)
    return result, total_ded, total_shr

def spatial_overlap_analysis(osm_gdf, official_gdf, utm_crs, buffer_m=15):
    """
    For each OSM edge, find all official features within buffer_m metres.
    Returns a merged GeoDataFrame with:
      - road_category       : OSM classification
      - official_category   : best matching official category (or NaN)
      - match_status        : 'matched', 'osm_only', 'category_match', 'category_mismatch'
    """
    print(f"\n  Spatial overlap analysis (buffer={buffer_m}m)...")
    osm_proj = osm_gdf.to_crs(utm_crs).copy()
    off_proj  = official_gdf.to_crs(utm_crs).copy()[['road_category','geometry']]
    off_proj_buf = off_proj.copy()
    off_proj_buf['geometry'] = off_proj_buf.geometry.buffer(buffer_m)

    joined = gpd.sjoin(
        osm_proj[['road_category','length_km','geometry']],
        off_proj_buf.rename(columns={'road_category':'official_category'}),
        how='left', predicate='intersects'
    )
    joined = joined[~joined.index.duplicated(keep='first')]

    def match_status(row):
        if pd.isna(row['official_category']):
            return 'osm_only'
        if row['road_category'] == row['official_category']:
            return 'category_match'
        # Both in dedicated / both in shared counts as broad match
        osm_ded  = row['road_category'] in DEDICATED_CATS
        off_ded  = row['official_category'] in DEDICATED_CATS
        if osm_ded == off_ded:
            return 'broad_match'
        return 'category_mismatch'

    joined['match_status'] = joined.apply(match_status, axis=1)

    n_total    = len(joined)
    n_matched  = (joined['match_status'] != 'osm_only').sum()
    n_exact    = (joined['match_status'] == 'category_match').sum()
    n_broad    = (joined['match_status'] == 'broad_match').sum()
    n_mismatch = (joined['match_status'] == 'category_mismatch').sum()
    n_osm_only = (joined['match_status'] == 'osm_only').sum()

    km_matched  = joined.loc[joined['match_status'] != 'osm_only', 'length_km'].sum()
    km_exact    = joined.loc[joined['match_status'] == 'category_match', 'length_km'].sum()
    km_total    = joined['length_km'].sum()

    print(f"    Total OSM edges:         {n_total:>6}")
    print(f"    Matched to official:     {n_matched:>6}  ({n_matched/n_total*100:.1f}%)")
    print(f"      Exact category match:  {n_exact:>6}  ({km_exact:.1f} km)")
    print(f"      Broad match:           {n_broad:>6}")
    print(f"      Category mismatch:     {n_mismatch:>6}")
    print(f"    OSM-only (no official):  {n_osm_only:>6}  ({n_osm_only/n_total*100:.1f}%)")
    print(f"    km coverage:             {km_matched:.1f}/{km_total:.1f} km ({km_matched/km_total*100:.1f}%)")

    return joined

def official_coverage_analysis(osm_gdf, official_gdf, utm_crs, buffer_m=15):
    """
    For each official feature, check if any OSM edge is within buffer_m.
    Returns coverage statistics per official category.
    """
    print(f"\n  Official coverage analysis (buffer={buffer_m}m)...")
    osm_proj = osm_gdf.to_crs(utm_crs).copy()
    off_proj  = official_gdf.to_crs(utm_crs).copy()
    off_proj['off_length_km'] = off_proj.geometry.length / 1000.0

    # Buffer OSM edges
    osm_buf = osm_proj.copy()
    osm_buf['geometry'] = osm_buf.geometry.buffer(buffer_m)

    joined = gpd.sjoin(
        off_proj[['road_category','off_length_km','geometry']],
        osm_buf[['road_category','geometry']].rename(
            columns={'road_category':'osm_category'}),
        how='left', predicate='intersects'
    )
    joined_dedup = joined[~joined.index.duplicated(keep='first')]
    joined_dedup = joined_dedup.copy()
    joined_dedup['covered_by_osm'] = joined_dedup['osm_category'].notna()

    coverage = joined_dedup.groupby('road_category').agg(
        total_features  = ('off_length_km', 'count'),
        total_km        = ('off_length_km', 'sum'),
        covered_features= ('covered_by_osm', 'sum'),
        covered_km      = ('off_length_km', lambda x: x[joined_dedup.loc[x.index,'covered_by_osm']].sum())
    ).reset_index()
    coverage['coverage_pct'] = coverage['covered_km'] / coverage['total_km'] * 100

    print(f"\n  {'Category':<35} {'Off km':>8} {'Covered':>8} {'%':>6}")
    print(f"  {'─'*62}")
    for _, r in coverage.iterrows():
        print(f"  {r['road_category']:<35} {r['total_km']:>8.1f} {r['covered_km']:>8.1f} {r['coverage_pct']:>5.1f}%")

    return coverage

def print_comparison_table(osm_km, official_km, osm_ded, osm_shr, off_ded, off_shr):
    print(f"\n{'='*78}")
    print(f"  AMSTERDAM CYCLING INFRASTRUCTURE: OSM vs OFFICIAL COMPARISON")
    print(f"{'='*78}")
    print(f"  {'Category':<33} {'OSM km':>8}  {'Official km':>11}  {'Diff km':>8}  {'Diff %':>7}")
    print(f"  {'─'*74}")

    for cat in CAT_ORDER:
        osm_v = osm_km.get(cat, 0.0)
        off_v = official_km.get(cat, 0.0)
        if osm_v < 0.1 and off_v < 0.1:
            continue
        diff    = osm_v - off_v
        diff_pct = (diff / off_v * 100) if off_v > 0 else float('nan')
        marker  = '★' if cat in DEDICATED_CATS else ('·' if cat in SHARED_CATS else ' ')
        off_s   = f"{off_v:.1f}" if off_v > 0 else '—'
        diff_s  = f"{diff:+.1f}" if off_v > 0 else '(no ref)'
        dpct_s  = f"{diff_pct:+.0f}%" if not np.isnan(diff_pct) else ''
        print(f"  {marker} {cat:<33} {osm_v:>8.1f}  {off_s:>11}  {diff_s:>8}  {dpct_s:>7}")

    print(f"  {'─'*74}")
    osm_total  = osm_ded  + osm_shr
    off_total  = off_ded  + off_shr
    print(f"  ★ DEDICATED total               {osm_ded:>8.1f}  {off_ded:>11.1f}  {osm_ded-off_ded:>+8.1f}")
    print(f"  · SHARED total                  {osm_shr:>8.1f}  {off_shr:>11.1f}  {osm_shr-off_shr:>+8.1f}")
    print(f"    GRAND total                   {osm_total:>8.1f}  {off_total:>11.1f}  {osm_total-off_total:>+8.1f}")
    print(f"  {'─'*74}")
    print(f"  Official fietsnetten total: 1,742.6 km (Lengte_m sum)")
    print(f"  OSM network total:          {osm_total:.1f} km")
    print(f"  Note: 'Bicycle on the road' & 'Other' not counted as dedicated infra")
    print(f"{'='*78}")

def plot_comparison(osm_gdf, official_gdf, boundary_gdf, utm_crs, output_dir, suffix=''):
    """Side-by-side map: OSM (left) vs Official (right)."""
    print("\n  Generating comparison map...")
    fig, axes = plt.subplots(1, 2, figsize=(20, 12),
                              gridspec_kw={'wspace': 0.05})

    for ax, (gdf, title) in zip(axes, [
        (osm_gdf,      "OSM (NL06 classification)"),
        (official_gdf, "Official Fietsnetwerk Amsterdam"),
    ]):
        boundary_gdf.plot(ax=ax, facecolor='#F5F5F5', edgecolor='#424242',
                          linewidth=1.5, zorder=1)
        other = official_gdf if gdf is osm_gdf else osm_gdf
        other.plot(ax=ax, color='#E0E0E0', linewidth=0.4, alpha=0.4, zorder=2)

        for cat in CAT_ORDER:
            sub = gdf[gdf['road_category'] == cat]
            if sub.empty: continue
            color = CATEGORY_COLORS.get(cat, '#999999')
            sub.plot(ax=ax, color=color, linewidth=1.5, alpha=0.9, zorder=3)

        ax.set_title(title, fontsize=13, fontweight='bold', pad=10)
        ax.axis('off')

    # Shared legend
    handles = []
    all_cats = set(osm_gdf['road_category'].unique()) | \
               set(official_gdf['road_category'].unique())
    for cat in CAT_ORDER:
        if cat in all_cats and cat != 'Other / unclassified':
            color = CATEGORY_COLORS.get(cat, '#999999')
            km_osm = osm_gdf[osm_gdf['road_category']==cat]['length_km'].sum()
            km_off = official_gdf.to_crs(utm_crs)[
                official_gdf['road_category']==cat].geometry.length.sum() / 1000
            label = f"{cat}  (OSM {km_osm:.0f} km | Off {km_off:.0f} km)"
            handles.append(mpatches.Patch(color=color, label=label))

    fig.legend(handles=handles, loc='lower center', ncol=3, fontsize=8,
               frameon=True, framealpha=0.95, bbox_to_anchor=(0.5, -0.02),
               title='Road category  (★=dedicated · =shared)', title_fontsize=9)
    plt.suptitle("Amsterdam Cycling Infrastructure: OSM vs Official",
                 fontsize=15, fontweight='bold', y=1.01)

    out_path = os.path.join(output_dir, f'amsterdam_comparison_map{suffix}.png')
    plt.savefig(out_path, dpi=150, bbox_inches='tight')
    print(f"  Map saved: {out_path}")
    plt.show()
    plt.close(fig)

def plot_bar_comparison(osm_km, official_km, output_dir):
    """Horizontal bar chart comparing OSM vs official km per category."""
    cats = [c for c in CAT_ORDER
            if osm_km.get(c, 0) > 0.5 or official_km.get(c, 0) > 0.5]
    if not cats:
        return

    y = np.arange(len(cats))
    osm_vals = [osm_km.get(c, 0) for c in cats]
    off_vals = [official_km.get(c, 0) for c in cats]

    fig, ax = plt.subplots(figsize=(11, max(6, len(cats)*0.55)))
    h = 0.35
    ax.barh(y + h/2, off_vals, h, label='Official', color='#1E88E5', alpha=0.85)
    ax.barh(y - h/2, osm_vals, h, label='OSM (NL06)', color='#E53935', alpha=0.85)

    ax.set_yticks(y)
    ax.set_yticklabels(cats, fontsize=9)
    ax.set_xlabel('km', fontsize=10)
    ax.set_title('Amsterdam cycling infrastructure: OSM vs Official (km)',
                 fontsize=12, fontweight='bold')
    ax.legend(fontsize=10)
    ax.axvline(0, color='black', linewidth=0.5)
    ax.grid(axis='x', alpha=0.3)
    plt.tight_layout()

    out_path = os.path.join(output_dir, 'amsterdam_comparison_bars.png')
    plt.savefig(out_path, dpi=150, bbox_inches='tight')
    print(f"  Bar chart saved: {out_path}")
    plt.show()
    plt.close(fig)

def plot_bar_comparison_two(a_osm_km, b_osm_km, a_off_km, b_off_km, output_dir):
    """Four-column horizontal bar: Scenario A OSM / A Official / B OSM / B Official."""
    cats = [c for c in CAT_ORDER
            if max(a_osm_km.get(c,0), a_off_km.get(c,0),
                   b_osm_km.get(c,0), b_off_km.get(c,0)) > 0.5]
    if not cats:
        return

    y = np.arange(len(cats))
    h = 0.20
    fig, ax = plt.subplots(figsize=(13, max(7, len(cats)*0.65)))

    ax.barh(y + 1.5*h, [a_off_km.get(c,0) for c in cats], h,
            label='A – Official (clipped)', color='#1E88E5', alpha=0.85)
    ax.barh(y + 0.5*h, [a_osm_km.get(c,0) for c in cats], h,
            label='A – OSM (strict clip)', color='#E53935', alpha=0.85)
    ax.barh(y - 0.5*h, [b_off_km.get(c,0) for c in cats], h,
            label='B – Official (full)', color='#43A047', alpha=0.85)
    ax.barh(y - 1.5*h, [b_osm_km.get(c,0) for c in cats], h,
            label='B – OSM (keep full)', color='#FB8C00', alpha=0.85)

    ax.set_yticks(y)
    ax.set_yticklabels(cats, fontsize=9)
    ax.set_xlabel('km', fontsize=10)
    ax.set_title('Amsterdam: OSM vs Official — Scenario A (clipped) vs B (full)',
                 fontsize=12, fontweight='bold')
    ax.legend(fontsize=9, loc='lower right')
    ax.grid(axis='x', alpha=0.3)
    plt.tight_layout()

    out_path = os.path.join(output_dir, 'amsterdam_two_scenario_bars.png')
    plt.savefig(out_path, dpi=150, bbox_inches='tight')
    print(f"  Two-scenario bar chart saved: {out_path}")
    plt.show()
    plt.close(fig)

def save_csv(osm_joined, official_coverage, osm_km, official_km, output_dir, official_gdf=None):
    """Save comparison data as CSV files."""
    # Per-segment OSM data with match status
    cols = ['road_category', 'official_category', 'match_status', 'length_km', 'geometry']
    cols_present = [c for c in cols if c in osm_joined.columns]
    seg_path = os.path.join(output_dir, 'amsterdam_osm_segments.csv')
    osm_joined[cols_present].drop(columns='geometry', errors='ignore').to_csv(
        seg_path, index=False)
    print(f"  OSM segments CSV: {seg_path}")

    # Summary km table
    summary = []
    for cat in CAT_ORDER:
        osm_v = osm_km.get(cat, 0.0)
        off_v = official_km.get(cat, 0.0)
        if osm_v < 0.1 and off_v < 0.1: continue
        summary.append({
            'category':    cat,
            'type':        'dedicated' if cat in DEDICATED_CATS else
                           'shared' if cat in SHARED_CATS else 'other',
            'osm_km':      round(osm_v, 2),
            'official_km': round(off_v, 2),
            'diff_km':     round(osm_v - off_v, 2),
            'diff_pct':    round((osm_v - off_v) / off_v * 100, 1) if off_v > 0 else None,
        })
    sum_path = os.path.join(output_dir, 'amsterdam_comparison_summary.csv')
    pd.DataFrame(summary).to_csv(sum_path, index=False)
    print(f"  Summary CSV: {sum_path}")

    # Official coverage table
    cov_path = os.path.join(output_dir, 'amsterdam_official_coverage.csv')
    official_coverage.to_csv(cov_path, index=False)
    print(f"  Coverage CSV: {cov_path}")

    # Extra: official breakdown by surface (Verharding) and network level (HoofdPlus)
    if 'Lengte_m' in official_gdf.columns:
        if 'Verharding' in official_gdf.columns:
            verh = official_gdf.groupby(['road_category','Verharding'])['Lengte_m'].sum().div(1000)
            verh_path = os.path.join(output_dir, 'amsterdam_official_by_surface.csv')
            verh.reset_index().rename(columns={'Lengte_m':'km'}).to_csv(verh_path, index=False)
            print(f"  Surface breakdown CSV: {verh_path}")
        if 'HoofdPlus' in official_gdf.columns:
            net = official_gdf.groupby(['road_category','HoofdPlus'])['Lengte_m'].sum().div(1000)
            net_path = os.path.join(output_dir, 'amsterdam_official_by_network.csv')
            net.reset_index().rename(columns={'Lengte_m':'km'}).to_csv(net_path, index=False)
            print(f"  Network level CSV: {net_path}")
        if 'Stadsdeel' in official_gdf.columns:
            dist = official_gdf.groupby(['road_category','Stadsdeel'])['Lengte_m'].sum().div(1000)
            dist_path = os.path.join(output_dir, 'amsterdam_official_by_district.csv')
            dist.reset_index().rename(columns={'Lengte_m':'km'}).to_csv(dist_path, index=False)
            print(f"  District breakdown CSV: {dist_path}")

def inspect_fietsstraat_osm_tags(official_gdf, osm_gdf, buffer_m=15):
    import warnings; warnings.filterwarnings('ignore')

    utm = 'EPSG:32631'

    # Pull only the official Fietsstraat features
    fstr = official_gdf[official_gdf['road_category'] == 'Bicycle street'].copy()
    if fstr.empty:
        print("No Fietsstraat features found in official_gdf")
        return

    fstr_utm  = fstr.to_crs(utm)
    osm_utm   = osm_gdf.to_crs(utm)

    # Buffer and spatial join
    fstr_buf = fstr_utm.copy()
    fstr_buf['geometry'] = fstr_buf.geometry.buffer(buffer_m)

    joined = gpd.sjoin(osm_utm, fstr_buf[['geometry']], how='inner', predicate='intersects')
    hits   = osm_utm.loc[joined.index.unique()]

    print(f"Official Fietsstraat: {len(fstr)} features, {fstr['Lengte_m'].sum()/1000:.1f} km")
    print(f"OSM edges within {buffer_m}m: {len(hits)}")
    print()

    # Show the most relevant tag columns
    tag_cols = [c for c in hits.columns if c in (
        'highway','bicycle','cycleway','cyclestreet','bicycle_road',
        'oneway','maxspeed','foot','access','motor_vehicle','moped','mofa',
        'surface','name','road_category'
    )]

    print("=== road_category distribution (how our classifier sees them) ===")
    print(hits['road_category'].value_counts().to_string())
    print()

    print("=== highway distribution ===")
    print(hits['highway'].value_counts().to_string())
    print()

    print("=== Top tag combinations ===")
    combo_cols = [c for c in ['highway','bicycle','cycleway','cyclestreet',
                               'bicycle_road','oneway','maxspeed'] if c in hits.columns]
    combos = hits[combo_cols].fillna('').astype(str).apply(
        lambda r: ' | '.join(f"{k}={v}" for k,v in r.items() if v not in ('','nan','')), axis=1
    )
    print(combos.value_counts().head(30).to_string())
    print()

    print("=== Sample rows (first 10) ===")
    display_cols = [c for c in tag_cols if c in hits.columns]
    print(hits[display_cols].head(10).to_string())

def run_scenario(scenario, official_gdf, osm_gdf_strict, osm_gdf_keep,
                 boundary_gdf, utm_crs, spatial_buffer_m=DEFAULT_SPATIAL_BUFFER_M):
    """
    Run one comparison scenario and return (osm_km, off_km, osm_ded, off_ded,
    osm_shr, off_shr, osm_joined, official_coverage, official_for_stats).

    scenario='A': both OSM and official clipped to osmnx municipality boundary
    scenario='B': official full (unclipped); OSM edges touching boundary kept whole
    """
    print(f"\n{'▶'*3} Scenario {scenario}")

    if scenario == 'A':
        # Official: clip to exact osmnx boundary, use Lengte_m of clipped features
        boundary_wgs = boundary_gdf.to_crs('EPSG:4326')
        poly_exact   = boundary_wgs.geometry.iloc[0]
        off_for_stats = official_gdf[
            official_gdf.geometry.intersects(poly_exact)].copy()
        print(f"  Official clipped to boundary: {len(off_for_stats)} features, "
              f"{off_for_stats['Lengte_m'].sum()/1000:.1f} km")
        osm_for_stats = osm_gdf_strict   # geometry cut at boundary
        print(f"  OSM strict clip: {len(osm_for_stats)} edges")

    else:  # scenario B
        # Official: full dataset, Lengte_m exact
        off_for_stats = official_gdf.copy()
        print(f"  Official full (unclipped): {len(off_for_stats)} features, "
              f"{off_for_stats['Lengte_m'].sum()/1000:.1f} km")
        osm_for_stats = osm_gdf_keep     # full geometry preserved
        print(f"  OSM keep full geometry: {len(osm_for_stats)} edges")

    # Km tables
    osm_km, osm_ded, osm_shr = compute_km_table(
        osm_for_stats, utm_crs, 'OSM', use_lengte_m=False)
    off_km, off_ded, off_shr = compute_km_table(
        off_for_stats, utm_crs, 'Official', use_lengte_m=True)

    print_comparison_table(osm_km, off_km, osm_ded, osm_shr, off_ded, off_shr)

    # Spatial join (use expanded boundary scope for both scenarios)
    boundary_wgs   = boundary_gdf.to_crs('EPSG:4326')
    poly_expanded  = boundary_wgs.geometry.iloc[0].buffer(0.01)
    off_join_scope = official_gdf[
        official_gdf.geometry.intersects(poly_expanded)].copy()

    osm_joined         = spatial_overlap_analysis(
        osm_for_stats, off_join_scope, utm_crs, spatial_buffer_m)
    official_coverage  = official_coverage_analysis(
        osm_for_stats, off_join_scope, utm_crs, spatial_buffer_m)

    mismatches = osm_joined[osm_joined['match_status'] == 'category_mismatch'].copy()
    if len(mismatches) > 0:
        print(f"\n  Category mismatches ({len(mismatches)} edges, "
              f"{mismatches['length_km'].sum():.1f} km):")
        cross = pd.crosstab(mismatches['road_category'],
                            mismatches['official_category'],
                            values=mismatches['length_km'],
                            aggfunc='sum').fillna(0).round(1)
        print(cross.to_string())

    return (osm_km, off_km, osm_ded, off_ded, osm_shr, off_shr,
            osm_joined, official_coverage, osm_for_stats, off_for_stats)