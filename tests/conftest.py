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
    CyclingLegalConfig,
    classify_area_road,
    get_cycling_config,
)

_AREA_SUPPORTED_COUNTRIES = {'spain', 'netherlands', 'germany', 'belgium', 'denmark'}

_results = []
_area_results = []


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


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    if _results:
        lines = []
        for status, case in _results:
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
        for status, case in _area_results:
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
