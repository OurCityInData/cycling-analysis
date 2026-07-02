"""
Ground-truth accuracy test for the Barcelona classifier, converted from
classifier_accuracy_test.ipynb.

Each TEST_CASES entry is a real OSM way that was checked in person, with
tags copied straight from the iD editor, and the category confirmed on the
ground. This replaces manually re-running the notebook to eyeball a results
table: now `pytest tests/test_classification.py -v` fails loudly (and
tells you exactly which way regressed) if a future change to
has_cycling_infrastructure_bcn / classify_cycling_path_bcn breaks a
previously-correct case.

IMPORTANT: this test file is *why* we know classify_cycling_path_bcn in
barcelona_vs_official.ipynb had drifted from the version actually being
validated here — see MIGRATION_NOTES.md. cycling_analysis/classification.py
now ships the version with the maxspeed-based fallback that these test
cases actually require (case "Calmed zone at 10 km/h" (#1)/(#2) below).
"""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cycling_analysis.classification import (
    classify_cycling_path_bcn,
    has_cycling_infrastructure_bcn,
)

# Tags copied directly from the iD editor for each location checked in person.
# Add more cases by appending in the same format.
TEST_CASES = [
    dict(
        category="Two-way side bike lane",
        name="Avinguda Paral·lel (Ejemplo 1)",
        url="https://www.openstreetmap.org/#map=16/41.37500/2.15871&layers=Y",
        tags={
            "cycle_network": "ES:BCN", "highway": "cycleway", "lanes": "2",
            "lit": "yes", "name": "Avinguda Paral·lel", "oneway": "no",
            "segregated": "yes", "smoothness": "excellent",
            "surface": "asphalt", "width": "3.5",
        },
    ),
    dict(
        category="Two-way side bike lane",
        name="Passeig de Sant Joan (Ejemplo 2)",
        url="https://www.openstreetmap.org/edit#map=18/41.393305/2.177892",
        tags={
            "cycle_network": "ES:BCN", "cycleway:surface": "asphalt",
            "highway": "cycleway", "lanes": "2", "layer": "1", "lit": "yes",
            "motor_vehicle": "no", "name": "Passeig de Sant Joan",
            "oneway": "no", "segregated": "yes", "surface": "asphalt", "width": "2",
        },
    ),
    dict(
        category="One-way side bike lane",
        name="Carrer de Manso",
        url="https://www.openstreetmap.org/#map=20/41.3774395/2.1615654&layers=Y",
        tags={
            "cycleway:surface": "asphalt", "highway": "cycleway", "lit": "yes",
            "name": "Carrer de Manso", "oneway": "yes", "segregated": "yes",
            "surface": "asphalt",
        },
    ),
    dict(
        category="One-way side bike lane",
        name="Carrer d'Aragó",
        url="https://www.openstreetmap.org/#map=20/41.3825122/2.1526109&layers=Y",
        tags={
            "cycleway:surface": "asphalt", "highway": "cycleway", "lanes": "1",
            "lit": "yes", "name": "Carrer d'Aragó", "oneway": "yes",
            "segregated": "yes", "smoothness": "excellent", "surface": "asphalt",
        },
    ),
    dict(
        category="Bike lane on sidewalk",
        name="Carrer de la Creu Coberta",
        url="https://www.openstreetmap.org/edit#map=19/41.375534/2.141368",
        tags={
            "footway": "sidewalk", "highway": "footway",
            "name": "Carrer de la Creu Coberta", "surface": "paving_stones",
        },
    ),
    dict(
        category="Bike lane on sidewalk",
        name="Avinguda del Litoral",
        url="https://www.openstreetmap.org/edit#map=19/41.390423/2.201212",
        tags={
            "footway": "sidewalk", "highway": "footway",
            "name": "Avinguda del Litoral",
        },
    ),
    dict(
        category="Calmed zone at 10 km/h",
        name="Carrer del Comte Borrell (#1)",
        url="https://www.openstreetmap.org/edit#map=19/41.377226/2.163290",
        tags={
            "bicycle": "yes", "cycleway:both": "no", "highway": "living_street",
            "lanes": "1", "maxspeed": "10", "name": "Carrer del Comte Borrell",
            "oneway": "yes", "oneway:bicycle": "no", "parking:both": "no",
            "parking:both:restriction": "no_parking", "smoothness": "excellent",
            "surface": "asphalt",
        },
    ),
    dict(
        category="Calmed zone at 10 km/h",
        name="Carrer del Comte Borrell (#2)",
        url="https://www.openstreetmap.org/#map=20/41.3768840/2.1632110&layers=Y",
        tags={
            "bicycle": "yes", "cycleway:both": "no", "highway": "living_street",
            "lanes": "1", "maxspeed": "10", "name": "Carrer del Comte Borrell",
            "oneway": "yes", "oneway:bicycle": "no", "parking:both": "no",
            "parking:both:restriction": "no_parking", "smoothness": "excellent",
            "surface": "asphalt",
        },
    ),
    # No test case yet for "Greenway (parks)" - no ground-truthed tags were
    # provided for that category. Add one in the same format if you have it.
]


def _predict(tags: dict):
    row = pd.Series(tags)
    passes = has_cycling_infrastructure_bcn(row)
    if not passes:
        return passes, "NOT INCLUDED (filtered out)"
    return passes, classify_cycling_path_bcn(row)


# KNOWN, PRE-EXISTING FAILURES - not introduced by this migration.
#
# Running these test cases against the classifier revealed two things:
#
# 1. The last *saved* run of classifier_accuracy_test.ipynb records 4/8
#    (50%) accuracy, with the "Comte Borrell" cases failing. But the
#    notebook's cell *source* (what's actually in the .ipynb file) already
#    contained a maxspeed-based fallback that fixes those two cases - the
#    notebook was edited but never re-run, so its displayed output was
#    stale. This module ports the current source, so those two cases now
#    pass (see classify_cycling_path_bcn's docstring in classification.py).
#
# 2. That still leaves 2 of 8 ground-truth cases genuinely failing today:
#    two `highway=footway` sidewalks with no explicit `bicycle` tag, which
#    real-world ground-truth says ARE "Bike lane on sidewalk" but which
#    has_cycling_infrastructure_bcn filters out because it requires an
#    explicit bicycle=yes/designated tag on footways. This is a real,
#    unresolved gap in the filter - not something this migration should
#    silently paper over. Marked xfail so it's visible in `pytest -rx`
#    instead of silently passing or breaking the whole suite.
KNOWN_FAILING_CASES = {
    "Carrer de la Creu Coberta",
    "Avinguda del Litoral",
}


def _case_param(case):
    marks = []
    if case["name"] in KNOWN_FAILING_CASES:
        marks.append(pytest.mark.xfail(
            reason="has_cycling_infrastructure_bcn filters out footways with no "
                   "explicit bicycle tag; ground truth says this one should count. "
                   "Pre-existing gap, see KNOWN_FAILING_CASES above.",
            strict=True,
        ))
    return pytest.param(case, id=case["name"], marks=marks)


@pytest.mark.parametrize("case", [_case_param(c) for c in TEST_CASES])
def test_ground_truth_classification(case):
    passes, predicted = _predict(case["tags"])
    assert predicted == case["category"], (
        f"{case['name']} ({case['url']}): expected {case['category']!r}, "
        f"got {predicted!r} (passed filter: {passes})"
    )


def test_overall_accuracy_matches_known_baseline():
    """
    Mirrors the notebook's summary cell: computes accuracy across all cases
    and reports every mismatch at once. Asserts against the CURRENT known
    baseline (6/8 - see KNOWN_FAILING_CASES) rather than 100%, so this test
    catches new regressions without being permanently red over the two
    pre-existing, unresolved cases.
    """
    results = []
    for tc in TEST_CASES:
        passes, predicted = _predict(tc["tags"])
        results.append({
            "name": tc["name"],
            "expected": tc["category"],
            "predicted": predicted,
            "match": predicted == tc["category"],
        })
    n_correct = sum(r["match"] for r in results)
    n_total = len(results)
    expected_correct = n_total - len(KNOWN_FAILING_CASES)
    mismatches = [r for r in results if not r["match"]]
    mismatch_report = "\n".join(
        f"  - {r['name']}: expected {r['expected']!r}, got {r['predicted']!r}"
        for r in mismatches
    )
    assert n_correct == expected_correct, (
        f"Accuracy: {n_correct}/{n_total} (expected {expected_correct}/{n_total} "
        f"given {len(KNOWN_FAILING_CASES)} known unresolved case(s))\n"
        f"Mismatches:\n{mismatch_report}"
    )
