# Cycling Infrastructure Analysis

OSM cycling-infrastructure comparison pipeline: OSM-derived cycling networks
benchmarked against official government data (Amsterdam, Barcelona) or the
BikeNEAT classifier (Barcelona, Freiburg), plus country-level screening.

Migrated from six Jupyter notebooks into a package + scripts. See
`MIGRATION_NOTES.md` for what changed and why, and what still needs
verification against real data.

## Setup

```bash
pip install -r requirements.txt
```

Also requires the `osmium` CLI tool on your PATH (`apt-get install osmium-tool`
or `brew install osmium-tool`) — used for bbox-cropping large PBF extracts.

## Layout

```
cycling_analysis/          shared library
    constants.py             cross-city constants (PLAIN_HIGHWAY, OSM_KEYS, ...)
    geometry.py               geometry cleanup (fix_geometry, remove_small_components, ...)
    classification.py         OSM-row classifiers (NL06 filter + Amsterdam/Barcelona taxonomies)
    bikeneat.py                BikeNEAT classifier (Lukawska et al. 2026), shared across cities
    io.py                       small shared download helper
    viz.py                       BikeNEAT comparison plotting
    country.py                   country/region-level screening (GADM-based)
    cities/
        amsterdam.py              Amsterdam vs. official Fietsnetwerk pipeline
        barcelona.py              Barcelona vs. official (AMB) pipeline

scripts/                    CLI entry points (replace the old notebooks)
    compare_amsterdam_vs_official.py
    compare_barcelona_vs_official.py
    compare_vs_bikeneat.py        --city barcelona|freiburg
    country_analysis.py           --country spain|netherlands|germany|belgium|denmark

tests/
    test_classification.py    ground-truth accuracy tests (from classifier_accuracy_test.ipynb)
```

## Running

```bash
# Amsterdam vs. official Fietsnetwerk (two-scenario comparison)
python scripts/compare_amsterdam_vs_official.py \
    --official-path data/geojson_lnglat.geojson \
    --pbf-path data/netherlands-latest.osm.pbf

# Barcelona vs. official (AMB)
python scripts/compare_barcelona_vs_official.py \
    --official-path data/xarxa_pedalable_barcelona.geojson \
    --pbf-path data/cataluna-latest.osm.pbf

# BikeNEAT comparison (downloads its own PBF)
python scripts/compare_vs_bikeneat.py --city freiburg
python scripts/compare_vs_bikeneat.py --city barcelona

# Country-level screening (resumable - safe to Ctrl-C and re-run)
python scripts/country_analysis.py --country spain --regions Cataluña
```

## Tests

```bash
pip install pytest
pytest tests/ -v
```

`test_classification.py` runs the Barcelona classifier against 8 real,
ground-truthed OSM ways. Two are marked `xfail` (known, pre-existing gap —
see the test file and MIGRATION_NOTES.md) so the suite stays informative
without being permanently red.
