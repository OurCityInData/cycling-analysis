import sys
import os
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cycling_analysis.classification import (
    has_cycling_infrastructure_bcn,
    classify_cycling_path_bcn,
)
from cycling_analysis.area_classification import (
    CATEGORIES,
    CyclingLegalConfig,
    classify_area_road,
    get_cycling_config,
)
from cycling_analysis.country import GADM_CONFIG, UTM_CRS_BY_COUNTRY, load_gadm_municipalities

_AREA_SUPPORTED_COUNTRIES = {'spain', 'netherlands', 'germany', 'belgium', 'denmark'}

_results = []
_area_results = []
_muni_area_results = []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call":
        return

    if hasattr(report, "wasxfail"):
        status = "XFAIL"
    elif report.passed:
        status = "PASS"
    elif report.failed:
        status = "FAIL"
    else:
        return

    if hasattr(item, "callspec") and "case" in item.callspec.params:
        _results.append((status, item.callspec.params["case"]))
    if hasattr(item, "callspec") and "area_case" in item.callspec.params:
        _area_results.append((status, item.callspec.params["area_case"]))
    if hasattr(item, "callspec") and "municipality" in item.callspec.params:
        _muni_area_results.append((status, item.callspec.params["municipality"]))


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    if _results:
        lines = []
        for status, case in sorted(_results, key=lambda r: r[1]["category"]):
            expected = case["category"]
            row = pd.Series(case["tags"])
            passes = has_cycling_infrastructure_bcn(row)
            predicted = classify_cycling_path_bcn(row) if passes else "NOT INCLUDED (filtered out)"

            lines.append(f"[{status}] {case['name']}")
            lines.append(f"  expected:  {expected}")
            lines.append(f"  got:       {predicted}")
            lines.append("")

        out = Path("output/classification_report.txt")
        out.parent.mkdir(exist_ok=True)
        out.write_text("\n".join(lines))

    if _area_results:
        lines = []
        # Group/order by the fixed CATEGORIES vocabulary (see
        # area_classification.py) rather than test collection order, so the
        # report reads as one block per output category.
        category_order = {cat: i for i, cat in enumerate(CATEGORIES)}
        sorted_area_results = sorted(
            _area_results,
            key=lambda r: category_order.get(r[1]["category"], len(CATEGORIES)),
        )
        for status, case in sorted_area_results:
            expected = case["category"]
            row = pd.Series(case["tags"])
            if case["country"] in _AREA_SUPPORTED_COUNTRIES:
                config = get_cycling_config(case["country"], case.get("municipality"))
            else:
                config = CyclingLegalConfig()
            predicted = classify_area_road(row, config)

            lines.append(f"[{status}] {case['name']}")
            lines.append(f"  expected:  {expected}")
            lines.append(f"  got:       {predicted}")
            lines.append("")

        out = Path("output/area_classification_report.txt")
        out.parent.mkdir(exist_ok=True)
        out.write_text("\n".join(lines))

    if _muni_area_results:
        from tests.test_municipality_area import DATA_DIR, ZEELAND_LAND_AREA_KM2

        gdf = load_gadm_municipalities("netherlands", DATA_DIR)
        gdf["area_km2"] = gdf.to_crs(UTM_CRS_BY_COUNTRY["netherlands"]).geometry.area.div(1e6)
        muni_col = GADM_CONFIG["netherlands"]["muni_col"]
        prov_col = GADM_CONFIG["netherlands"]["province_col"]
        zeeland = gdf[gdf[prov_col] == "Zeeland"]
        computed = dict(zip(zeeland[muni_col], zeeland["area_km2"]))

        lines = []
        for status, municipality in sorted(_muni_area_results, key=lambda r: r[1]):
            expected_km2, source = ZEELAND_LAND_AREA_KM2[municipality]
            got_km2 = computed[municipality]
            pct_diff = (got_km2 - expected_km2) / expected_km2 * 100

            lines.append(f"[{status}] {municipality}")
            lines.append(f"  expected (land area):  {expected_km2:.2f} km²  ({source})")
            lines.append(f"  got (GADM, UTM area):   {got_km2:.2f} km²")
            lines.append(f"  diff:                   {pct_diff:+.1f}%")
            lines.append("")

        computed_total = sum(computed[m] for m in ZEELAND_LAND_AREA_KM2)
        expected_total = sum(km2 for km2, _ in ZEELAND_LAND_AREA_KM2.values())
        total_pct_diff = (computed_total - expected_total) / expected_total * 100
        lines.append("[TOTAL] Zeeland (13 municipalities)")
        lines.append(f"  expected (land area):  {expected_total:.2f} km²")
        lines.append(f"  got (GADM, UTM area):   {computed_total:.2f} km²")
        lines.append(f"  diff:                   {total_pct_diff:+.1f}%")

        out = Path("output/municipality_area_report.txt")
        out.parent.mkdir(exist_ok=True)
        out.write_text("\n".join(lines))
