"""
Small shared I/O helper(s). This is intentionally tiny: the Amsterdam and
Barcelona vs.-official loaders (load_official_data, load_osm_data,
load_official_bcn, load_osm_bcn) are city-specific enough that they live in
cycling_analysis/cities/ instead - see that module's docstring.
"""

import os
import urllib.request


def download_pbf(url: str, dest_path: str) -> str:
    """
    Download a PBF file to dest_path with a simple progress printout,
    skipping the download if it's already cached on disk.

    Ported from the identical "DOWNLOAD PBF" cell duplicated in
    barcelona_vs_bikeneat.ipynb and freiburg_vs_bikeneat.ipynb.
    """
    if os.path.exists(dest_path):
        print(f"Using cached: {dest_path}  ({os.path.getsize(dest_path)//1_000_000} MB)")
        return dest_path

    print(f"Downloading {dest_path} (one-time) ...")

    def _prog(n, bs, total):
        pct = min(100, n * bs * 100 // total) if total else 0
        print(f"\r  {pct:3d}%", end="", flush=True)

    urllib.request.urlretrieve(url, dest_path, reporthook=_prog)
    print(f"\n  Done - {os.path.getsize(dest_path)//1_000_000} MB saved to {dest_path}")
    return dest_path
