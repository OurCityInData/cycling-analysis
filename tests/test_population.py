"""
Unit tests for cycling_analysis/population.py's pure-computation part
(compute_population_stats) - no network I/O (fetch_population_raster hits
WorldPop directly and is validated manually, same as the rest of the GIS
pipeline - see CLAUDE.md's Known gaps). Uses an in-memory synthetic raster
rather than a real WorldPop download.
"""

import os
import sys

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cycling_analysis.population import compute_population_stats


def _write_raster(path, data, nodata=-99999.0):
    transform = from_origin(0, 10, 1, 1)  # 1-degree pixels, origin at (0, 10)
    with rasterio.open(
        path, 'w', driver='GTiff', height=data.shape[0], width=data.shape[1],
        count=1, dtype='float32', crs='EPSG:4326', transform=transform, nodata=nodata,
    ) as dst:
        dst.write(data.astype('float32'), 1)


def test_compute_population_stats_sums_pixels_in_polygon(tmp_path):
    # 10x10 grid, every pixel = 5.0 -> a whole-grid polygon should sum to 500.
    data = np.full((10, 10), 5.0)
    raster_path = tmp_path / 'pop.tif'
    _write_raster(raster_path, data)

    whole_grid = box(0, 0, 10, 10)
    quarter_grid = box(0, 5, 5, 10)  # top-left 5x5 quarter -> 25 pixels * 5.0 = 125
    gdf = gpd.GeoDataFrame(
        {'name': ['whole', 'quarter']}, geometry=[whole_grid, quarter_grid], crs='EPSG:4326',
    )

    result = compute_population_stats(gdf, raster_path)

    assert list(result.columns) == ['name', 'geometry', 'population']
    assert result.loc[result['name'] == 'whole', 'population'].iloc[0] == 500.0
    assert result.loc[result['name'] == 'quarter', 'population'].iloc[0] == 125.0


def test_compute_population_stats_treats_nodata_as_zero(tmp_path):
    data = np.full((4, 4), -99999.0)  # entirely nodata
    raster_path = tmp_path / 'pop_nodata.tif'
    _write_raster(raster_path, data)

    gdf = gpd.GeoDataFrame({'name': ['all_nodata']}, geometry=[box(0, 0, 4, 4)], crs='EPSG:4326')

    result = compute_population_stats(gdf, raster_path)

    assert result.loc[0, 'population'] == 0.0


def test_compute_population_stats_does_not_mutate_input(tmp_path):
    data = np.full((2, 2), 1.0)
    raster_path = tmp_path / 'pop_small.tif'
    _write_raster(raster_path, data)

    gdf = gpd.GeoDataFrame({'name': ['a']}, geometry=[box(0, 8, 2, 10)], crs='EPSG:4326')

    compute_population_stats(gdf, raster_path)

    assert 'population' not in gdf.columns
