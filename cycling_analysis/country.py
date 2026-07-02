"""
Country-level cycling-infrastructure screening.

Ported from cycling_country_analysis.ipynb. This is a THIRD, independent
classify_road() taxonomy — not the same as classify_cycling_path_amsterdam
or classify_cycling_path_bcn in classification.py. It exists for a
different purpose: coarse, nationwide screening across every municipality
in a country/region (via GADM boundaries), not a single-city high-precision
comparison against an official reference dataset. Kept as its own module
rather than forced into classification.py.

The original notebook processed one region/country per run via two
module-level variables you edited by hand (COUNTRY, FILTER_REGIONS) and
wrote a bare CHECKPOINT_PATH/RUN_NAME csv into the working directory.
run_country_analysis() below is the same municipality-by-municipality,
checkpoint-resumable loop, but as a function taking those as parameters
(see scripts/country_analysis.py for the CLI).
"""

import gc
import io
import os
import subprocess
import unicodedata
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from pyrosm import OSM

# ── Sub-region PBF sources ───────────────────────────────────────────────────
# Each entry: (sub_region_name, geofabrik_url). sub_region_name must match the
# GADM province_col value for that region (see GADM_CONFIG below) — mismatches
# cause that region to be silently skipped, so verify against
# load_gadm_municipalities() output before a big run.
REGIONS = {
    'spain': [
        ('Andalucía',         'https://download.geofabrik.de/europe/spain/andalucia-latest.osm.pbf'),
        ('Aragón',            'https://download.geofabrik.de/europe/spain/aragon-latest.osm.pbf'),
        ('Asturias',          'https://download.geofabrik.de/europe/spain/asturias-latest.osm.pbf'),
        ('Cantabria',         'https://download.geofabrik.de/europe/spain/cantabria-latest.osm.pbf'),
        ('Castilla-La Mancha','https://download.geofabrik.de/europe/spain/castilla-la-mancha-latest.osm.pbf'),
        ('Castilla y León',   'https://download.geofabrik.de/europe/spain/castilla-y-leon-latest.osm.pbf'),
        ('Cataluña',          'https://download.geofabrik.de/europe/spain/cataluna-latest.osm.pbf'),
        ('Extremadura',       'https://download.geofabrik.de/europe/spain/extremadura-latest.osm.pbf'),
        ('Galicia',           'https://download.geofabrik.de/europe/spain/galicia-latest.osm.pbf'),
        ('Islas Baleares',    'https://download.geofabrik.de/europe/spain/islas-baleares-latest.osm.pbf'),
        ('La Rioja',          'https://download.geofabrik.de/europe/spain/la-rioja-latest.osm.pbf'),
        ('Madrid',            'https://download.geofabrik.de/europe/spain/madrid-latest.osm.pbf'),
        ('Murcia',            'https://download.geofabrik.de/europe/spain/murcia-latest.osm.pbf'),
        ('Navarra',           'https://download.geofabrik.de/europe/spain/navarra-latest.osm.pbf'),
        ('País Vasco',        'https://download.geofabrik.de/europe/spain/pais-vasco-latest.osm.pbf'),
        ('Valencia',          'https://download.geofabrik.de/europe/spain/valencia-latest.osm.pbf'),
    ],
    'netherlands': [
        ('Groningen',     'https://download.geofabrik.de/europe/netherlands/groningen-latest.osm.pbf'),
        ('Friesland',     'https://download.geofabrik.de/europe/netherlands/friesland-latest.osm.pbf'),
        ('Drenthe',       'https://download.geofabrik.de/europe/netherlands/drenthe-latest.osm.pbf'),
        ('Overijssel',    'https://download.geofabrik.de/europe/netherlands/overijssel-latest.osm.pbf'),
        ('Flevoland',     'https://download.geofabrik.de/europe/netherlands/flevoland-latest.osm.pbf'),
        ('Gelderland',    'https://download.geofabrik.de/europe/netherlands/gelderland-latest.osm.pbf'),
        ('Utrecht',       'https://download.geofabrik.de/europe/netherlands/utrecht-latest.osm.pbf'),
        ('Noord-Holland', 'https://download.geofabrik.de/europe/netherlands/noord-holland-latest.osm.pbf'),
        ('Zuid-Holland',  'https://download.geofabrik.de/europe/netherlands/zuid-holland-latest.osm.pbf'),
        ('Zeeland',       'https://download.geofabrik.de/europe/netherlands/zeeland-latest.osm.pbf'),
        ('Noord-Brabant', 'https://download.geofabrik.de/europe/netherlands/noord-brabant-latest.osm.pbf'),
        ('Limburg',       'https://download.geofabrik.de/europe/netherlands/limburg-latest.osm.pbf'),
    ],
    'germany': [
        ('Baden-Württemberg',      'https://download.geofabrik.de/europe/germany/baden-wuerttemberg-latest.osm.pbf'),
        ('Bayern',                 'https://download.geofabrik.de/europe/germany/bayern-latest.osm.pbf'),
        ('Berlin',                 'https://download.geofabrik.de/europe/germany/berlin-latest.osm.pbf'),
        ('Brandenburg',            'https://download.geofabrik.de/europe/germany/brandenburg-latest.osm.pbf'),
        ('Bremen',                 'https://download.geofabrik.de/europe/germany/bremen-latest.osm.pbf'),
        ('Hamburg',                'https://download.geofabrik.de/europe/germany/hamburg-latest.osm.pbf'),
        ('Hessen',                 'https://download.geofabrik.de/europe/germany/hessen-latest.osm.pbf'),
        ('Mecklenburg-Vorpommern', 'https://download.geofabrik.de/europe/germany/mecklenburg-vorpommern-latest.osm.pbf'),
        ('Niedersachsen',          'https://download.geofabrik.de/europe/germany/niedersachsen-latest.osm.pbf'),
        ('Nordrhein-Westfalen',    'https://download.geofabrik.de/europe/germany/nordrhein-westfalen-latest.osm.pbf'),
        ('Rheinland-Pfalz',        'https://download.geofabrik.de/europe/germany/rheinland-pfalz-latest.osm.pbf'),
        ('Saarland',               'https://download.geofabrik.de/europe/germany/saarland-latest.osm.pbf'),
        ('Sachsen',                'https://download.geofabrik.de/europe/germany/sachsen-latest.osm.pbf'),
        ('Sachsen-Anhalt',         'https://download.geofabrik.de/europe/germany/sachsen-anhalt-latest.osm.pbf'),
        ('Schleswig-Holstein',     'https://download.geofabrik.de/europe/germany/schleswig-holstein-latest.osm.pbf'),
        ('Thüringen',              'https://download.geofabrik.de/europe/germany/thueringen-latest.osm.pbf'),
    ],
    # Geofabrik has NO sub-regions for Belgium -> URLs are None; the country
    # PBF (COUNTRY_PBFS) is downloaded once and osmium-extracted per municipality.
    'belgium': [
        ('Antwerp',         None),
        ('East Flanders',   None),
        ('West Flanders',   None),
        ('Flemish Brabant', None),
        ('Brussels',        None),
        ('Hainaut',         None),
        ('Liège',           None),
        ('Limburg',         None),
        ('Luxembourg',      None),
        ('Namur',           None),
        ('Walloon Brabant', None),
    ],
    'denmark': [
        ('Nordjylland', 'https://download.geofabrik.de/europe/denmark/nordjylland-latest.osm.pbf'),
        ('Midtjylland', 'https://download.geofabrik.de/europe/denmark/midtjylland-latest.osm.pbf'),
        ('Syddanmark',  'https://download.geofabrik.de/europe/denmark/syddanmark-latest.osm.pbf'),
        ('Sjælland',    'https://download.geofabrik.de/europe/denmark/sjaelland-latest.osm.pbf'),
        ('Hovedstaden', 'https://download.geofabrik.de/europe/denmark/hovedstaden-latest.osm.pbf'),
    ],
}

COUNTRY_PBFS = {
    'belgium': 'https://download.geofabrik.de/europe/belgium-latest.osm.pbf',
}

GADM_CODES = {
    'spain': 'ESP', 'netherlands': 'NLD', 'germany': 'DEU',
    'belgium': 'BEL', 'denmark': 'DNK',
}

GADM_CONFIG = {
    'spain':       {'level': 4, 'province_col': 'NAME_1', 'muni_col': 'NAME_4'},
    'netherlands': {'level': 2, 'province_col': 'NAME_1', 'muni_col': 'NAME_2'},
    'germany':     {'level': 2, 'province_col': 'NAME_1', 'muni_col': 'NAME_2'},
    'belgium':     {'level': 4, 'province_col': 'NAME_2', 'muni_col': 'NAME_4'},
    'denmark':     {'level': 2, 'province_col': 'NAME_1', 'muni_col': 'NAME_2'},
}

# UTM zone per country. EPSG:32631 covers Spain/Netherlands/Belgium/France;
# use EPSG:32632 for Denmark/eastern Germany if you add those runs.
UTM_CRS_BY_COUNTRY = {
    'spain': 'EPSG:32631', 'netherlands': 'EPSG:32631',
    'belgium': 'EPSG:32631', 'germany': 'EPSG:32632', 'denmark': 'EPSG:32632',
}

CATEGORIES = [
    'greenway_parks', 'two_way_side_lane', 'one_way_side_lane',
    'bike_lane_on_sidewalk', 'bus_bike_lane', 'contraflow',
    'fietsstraat_2_1', 'calmed_zone_10', 'calmed_street_20',
    'shared_street_30', 'service_road', 'car_road',
]

CATEGORY_LABELS = {
    'greenway_parks':        'Greenway (parks)',
    'two_way_side_lane':     'Two-way side bike lane',
    'one_way_side_lane':     'One-way side bike lane',
    'bike_lane_on_sidewalk': 'Bike lane on sidewalk',
    'bus_bike_lane':         'Bus-bike lane',
    'contraflow':            'Contraflow cycling',
    'fietsstraat_2_1':       '2-1 road',
    'calmed_zone_10':        'Calmed zone at 10 km/h',
    'calmed_street_20':      'Calmed street at 20 km/h',
    'shared_street_30':      'Shared street at 30 km/h',
    'service_road':          'Service road',
    'car_road':              'Car road',
}

GREEN_LANDUSE = {'park', 'forest', 'nature_reserve', 'recreation_ground',
                  'meadow', 'grass', 'village_green'}
GREEN_LEISURE = {'park', 'garden', 'nature_reserve', 'recreation_ground'}
GREEN_NATURAL = {'wood', 'scrub', 'heath', 'grassland'}

CYCLING_TAGS = [
    'cycleway', 'cycleway:left', 'cycleway:right', 'cycleway:both',
    'bicycle', 'foot', 'segregated', 'busway',
    'cyclestreet', 'bicycle_road', 'access', 'oneway:bicycle',
]

EXCLUDED_HW = {
    'motorway', 'motorway_link',
    'proposed', 'construction', 'raceway',
    'abandoned', 'razed', 'disused',
}

CAR_ROADS_HW = {
    'trunk', 'trunk_link',
    'primary', 'primary_link',
    'secondary', 'secondary_link',
    'tertiary', 'tertiary_link',
    'residential', 'unclassified', 'road',
}

MAX_SEGMENTS = 30_000


def ascii_slug(s: str) -> str:
    """Strip accents so filenames stay ASCII-safe (e.g. 'Cataluña' -> 'cataluna')."""
    return unicodedata.normalize('NFD', s).encode('ascii', 'ignore').decode().lower().replace(' ', '_')


def load_gadm_municipalities(country: str, data_dir: Path) -> gpd.GeoDataFrame:
    """
    Downloads GADM boundaries at the configured level. Drops rows where the
    municipality name column is null (GADM sometimes has null names for
    aggregate polygons). Cached locally after first download.
    """
    code = GADM_CODES[country]
    level = GADM_CONFIG[country]['level']
    mcol = GADM_CONFIG[country]['muni_col']
    cache = data_dir / f'gadm41_{code}_{level}.geojson'

    if cache.exists():
        print(f'Cached: {cache.name}')
        gdf = gpd.read_file(cache)
    else:
        url = f'https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_{code}_{level}.json.zip'
        print(f'Downloading GADM level-{level} for {code} ...')
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            json_file = [n for n in z.namelist() if n.lower().endswith('.json')][0]
            gdf = gpd.read_file(z.open(json_file))
        gdf.to_file(cache, driver='GeoJSON')
        print(f'Downloaded {len(gdf)} rows -> cached to {cache.name}')

    before = len(gdf)
    gdf = gdf[gdf[mcol].notna()].copy()
    if before - len(gdf) > 0:
        print(f'Dropped {before - len(gdf)} rows with null {mcol}')
    return gdf


def download_pbf(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    r = requests.get(url, stream=True, timeout=300)
    r.raise_for_status()
    total = int(r.headers.get('content-length', 0))
    downloaded = 0
    with open(dest, 'wb') as f:
        for block in r.iter_content(chunk_size=4 * 1024 * 1024):
            f.write(block)
            downloaded += len(block)
            if total:
                print(f'\r  {downloaded * 100 // total:3d}%', end='', flush=True)
    print()
    return dest


def osmium_extract(src_pbf: str, bbox, dest_pbf: str) -> bool:
    minx, miny, maxx, maxy = bbox
    cmd = (
        f'osmium extract '
        f'-b {minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f} '
        f'"{src_pbf}" -o "{dest_pbf}" --overwrite'
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.returncode == 0


def get_green_polygons(osm_obj, crs) -> gpd.GeoDataFrame:
    try:
        lu = osm_obj.get_landuse()
        mask = (
            lu['landuse'].isin(GREEN_LANDUSE)
            | lu['leisure'].isin(GREEN_LEISURE)
            | lu['natural'].isin(GREEN_NATURAL)
        )
        polys = lu[mask][['geometry']].copy()
        polys = polys[polys.geometry.is_valid]
        polys = polys[polys.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])]
        if len(polys) == 0:
            return gpd.GeoDataFrame(geometry=[], crs=crs)
        return polys.to_crs(crs).dissolve()[['geometry']].reset_index(drop=True)
    except Exception:
        return gpd.GeoDataFrame(geometry=[], crs=crs)


def tag_greenway(roads_utm: gpd.GeoDataFrame, green_dissolved: gpd.GeoDataFrame) -> pd.Series:
    if len(green_dissolved) == 0:
        return pd.Series(False, index=roads_utm.index)
    centroids = roads_utm[['geometry']].copy()
    centroids['geometry'] = roads_utm.geometry.centroid
    sj = gpd.sjoin(centroids.reset_index(), green_dissolved, how='left', predicate='within')
    in_green = set(sj.loc[sj['index_right'].notna(), 'index'].tolist())
    return roads_utm.index.isin(in_green)


def _v(row, field: str) -> str:
    v = row.get(field, None)
    return '' if (v is None or str(v) in ('nan', 'None', '')) else str(v).lower().strip()


def parse_maxspeed(v: str):
    if not v:
        return None
    if v in ('walk', 'foot', 'signing'):
        return 5
    if ':' in v:
        v = v.split(':')[-1]
    try:
        return int(float(v.split()[0]))
    except (ValueError, IndexError):
        return None


def classify_road(row):
    """Coarse nationwide classifier - distinct taxonomy from classification.py."""
    hw = _v(row, 'highway')
    bicycle = _v(row, 'bicycle')
    access = _v(row, 'access')
    busway = _v(row, 'busway')
    cw = _v(row, 'cycleway')
    cw_left = _v(row, 'cycleway:left')
    cw_right = _v(row, 'cycleway:right')
    cw_both = _v(row, 'cycleway:both')
    cyclestreet = _v(row, 'cyclestreet')
    bicy_road = _v(row, 'bicycle_road')
    maxspeed = parse_maxspeed(_v(row, 'maxspeed'))
    oneway = _v(row, 'oneway')
    ow_bicycle = _v(row, 'oneway:bicycle')
    in_green = bool(row.get('in_green_space', False))

    if hw in EXCLUDED_HW:
        return None
    if bicycle in ('no', 'dismount') and hw not in ('cycleway',):
        return None
    if access == 'no' and bicycle not in ('yes', 'designated', 'permissive'):
        return None

    if in_green and hw in ('cycleway', 'path', 'footway', 'track') and bicycle != 'no':
        return 'greenway_parks'
    if cw_both in ('track', 'lane', 'yes'):
        return 'two_way_side_lane'
    if cw_left in ('track', 'lane') and cw_right in ('track', 'lane'):
        return 'two_way_side_lane'
    if hw == 'cycleway' and oneway not in ('yes', '1', '-1', 'true'):
        return 'two_way_side_lane'
    if cw in ('track', 'lane', 'shared_lane'):
        return 'one_way_side_lane'
    if cw_right in ('track', 'lane', 'yes') or cw_left in ('track', 'lane', 'yes'):
        return 'one_way_side_lane'
    if hw == 'cycleway' and oneway in ('yes', '1', 'true'):
        return 'one_way_side_lane'
    if hw in ('footway', 'path', 'pedestrian') and bicycle in ('yes', 'designated', 'permissive'):
        return 'bike_lane_on_sidewalk'
    if busway in ('lane', 'yes') and bicycle not in ('no',):
        return 'bus_bike_lane'
    if cw in ('opposite_lane', 'opposite_track', 'opposite'):
        return 'contraflow'
    if oneway in ('yes', '1', 'true') and ow_bicycle in ('no', '-1'):
        return 'contraflow'
    if cyclestreet in ('yes', '1', 'true') or bicy_road in ('yes', '1', 'true'):
        return 'fietsstraat_2_1'
    if hw == 'living_street':
        return 'calmed_zone_10'
    if maxspeed is not None and maxspeed <= 10:
        return 'calmed_zone_10'
    if maxspeed == 20:
        return 'calmed_street_20'
    if maxspeed == 30:
        return 'shared_street_30'
    if hw == 'service' and bicycle not in ('no',) and access not in ('no',):
        return 'service_road'
    if hw in CAR_ROADS_HW and bicycle not in ('no',):
        return 'car_road'
    if hw in ('track', 'path', 'cycleway', 'footway') and bicycle not in ('no',):
        return 'bike_lane_on_sidewalk'
    return None


def run_country_analysis(country, filter_regions=None, data_dir=Path('data'),
                          max_segments=MAX_SEGMENTS, run_name=None):
    """
    Municipality-by-municipality cycling infrastructure screen, resumable via
    a `{run_name}_checkpoint.csv` file (safe to Ctrl-C and re-run).

    country: one of REGIONS.keys() ('spain', 'netherlands', 'germany', 'belgium', 'denmark')
    filter_regions: list of sub-region names to run (must match GADM province_col
        values - run once with a bad name to see the diagnostic mismatch printout),
        or None for the whole country.
    Returns the summary DataFrame (also written to `{run_name}_cycling_by_municipality.csv`).
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(exist_ok=True, parents=True)
    utm_crs = UTM_CRS_BY_COUNTRY[country]

    all_regions = REGIONS[country]
    if filter_regions:
        province_list = [(name, url) for name, url in all_regions if name in filter_regions]
        missing = set(filter_regions) - {name for name, _ in province_list}
        if missing:
            print(f'WARNING: filter_regions entries not found in REGIONS config: {missing}')
            print(f'   Available: {[name for name, _ in all_regions]}')
    else:
        province_list = all_regions

    country_mode = country in COUNTRY_PBFS
    country_pbf_path = data_dir / COUNTRY_PBFS[country].split('/')[-1] if country_mode else None

    prov_col = GADM_CONFIG[country]['province_col']
    muni_col = GADM_CONFIG[country]['muni_col']

    if run_name is None:
        if filter_regions:
            region_tag = '_'.join(ascii_slug(r) for r in sorted(filter_regions))
            run_name = f'{country}_{region_tag}'
        else:
            run_name = country

    print(f'Country        : {country}')
    print(f'Regions to run : {[name for name, _ in province_list]}')
    print(f'Mode           : {"country PBF" if country_mode else "per-region PBFs"}')
    print(f'Run name       : {run_name}')

    municipalities_gdf = load_gadm_municipalities(country, data_dir)

    gadm_names = sorted(municipalities_gdf[prov_col].unique())
    config_names = {name for name, _ in province_list}
    unmatched = config_names - set(gadm_names)
    if unmatched:
        print(f'WARNING: these region names have NO GADM match: {sorted(unmatched)}')
        print(f'   GADM {prov_col} values available: {gadm_names}')
    else:
        print(f'All {len(config_names)} region name(s) matched against GADM.')

    if country_mode and not country_pbf_path.exists():
        print(f'Downloading {country_pbf_path.name} ...')
        download_pbf(COUNTRY_PBFS[country], country_pbf_path)

    if not country_mode:
        seen_urls = set()
        for region_name, url in province_list:
            if url and url not in seen_urls:
                pbf_path = data_dir / url.split('/')[-1]
                if not pbf_path.exists():
                    print(f'Downloading {pbf_path.name} ...')
                    download_pbf(url, pbf_path)
                else:
                    print(f'Cached: {pbf_path.name}')
                seen_urls.add(url)

    checkpoint_path = f'{run_name}_checkpoint.csv'
    if os.path.exists(checkpoint_path):
        checkpoint_df = pd.read_csv(checkpoint_path)
        done_munis = set(zip(checkpoint_df['Region'], checkpoint_df['Municipality']))
        municipality_results = checkpoint_df.to_dict('records')
        print(f'Resuming - {len(municipality_results)} municipalities already done.')
    else:
        done_munis = set()
        municipality_results = []
        print('Starting fresh.')

    for p_idx, (region_name, region_url) in enumerate(province_list):
        prov_munis = municipalities_gdf[
            municipalities_gdf[prov_col].str.strip() == region_name.strip()
        ].copy()
        if len(prov_munis) == 0:
            prov_munis = municipalities_gdf[
                municipalities_gdf[prov_col].str.lower().str.strip() == region_name.lower().strip()
            ].copy()
        if len(prov_munis) == 0:
            print(f'[{p_idx+1}/{len(province_list)}] {region_name} - NO GADM MATCH')
            continue

        remaining = [
            row for _, row in prov_munis.iterrows()
            if (region_name, row[muni_col]) not in done_munis
        ]
        if not remaining:
            print(f'[{p_idx+1}/{len(province_list)}] {region_name} - already complete, skipping.')
            continue

        print(f'\n[{p_idx+1}/{len(province_list)}] {region_name} '
              f'({len(remaining)} municipalities remaining of {len(prov_munis)} total)')

        source_pbf = country_pbf_path if country_mode else data_dir / region_url.split('/')[-1]

        for m_idx, muni_row in enumerate(remaining):
            muni_name = muni_row[muni_col]
            bbox = muni_row['geometry'].bounds
            safe_name = str(muni_name).replace(' ', '_').replace('/', '_')
            temp_pbf = f'/tmp/muni_{safe_name}.osm.pbf'

            print(f'  [{m_idx+1}/{len(remaining)}] {muni_name} ...', end=' ', flush=True)
            osm = roads = cycling = green = None
            try:
                ok = osmium_extract(str(source_pbf), bbox, temp_pbf)
                if not ok:
                    raise RuntimeError('osmium extract failed')

                osm = OSM(temp_pbf)
                roads_raw = osm.get_network(network_type='all', extra_attributes=CYCLING_TAGS)
                if roads_raw is None or len(roads_raw) == 0:
                    raise ValueError('No roads found')
                if len(roads_raw) > max_segments:
                    raise ValueError(
                        f'{len(roads_raw):,} segments > max_segments={max_segments:,} '
                        f'(likely bbox overcount - skipping to avoid RAM crash)'
                    )

                roads = roads_raw.to_crs(utm_crs).copy()
                del roads_raw
                roads['length_m'] = roads.geometry.length

                green = get_green_polygons(osm, utm_crs)
                roads['in_green_space'] = tag_greenway(roads, green)
                roads['category'] = roads.apply(classify_road, axis=1)
                cycling = roads[roads['category'].notna()]

                by_cat = (
                    cycling.groupby('category')['length_m']
                    .sum().div(1000)
                    .reindex(CATEGORIES, fill_value=0).round(2)
                )
                total_km = by_cat.sum()

                result_row = {'Region': region_name, 'Municipality': muni_name}
                result_row.update(by_cat.to_dict())
                municipality_results.append(result_row)
                done_munis.add((region_name, muni_name))
                print(f'{total_km:.1f} km  ({len(cycling):,} segments)')

            except Exception as exc:
                print(f'SKIPPED - {exc}')
                result_row = {'Region': region_name, 'Municipality': muni_name}
                result_row.update({c: 0.0 for c in CATEGORIES})
                municipality_results.append(result_row)
                done_munis.add((region_name, muni_name))

            finally:
                del osm, roads, cycling, green
                gc.collect()
                try:
                    os.remove(temp_pbf)
                except FileNotFoundError:
                    pass

            pd.DataFrame(municipality_results).to_csv(checkpoint_path, index=False)

        gc.collect()

    print('\nDone.')
    summary = pd.DataFrame(municipality_results)
    cat_cols = [c for c in CATEGORIES if c in summary.columns]
    summary = summary[['Region', 'Municipality'] + cat_cols]
    summary.columns = ['Region', 'Municipality'] + [CATEGORY_LABELS[c] for c in cat_cols]

    cat_label_cols = [CATEGORY_LABELS[c] for c in cat_cols]
    summary.insert(2, 'Total Cycling Path km', summary[cat_label_cols].sum(axis=1).round(1))
    summary = summary.sort_values(['Region', 'Total Cycling Path km'], ascending=[True, False]).reset_index(drop=True)

    out_path = f'{run_name}_cycling_by_municipality.csv'
    summary.to_csv(out_path, index=False)
    print(f'Saved -> {out_path}')
    return summary
