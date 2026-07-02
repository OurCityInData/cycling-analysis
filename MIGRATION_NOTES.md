# Migration notes: notebooks -> package + scripts

Source notebooks: `amsterdam_vs_official.ipynb`, `barcelona_vs_official.ipynb`,
`barcelona_vs_bikeneat.ipynb`, `freiburg_vs_bikeneat.ipynb`,
`cycling_country_analysis.ipynb`, `classifier_accuracy_test.ipynb`.

## What actually got deduplicated, and what didn't (on purpose)

Diffing every duplicated-looking function across notebooks (not just comparing
names) turned up two different situations that needed different treatment:

**Genuinely identical, safe to merge into one shared function:**
- `fix_geometry`, `clean_isolated_edges`, `remove_small_components` -> `cycling_analysis/geometry.py`
- `has_cycling_infrastructure` (the NL06 filter) - identical across
  `amsterdam_vs_official.ipynb`, `barcelona_vs_bikeneat.ipynb`, and
  `freiburg_vs_bikeneat.ipynb` -> `cycling_analysis/classification.py`
- The entire BikeNEAT predicate engine + `set_value` + `run_bikeneat` -
  byte-identical (modulo comments) between `barcelona_vs_bikeneat.ipynb` and
  `freiburg_vs_bikeneat.ipynb` -> `cycling_analysis/bikeneat.py`. This is the
  single biggest win: those two notebooks are now ONE script
  (`compare_vs_bikeneat.py --city ...`).

**Look identical by name, but encode real, different domain logic - kept
separate rather than force-merged:**
- `classify_cycling_path` (Amsterdam/NL06 taxonomy) vs.
  `classify_cycling_path_bcn` (Barcelona/AMB taxonomy): different category
  sets, different rules (e.g. Barcelona has maxspeed-based categories with no
  NL equivalent; Amsterdam infers cycling access on unmarked paths under NL
  law, Barcelona does not under Spanish law). Merging these into one
  config-driven function would either drop a country-specific rule or need a
  config schema as complex as just having two functions. See
  `classification.py`'s module docstring for the full reasoning.
- `amsterdam.py` and `barcelona.py` (metrics + plotting): the two notebooks'
  `compute_km_table` / `spatial_overlap_analysis` / `plot_comparison` etc. have
  the same *purpose* but different *interfaces* (dict-of-categories vs. a
  `category`-column DataFrame, different column names). Rather than guess at
  a forced-generic interface I can't test against real data, each city kept
  its own module. `classify_road` in `country.py` is a third, independent
  taxonomy for the same reason - nationwide coarse screening, not a
  single-city high-precision comparison.

## Real bugs found during the diff (not introduced by this migration)

1. **Stale notebook output vs. source drift.** `barcelona_vs_official.ipynb`'s
   copy of `classify_cycling_path_bcn` was missing a maxspeed-based fallback
   branch that exists in `classifier_accuracy_test.ipynb`'s copy of the same
   function. The two were supposed to be identical ("copied verbatim" per the
   test notebook's own comment) but had drifted from being edited
   independently. This package uses the newer, maxspeed-fallback version
   (the one the accuracy test was written to validate).

2. **The accuracy test notebook's displayed output was stale relative to its
   own source.** The last *saved run* of `classifier_accuracy_test.ipynb`
   shows 4/8 (50%) accuracy. But the *current source* in that same notebook
   already contains the maxspeed fix, which fixes 2 of those 4 failures - the
   notebook was edited after its last execution and never re-run, so the
   displayed output didn't match the code. `tests/test_classification.py`
   runs the current source and gets 6/8. This kind of silent staleness is
   exactly the class of bug that notebooks make easy to miss and scripts +
   tests make visible.

3. **Unresolved gap, now tracked instead of hidden:** 2 of the 8 ground-truth
   cases still fail even with the fix - both are `highway=footway` sidewalks
   with no explicit `bicycle` tag that real-world ground truth says should
   count as cycling infrastructure, but `has_cycling_infrastructure_bcn`
   filters out because it requires an explicit tag on footways. This was true
   in the original notebook too; it's just now a visible `xfail` in the test
   suite (`tests/test_classification.py`) instead of an easy-to-miss line in
   a results table.

## What is NOT verified

This environment had no PBF files or official GeoJSON datasets available (the
uploads were notebooks only), so none of the GIS pipelines
(`load_osm_data`, `run_bikeneat`, `spatial_overlap_analysis`, etc.) could be
executed end-to-end against real data. All ported code was verified by:
- `ast.parse` / `py_compile` syntax checking on every file,
- line-by-line diffing against the original notebook source for every
  "shared" function before merging it,
- actually running `tests/test_classification.py`'s logic (the one piece with
  no GIS dependencies) against the real ground-truth tag data, which is what
  surfaced the two bugs above.

Before trusting this for real analysis: run each script once against the same
input data the original notebook used, and diff the output CSVs / km totals
against a saved notebook run to confirm parity, especially for
`amsterdam.py`/`barcelona.py`'s metrics and plotting functions, which were
ported with light interface cleanup (parameterizing what used to be
notebook-global constants like `SPATIAL_BUFFER_M`, `CLEAN_ISOLATED`).

## Left behind intentionally

- Barcelona's "diagnostic pipeline maps" cell at the end of
  `barcelona_vs_official.ipynb` (re-running intermediate pyrosm stages purely
  to plot them) and the `_hw_col`/`HW_COLORS` helper it used - one-off
  exploratory scratch work, not reusable pipeline logic.
- Hardcoded absolute paths from the original notebooks
  (`/Users/joelps/barcelona-cycling/...`) were replaced with `--data-dir`
  / `--output-dir` / `--official-path` / `--pbf-path` CLI args with relative
  defaults, so the scripts aren't tied to one machine.
