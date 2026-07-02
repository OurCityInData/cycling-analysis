"""
Barcelona (AMB) vs. official cycling-network comparison.

Ported from barcelona_vs_official.ipynb. Uses the shared geometry cleanup
(cycling_analysis.geometry) and Barcelona's own filter/taxonomy
(cycling_analysis.classification.has_cycling_infrastructure_bcn /
classify_cycling_path_bcn) — see classification.py's module docstring for
why this isn't merged with amsterdam.py into one generic engine.

Note: an ad-hoc "diagnostic pipeline maps" cell at the end of the original
notebook (re-running intermediate pyrosm stages just to plot them) was
exploratory scratch work, not part of the reusable pipeline, and was not
ported here.
"""

import os
import subprocess

import geopandas as gpd
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..classification import classify_cycling_path_bcn, has_cycling_infrastructure_bcn
from ..geometry import fix_geometry, remove_small_components

CAT_ORDER = [
    'One-way side bike lane',
    'Two-way side bike lane',
    'Bike lane on sidewalk',
    'Greenway (parks)',
    'Calmed zone at 10 km/h',
    'Calmed street at 20 km/h',
    'Shared street at 30 km/h',
    'Bus-bike lane',
    'Contraflow cycling',
    'Other / unclassified',
]
DEDICATED_CATS = {
    'One-way side bike lane',
    'Two-way side bike lane',
    'Bike lane on sidewalk',
    'Greenway (parks)',
    'Bus-bike lane',
    'Contraflow cycling',
}
BCN_DEDICATED_CATS = DEDICATED_CATS
SHARED_CATS = {
    'Calmed zone at 10 km/h',
    'Calmed street at 20 km/h',
    'Shared street at 30 km/h',
}
BCN_SHARED_CATS = SHARED_CATS
CATEGORY_COLORS = {
    'One-way side bike lane':   '#1E88E5',  # blue
    'Two-way side bike lane':   '#1565C0',  # dark blue
    'Bike lane on sidewalk':    '#8E24AA',  # purple
    'Greenway (parks)':         '#00897B',  # teal
    'Calmed zone at 10 km/h':   '#E53935',  # red
    'Calmed street at 20 km/h': '#FB8C00',  # orange
    'Shared street at 30 km/h': '#FDD835',  # yellow
    'Bus-bike lane':            '#FFB300',  # amber
    'Contraflow cycling':       '#D81B60',  # pink
    'Other / unclassified':     '#BDBDBD',  # grey
}
# ── AMB (Area Metropolitana de Barcelona) official data mapping ──────────────
AMB_TYPW_MAP = {
    # AMB_TIP_GENERAL values that map directly to a single category
    'Vorera bici':            'Bike lane on sidewalk',
    'Carril o vorera-bici':   'Bike lane on sidewalk',
    'Camí verd o pista bici': 'Greenway (parks)',
    'Carrer 30':              'Shared street at 30 km/h',
    # 'Carril bici' → direction-dependent (handled in load_official_bcn via AMB_SENTIT)
    # 'Altres vies pacificades' → speed-dependent (handled via MAXSPEED)
    # Excluded — infrastructure not yet built or alternative routing
    'Pendent d\'execució':   '__EXCLUDE__',
    'Traçat alternatiu':      '__EXCLUDE__',
}
AMB_DETALL_BUS_BIKE    = {'bus-bici', 'bus bici', 'carril bus', 'bus bike'}
AMB_DETALL_CONTRAFLOW  = {'contramarxa', 'contrasentit', 'contraflow', 'contramarch'}
AMB_API = (
    "https://geoportal.amb.cat/geoserveis/rest/services"
    "/xarxa_pedalable/MapServer/0/query"
)
AMB_PARAMS = {
    "where":             "1=1",
    "outFields":         "*",
    "f":                 "geojson",
    "resultRecordCount": 2000,
}
PBF_URL = "https://download.geofabrik.de/europe/spain/cataluna-latest.osm.pbf"
DEFAULT_SPATIAL_BUFFER_M = 15


def load_official_bcn(path, utm_crs, clip_poly=None, municipality=None):
    """
    Load AMB xarxa_pedalable GeoJSON and map to internal categories.

    Category logic:
      AMB_TIP_GENERAL = 'Carril bici'
        + AMB_SENTIT contains 'bidirec'   → Two-way side bike lane
        + AMB_SENTIT contains 'unidirec'  → One-way side bike lane
        + AMB_SENTIT unknown/null         → One-way side bike lane (default)
      AMB_TIP_GENERAL = 'Vorera bici'
        or 'Carril o vorera-bici'         → Bike lane on sidewalk
      AMB_TIP_GENERAL = 'Camí verd o pista bici'
                                          → Greenway (parks)
      AMB_TIP_GENERAL = 'Carrer 30'       → Shared street at 30 km/h
      AMB_TIP_GENERAL = 'Altres vies pacificades'
        + MAXSPEED ≤ 10                   → Calmed zone at 10 km/h
        + MAXSPEED ≤ 20                   → Calmed street at 20 km/h
        + MAXSPEED > 20 or unknown        → Shared street at 30 km/h

      AMB_TIP_DETALL contains bus-bici keyword → Bus-bike lane (overrides above)
      AMB_TIP_DETALL contains contramarxa kw  → Contraflow cycling (overrides above)

    Excluded (infrastructure not yet built):
      AMB_TIP_GENERAL = 'Pendent d\'execució'  (planned, not built)
      AMB_TIP_GENERAL = 'Traçat alternatiu'    (alternative routing)

    municipality: if not None, filter to this MUNICIPI value (e.g. 'Barcelona')
    """
    gdf = gpd.read_file(path)
    print(f'AMB data loaded  ({len(gdf)} features)')
    print(f'  CRS     : {gdf.crs}')

    # ── CRS sanity check ──────────────────────────────────────────────────────
    # ArcGIS REST f=geojson always returns WGS84 coordinates. A previously
    # downloaded file may carry a wrong EPSG:25831 header. Detect and override.
    if gdf.crs and gdf.crs.to_epsg() in (25831, 25830):
        xmin = gdf.total_bounds[0]   # min longitude / min easting
        # EPSG:25831 easting for Catalonia: ~260000–560000 m
        # WGS84 longitude for Catalonia:    ~0.1–3.3 degrees
        if xmin < 50:
            print(f'  ⚠️  CRS header says EPSG:25831 but coordinates look like WGS84 (xmin={xmin:.3f})')
            print(f'       Overriding CRS to EPSG:4326. Delete and re-download to fix the file permanently.')
            gdf = gdf.set_crs('EPSG:4326', allow_override=True)

    print(f'  Columns : {list(gdf.columns)}')

    # ── Municipality filter ───────────────────────────────────────────────────
    if municipality is not None and 'MUNICIPI' in gdf.columns:
        before = len(gdf)
        gdf = gdf[gdf['MUNICIPI'] == municipality].copy()
        print(f'  Municipality filter ({municipality!r}): {before} → {len(gdf)} features')

    # ── Exclude non-built infrastructure ──────────────────────────────────────
    if 'AMB_TIP_GENERAL' in gdf.columns:
        exclude_vals = {'Pendent d\'execució', 'Traçat alternatiu'}
        before = len(gdf)
        gdf = gdf[~gdf['AMB_TIP_GENERAL'].isin(exclude_vals)].copy()
        excluded = before - len(gdf)
        if excluded > 0:
            print(f'  Excluded {excluded} planned/alternative features')
    else:
        print('  ⚠️  AMB_TIP_GENERAL column not found — no exclusion filter applied')

    # ── Assign category ───────────────────────────────────────────────────────
    def classify_amb_row(row):
        tip  = str(row.get('AMB_TIP_GENERAL', '') or '').strip()
        det  = str(row.get('AMB_TIP_DETALL',  '') or '').strip().lower()
        sent = str(row.get('AMB_SENTIT',      '') or '').strip().lower()
        spd_raw = str(row.get('MAXSPEED', '') or '').strip()

        try:
            spd = float(spd_raw)
        except ValueError:
            spd = None

        # ── Check AMB_TYPW_MAP for direct mappings first ──────────────────────
        direct = AMB_TYPW_MAP.get(tip)
        if direct == '__EXCLUDE__':
            return '__EXCLUDE__'
        if direct is not None:
            # Still allow bus-bike / contraflow override from AMB_TIP_DETALL
            pass

        # ── Bus-bike lane (detected from AMB_TIP_DETALL) ──────────────────────
        if any(kw in det for kw in AMB_DETALL_BUS_BIKE):
            return 'Bus-bike lane'

        # ── Contraflow cycling (detected from AMB_TIP_DETALL) ─────────────────
        if any(kw in det for kw in AMB_DETALL_CONTRAFLOW):
            return 'Contraflow cycling'

        # ── If a direct mapping was found (non-exclude), use it ───────────────
        if direct is not None:
            return direct

        # ── Carril bici — direction-dependent ─────────────────────────────────
        if tip == 'Carril bici':
            if 'bidirec' in sent:
                return 'Two-way side bike lane'
            # Unidireccional or unknown → one-way
            return 'One-way side bike lane'

        # ── Altres vies pacificades — speed-dependent ─────────────────────────
        if tip == 'Altres vies pacificades':
            if spd is not None:
                if spd <= 10: return 'Calmed zone at 10 km/h'
                if spd <= 20: return 'Calmed street at 20 km/h'
            return 'Shared street at 30 km/h'

        # ── Unmapped value ────────────────────────────────────────────────────
        if tip:
            print(f'  ⚠️  Unmapped AMB_TIP_GENERAL: {tip!r} → Other / unclassified')
        return 'Other / unclassified'

    gdf['category'] = gdf.apply(classify_amb_row, axis=1)

    # Drop excluded rows
    before = len(gdf)
    gdf = gdf[gdf['category'] != '__EXCLUDE__'].copy()
    if len(gdf) < before:
        print(f'  Removed {before - len(gdf)} excluded rows after classification')

    # ── Compute segment length ─────────────────────────────────────────────────
    gdf_utm = gdf.to_crs(utm_crs)
    gdf['length_m'] = gdf_utm.geometry.length

    # ── Clip to boundary if provided ──────────────────────────────────────────
    if clip_poly is not None:
        poly_gs = gpd.GeoSeries([clip_poly], crs='EPSG:4326')
        if poly_gs.crs != gdf.crs:
            poly_gs = poly_gs.to_crs(gdf.crs)
        before = len(gdf)
        gdf = gdf[gdf.geometry.intersects(poly_gs.iloc[0])].copy()
        gdf['geometry'] = gdf.geometry.intersection(poly_gs.iloc[0])
        gdf = gdf[~gdf.geometry.is_empty].copy()
        gdf_utm2 = gdf.to_crs(utm_crs)
        gdf['length_m'] = gdf_utm2.geometry.length
        print(f'  Clipped: {before} → {len(gdf)} features')

    # ── Category summary ──────────────────────────────────────────────────────
    ref_km = {}
    total_km = gdf['length_m'].sum() / 1000
    print(f'\nCategory distribution:')
    for cat in CAT_ORDER:
        mask = gdf['category'] == cat
        if mask.any():
            n  = mask.sum()
            km = gdf.loc[mask, 'length_m'].sum() / 1000
            ref_km[cat] = km
            print(f'  {cat:40s} {n:5d} features  {km:6.1f} km')
    unmapped = gdf[gdf['category'] == 'Other / unclassified']
    if len(unmapped) > 0:
        ref_km['Other / unclassified'] = unmapped['length_m'].sum() / 1000
    print(f'  {"":<40} ─────────────────────')
    print(f'  {"TOTAL":<40}        {total_km:6.1f} km')

    return gdf, ref_km

def load_osm_bcn(pbf_path, boundary_gdf, utm_crs, clean_isolated=True,
                 keep_full_geometry=False):
    """
    Run the full BCN OSM pipeline:
      1. osmnx boundary fetch
      2. osmium bbox crop of the Catalonia PBF
      3. pyrosm reads cycling edges
      4. fix_geometry → has_cycling_infrastructure_bcn filter
      5. clip to municipality boundary
      6. remove_small_components (if clean_isolated=True)
      7. classify_cycling_path_bcn

    keep_full_geometry=False : clip geometry at boundary (Scenario A)
    keep_full_geometry=True  : keep edges whole that touch boundary (Scenario B)
    """
    from pyrosm import OSM

    poly     = boundary_gdf.geometry.iloc[0]
    bbox     = poly.bounds
    tmp_pbf  = '/tmp/barcelona_cycling_clip.osm.pbf'

    # 1. osmium crop to Barcelona bbox
    print('Cropping PBF to Barcelona bbox...')
    cmd = (f'osmium extract -b {bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]} '
           f'"{pbf_path}" -o "{tmp_pbf}" --overwrite')
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f'osmium failed:\n{res.stderr}')
    print(f'  Crop OK')

    # 2. Read + fix geometry
    print('Reading edges with pyrosm...')
    osm = OSM(tmp_pbf)
    _, raw = osm.get_network(network_type='cycling', nodes=True)
    raw = raw[raw.geometry.geom_type.isin(['LineString', 'MultiLineString'])].copy()
    raw = raw[raw.geometry.notna()].copy()
    raw['geometry'] = raw['geometry'].apply(fix_geometry)
    raw = raw[raw.geometry.notna()].copy()
    print(f'  Raw edges: {len(raw):,}')

    # 3. BCN filter
    print('Applying BCN cycling filter...')
    mask   = raw.apply(has_cycling_infrastructure_bcn, axis=1)
    passed = raw[mask].copy()
    print(f'  Passed: {len(passed):,}  |  Failed: {(~mask).sum():,}')

    # 4. Clip to municipality boundary
    poly_gs = gpd.GeoSeries([poly], crs=boundary_gdf.crs)
    if poly_gs.crs != passed.crs:
        poly_gs = poly_gs.to_crs(passed.crs)
    clip_poly = poly_gs.iloc[0]

    if keep_full_geometry:
        edges = passed[passed.geometry.intersects(clip_poly)].copy()
    else:
        edges = passed[passed.geometry.intersects(clip_poly)].copy()
        edges['geometry'] = edges.geometry.intersection(clip_poly)
        edges = edges[~edges.geometry.is_empty].copy()
    print(f'  After clip: {len(edges):,}')

    # 5. Remove small components
    if clean_isolated:
        edges = remove_small_components(edges, utm_crs, min_component_km=0.5, snap_m=2.0)

    # 6. Classify
    print('Classifying edges...')
    edges['category'] = edges.apply(classify_cycling_path_bcn, axis=1)

    # 7. Compute length
    edges_utm = edges.to_crs(utm_crs)
    edges['length_m'] = edges_utm.geometry.length

    total_km = edges['length_m'].sum() / 1000
    print(f'\nOSM edges ready: {len(edges):,} ({total_km:.1f} km)')
    print('Category breakdown:')
    for cat in CAT_ORDER:
        mask = edges['category'] == cat
        if mask.any():
            km = edges.loc[mask, 'length_m'].sum() / 1000
            print(f'  {cat:40s} {km:6.1f} km')

    if os.path.exists(tmp_pbf):
        os.remove(tmp_pbf)

    return edges

def compute_km_table(osm_gdf, official_ref_km, utm_crs):
    """Build per-category km comparison table."""
    osm_utm = osm_gdf.to_crs(utm_crs)
    osm_km = (
        osm_utm.groupby(osm_gdf['category'])
        .apply(lambda g: g.geometry.length.sum() / 1000)
        .rename('osm_km')
    )
    rows = []
    for cat in CAT_ORDER:
        o_km = osm_km.get(cat, 0.0)
        f_km = official_ref_km.get(cat, None)
        if f_km is not None:
            diff    = o_km - f_km
            diff_pct = (diff / f_km * 100) if f_km > 0 else float('nan')
        else:
            diff = diff_pct = float('nan')
        rows.append({'category': cat, 'osm_km': o_km,
                     'official_km': f_km, 'diff_km': diff, 'diff_pct': diff_pct})
    return pd.DataFrame(rows)

def spatial_overlap_analysis(osm_gdf, official_gdf, utm_crs, buffer_m=15):
    """
    For each OSM edge, check if any official feature falls within buffer_m.
    Returns the OSM GDF with match_status and matched_official_cat columns.
    """
    osm_utm = osm_gdf.to_crs(utm_crs).copy()
    off_utm = official_gdf.to_crs(utm_crs).copy()

    off_buffered = off_utm.copy()
    off_buffered['geometry'] = off_utm.geometry.buffer(buffer_m)

    joined = gpd.sjoin(
        osm_utm[['geometry', 'category']],
        off_buffered[['geometry', 'category']].rename(columns={'category': 'off_cat'}),
        how='left', predicate='intersects'
    )

    def assign_status(group):
        osm_cat = group['category'].iloc[0]
        off_cats = group['off_cat'].dropna().unique()
        if len(off_cats) == 0:
            return 'osm_only'
        if osm_cat in off_cats:
            return 'exact_match'
        # Broad match: both are dedicated or both are shared
        osm_dedicated = osm_cat in BCN_DEDICATED_CATS
        any_dedicated = any(c in BCN_DEDICATED_CATS for c in off_cats)
        if osm_dedicated == any_dedicated:
            return 'broad_match'
        return 'category_mismatch'

    status = joined.groupby(joined.index).apply(assign_status).rename('match_status')
    best_off = (
        joined.dropna(subset=['off_cat'])
        .groupby(level=0)['off_cat']
        .first()
        .rename('matched_official_cat')
    )

    result = osm_gdf.copy()
    result['match_status']         = status.reindex(result.index).fillna('osm_only')
    result['matched_official_cat'] = best_off.reindex(result.index)
    return result

def official_coverage_analysis(official_gdf, osm_gdf, utm_crs, buffer_m=15):
    """
    For each official feature, check if any OSM edge falls within buffer_m.
    Returns coverage statistics per category.
    """
    off_utm = official_gdf.to_crs(utm_crs).copy()
    osm_utm = osm_gdf.to_crs(utm_crs).copy()

    osm_buffered = osm_utm.copy()
    osm_buffered['geometry'] = osm_utm.geometry.buffer(buffer_m)

    joined = gpd.sjoin(
        off_utm[['geometry', 'category', 'length_m']],
        osm_buffered[['geometry']],
        how='left', predicate='intersects'
    )
    joined['covered'] = joined['index_right'].notna()
    joined_dedup = joined[~joined.index.duplicated(keep='first')].copy()

    rows = []
    for cat in CAT_ORDER:
        mask = official_gdf['category'] == cat
        if not mask.any():
            continue
        idx  = official_gdf[mask].index
        subset = joined_dedup.loc[joined_dedup.index.isin(idx)]
        total_km   = subset['length_m'].sum() / 1000
        covered_km = subset.loc[subset['covered'], 'length_m'].sum() / 1000
        pct = (covered_km / total_km * 100) if total_km > 0 else 0.0
        rows.append({'category': cat, 'official_km': total_km,
                     'covered_km': covered_km, 'coverage_pct': pct})
    return pd.DataFrame(rows)

def mismatch_matrix(osm_with_status):
    """Return a sorted DataFrame of (osm_category, official_category, km) mismatches."""
    mismatches = osm_with_status[osm_with_status['match_status'] == 'category_mismatch'].copy()
    if len(mismatches) == 0:
        print('No category mismatches found.')
        return pd.DataFrame()
    mx = (
        mismatches.groupby(['category', 'matched_official_cat'])['length_m']
        .sum()
        .div(1000)
        .rename('km')
        .reset_index()
        .sort_values('km', ascending=False)
    )
    mx.columns = ['osm_category', 'official_category', 'km']
    return mx

def print_comparison_table(km_df, scenario_label=''):
    """Print a formatted km comparison table."""
    label = f' [{scenario_label}]' if scenario_label else ''
    print(f'\n{"─"*72}')
    print(f'  Km comparison table{label}')
    print(f'{"─"*72}')
    print(f'  {"Category":<38} {"OSM km":>8} {"Off km":>8} {"Diff km":>9} {"Diff %":>7}')
    print(f'  {"─"*38} {"─"*8} {"─"*8} {"─"*9} {"─"*7}')
    ded_osm = ded_off = 0.0
    sha_osm = sha_off = 0.0
    for _, r in km_df.iterrows():
        cat  = r['category']
        o_km = r['osm_km']
        f_km = r['official_km']
        diff = r['diff_km']
        pct  = r['diff_pct']
        marker = '★' if cat in BCN_DEDICATED_CATS else '·' if cat in BCN_SHARED_CATS else ' '
        f_str  = f'{f_km:8.1f}' if f_km is not None and not np.isnan(f_km) else '       —'
        d_str  = f'{diff:+9.1f}' if not np.isnan(diff) else '        —'
        p_str  = f'{pct:+6.0f}%' if not np.isnan(pct) else '      —'
        print(f'{marker} {cat:<38} {o_km:8.1f} {f_str} {d_str} {p_str}')
        if cat in BCN_DEDICATED_CATS:
            ded_osm += o_km
            ded_off += f_km if f_km and not np.isnan(f_km) else 0
        elif cat in BCN_SHARED_CATS:
            sha_osm += o_km
            sha_off += f_km if f_km and not np.isnan(f_km) else 0
    print(f'  {"─"*38} {"─"*8} {"─"*8} {"─"*9} {"─"*7}')
    print(f'★ {"DEDICATED total":<38} {ded_osm:8.1f} {ded_off:8.1f}')
    print(f'· {"SHARED total":<38} {sha_osm:8.1f} {sha_off:8.1f}')
    print(f'  {"GRAND total":<38} {ded_osm+sha_osm:8.1f} {ded_off+sha_off:8.1f}')
    print(f'{"─"*72}')

def plot_comparison(osm_gdf, official_gdf, boundary_gdf, scenario_label='',
                    save_path=None):
    """Side-by-side map: OSM left, Official right."""
    fig, axes = plt.subplots(1, 2, figsize=(20, 12))

    for ax, gdf, title in [
        (axes[0], osm_gdf,      f'OSM — {scenario_label}'),
        (axes[1], official_gdf, f'Official — {scenario_label}'),
    ]:
        boundary_gdf.to_crs(gdf.crs).plot(
            ax=ax, facecolor='#f5f5f5', edgecolor='#333', linewidth=1.5)
        for cat in CAT_ORDER:
            mask = gdf['category'] == cat
            if mask.any():
                gdf[mask].plot(
                    ax=ax,
                    color=CATEGORY_COLORS.get(cat, '#BDBDBD'),
                    linewidth=0.7, alpha=0.8
                )
        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.axis('off')

    # Legend
    legend_patches = [
        mpatches.Patch(color=CATEGORY_COLORS.get(c, '#BDBDBD'), label=c)
        for c in CAT_ORDER
        if osm_gdf['category'].eq(c).any() or official_gdf['category'].eq(c).any()
    ]
    fig.legend(handles=legend_patches, loc='lower center',
               ncol=3, fontsize=10, frameon=True, bbox_to_anchor=(0.5, 0.01))
    plt.suptitle('Barcelona Cycling Network: OSM vs Official', fontsize=16,
                 fontweight='bold', y=1.01)
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f'  Map saved: {save_path}')
    plt.show()

def plot_bar_comparison(km_df, save_path=None):
    """Horizontal bar chart comparing OSM and Official km per category."""
    cats  = [r['category'] for _, r in km_df.iterrows() if r['osm_km'] > 0 or
             (r['official_km'] is not None and not pd.isna(r['official_km']) and r['official_km'] > 0)]
    y     = np.arange(len(cats))
    osm_v = [km_df.loc[km_df['category'] == c, 'osm_km'].values[0] for c in cats]
    off_v = [km_df.loc[km_df['category'] == c, 'official_km'].values[0]
             if not pd.isna(km_df.loc[km_df['category'] == c, 'official_km'].values[0]) else 0
             for c in cats]

    fig, ax = plt.subplots(figsize=(10, 7))
    ax.barh(y - 0.2, osm_v, height=0.38, color='#1E88E5', alpha=0.85, label='OSM')
    ax.barh(y + 0.2, off_v, height=0.38, color='#43A047', alpha=0.85, label='Official')
    ax.set_yticks(y)
    ax.set_yticklabels(cats, fontsize=10)
    ax.set_xlabel('km', fontsize=11)
    ax.set_title('Barcelona: OSM vs Official km by Category', fontsize=13, fontweight='bold')
    ax.legend(fontsize=10)
    ax.grid(axis='x', alpha=0.3)
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f'  Bar chart saved: {save_path}')
    plt.show()

def save_csvs(km_a, km_b, coverage_a, coverage_b, mx_a, mx_b, output_dir):
    """Save all comparison tables as CSV files."""
    km_a.to_csv(os.path.join(output_dir, 'bcn_km_comparison_A.csv'), index=False)
    km_b.to_csv(os.path.join(output_dir, 'bcn_km_comparison_B.csv'), index=False)
    coverage_a.to_csv(os.path.join(output_dir, 'bcn_coverage_A.csv'), index=False)
    coverage_b.to_csv(os.path.join(output_dir, 'bcn_coverage_B.csv'), index=False)
    if len(mx_a) > 0:
        mx_a.to_csv(os.path.join(output_dir, 'bcn_mismatches_A.csv'), index=False)
    if len(mx_b) > 0:
        mx_b.to_csv(os.path.join(output_dir, 'bcn_mismatches_B.csv'), index=False)
    print(f'✅ CSVs saved to {output_dir}')