"""
Geometry cleanup utilities shared by every city comparison.

Ported from amsterdam_vs_official.ipynb and barcelona_vs_official.ipynb,
where these three functions were duplicated near-verbatim (confirmed via
diff: only cosmetic differences — f-string quoting, a docstring, a
`bool(...)` simplification — no logic differences).
"""

import networkx as nx
from shapely.geometry import Point
from shapely.ops import linemerge
from shapely.strtree import STRtree


def fix_geometry(geom):
    """Collapse a MultiLineString into a single LineString where possible."""
    if geom is None:
        return None
    if geom.geom_type == 'LineString':
        return geom
    if geom.geom_type == 'MultiLineString':
        return linemerge(geom)
    return geom


def clean_isolated_edges(edges_gdf, utm_crs, min_length_m=50.0, endpoint_tol_m=1.0):
    """
    Drop short edge fragments (< min_length_m) whose endpoints don't touch
    any other edge within endpoint_tol_m — i.e. genuinely isolated slivers,
    as opposed to short-but-connected segments.
    """
    gdf = edges_gdf.to_crs(utm_crs).copy()
    lengths = gdf.geometry.length.values
    all_geoms = list(gdf.geometry)
    tree = STRtree(all_geoms)
    keep = []
    n_dropped, km_dropped = 0, 0.0
    for pos, (idx, row) in enumerate(gdf.iterrows()):
        length = lengths[pos]
        if length >= min_length_m:
            keep.append(True)
            continue
        try:
            coords = list(row.geometry.coords)
            start_pt = Point(coords[0])
            end_pt = Point(coords[-1])
        except Exception:
            keep.append(True)
            continue
        start_hits = [i for i in tree.query(start_pt.buffer(endpoint_tol_m)) if i != pos]
        end_hits = [i for i in tree.query(end_pt.buffer(endpoint_tol_m)) if i != pos]
        keep.append(bool(start_hits or end_hits))
        if not keep[-1]:
            n_dropped += 1
            km_dropped += length
    gdf_clean = gdf[keep].to_crs(edges_gdf.crs)
    print(f'  Cleaned {n_dropped} isolated micro-fragments ({km_dropped/1000:.2f} km removed)')
    return gdf_clean


def remove_small_components(edges_gdf, utm_crs, min_component_km=0.5, snap_m=2.0):
    """
    Remove edges that belong to small isolated subgraphs.

    The proximity-based clean_isolated_edges() keeps fragments that are spatially
    close to other edges but not actually connected — common in dense networks.
    This function builds the actual topology graph, finds every connected
    component, and drops components whose total length is below min_component_km.

    snap_m: endpoints within this distance are treated as the same node, handling
            minor GPS / digitization offsets between adjacent OSM ways.
    """
    gdf = edges_gdf.to_crs(utm_crs).copy()
    G = nx.Graph()

    def snap_node(xy, precision=snap_m):
        return (round(xy[0] / precision), round(xy[1] / precision))

    for i, row in enumerate(gdf.itertuples(index=False)):
        try:
            coords = list(row.geometry.coords)
            u = snap_node(coords[0])
            v = snap_node(coords[-1])
            G.add_edge(u, v, pos=i, length=row.geometry.length)
        except Exception:
            pass

    # Identify which nodes are in "large" components (>= min_component_km)
    keep_nodes = set()
    n_components = nx.number_connected_components(G)
    for comp in nx.connected_components(G):
        subG = G.subgraph(comp)
        total_km = sum(d['length'] for _, _, d in subG.edges(data=True)) / 1000
        if total_km >= min_component_km:
            keep_nodes.update(comp)

    keep = []
    for row in gdf.itertuples(index=False):
        try:
            coords = list(row.geometry.coords)
            u = snap_node(coords[0])
            v = snap_node(coords[-1])
            keep.append(u in keep_nodes or v in keep_nodes)
        except Exception:
            keep.append(True)

    result = gdf[keep].to_crs(edges_gdf.crs)
    removed = len(gdf) - len(result)
    print(f"  Component filter: {n_components} components → removed {removed} edges"
          f" in components < {min_component_km} km ({len(result)} remaining)")
    return result
