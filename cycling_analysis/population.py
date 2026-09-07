"""
Population columns for the country-level screening pipeline
(cycling_analysis/country.py's run_country_analysis()). Adds `Population`
and `Population density (per km²)` to every municipality row, across all 5
configured countries (no per-country gate like pollution.py's
POLLUTION_COUNTRIES - this data source covers every country uniformly).

Source: WorldPop (University of Southampton), Global 2015-2030 R2024B
constrained 100m population-count rasters
(https://www.worldpop.org/methods/populations/), one GeoTIFF per country
(CC BY 4.0). "Constrained" means the raster is masked to modelled building
footprints rather than spread evenly across a country's whole area, which
matters for zonal-summing over small municipality polygons. These are
modelled estimates, not census counts - same "not flagged as inferred"
caveat as the pollution columns' fallback tiers (see CLAUDE.md).

Population is computed once per country/run by zonal-summing the raster
directly over each GADM municipality polygon (rasterstats.zonal_stats,
stat='sum') - the same shape as how `Municipality area (km²)` is computed
straight from GADM geometry in country.py, not a per-row external-dataset
join like the pollution columns. Zonal stats run in the GDF's native WGS84
CRS (matching the raster's CRS), not the UTM-reprojected copy used for area/
pollution distance calcs.
"""

from pathlib import Path

import geopandas as gpd
import requests
from rasterstats import zonal_stats

POPULATION_COLUMNS = ['Population', 'Population density (per km²)']

# {iso3: iso3_lowercase} - WorldPop's URL path uses the country code in both
# cases in different segments. Verified live for all 5 configured countries
# (2026-07): ESP/NLD/DEU/BEL/DNK all resolve under this exact pattern.
WORLDPOP_URL_TEMPLATE = (
    'https://data.worldpop.org/GIS/Population/Global_2015_2030/R2024B/2020/'
    '{iso3}/v1/100m/constrained/{iso3_lower}_pop_2020_CN_100m_R2024B_v1.tif'
)


def fetch_population_raster(iso3: str, data_dir: Path) -> Path:
    """
    Downloads (if not already cached) the WorldPop constrained 100m
    population raster for one country, ISO3 code (e.g. 'ESP'). Cached
    locally after first download, same pattern as load_gadm_municipalities()
    and download_pbf() in country.py.
    """
    data_dir = Path(data_dir)
    cache = data_dir / f'worldpop_{iso3}_2020_100m.tif'
    if cache.exists():
        return cache

    url = WORLDPOP_URL_TEMPLATE.format(iso3=iso3, iso3_lower=iso3.lower())
    print(f'Downloading WorldPop population raster for {iso3} ...')
    r = requests.get(url, stream=True, timeout=300)
    r.raise_for_status()
    with open(cache, 'wb') as f:
        for block in r.iter_content(chunk_size=4 * 1024 * 1024):
            f.write(block)
    return cache


def compute_population_stats(municipalities_gdf: gpd.GeoDataFrame, raster_path: Path) -> gpd.GeoDataFrame:
    """
    Zonal-sums the WorldPop raster over each municipality polygon and adds a
    `population` column. Expects municipalities_gdf in its native WGS84 CRS
    (the raster's CRS) - NOT the UTM-reprojected copy used elsewhere in
    country.py for area/distance math. Returns a copy; does not mutate the
    input in place.
    """
    stats = zonal_stats(
        municipalities_gdf.geometry, str(raster_path), stats=['sum'], nodata=-99999,
    )
    out = municipalities_gdf.copy()
    out['population'] = [s['sum'] or 0.0 for s in stats]
    return out
