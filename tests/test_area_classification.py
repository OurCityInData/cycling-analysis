"""
Ground-truth tests for classify_area_road(), the single area-adjustable
classifier that replaced classify_road() in cycling_analysis/country.py.

Uses the SAME real, in-person-verified OSM ways as tests/test_classification.py
(tags and urls copied verbatim from there), so the new area-adjustable engine
can be checked against the exact same ground truth as the old Barcelona
classifier. Two label renames apply going from the old Barcelona-taxonomy
names to the new fixed CATEGORIES vocabulary: "Calmed zone at 10 km/h" ->
"Pacified zone at 10 km/h" and "Greenway (parks)" -> "Greenway".

country/municipality are assigned per way based on its actual real-world
location (not all of these are in Barcelona - one is Dutch, one is in Girona
province, one is in Italy), since classify_area_road's output depends on
which CyclingLegalConfig applies.

Uses "area_case" rather than "case" as the parametrize name so
tests/conftest.py's Barcelona-specific reporting hook (hardcoded to
has_cycling_infrastructure_bcn / classify_cycling_path_bcn, keyed off a
param literally named "case") does not pick these up.
"""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cycling_analysis.area_classification import (
    CyclingLegalConfig,
    classify_area_road,
    get_cycling_config,
)

SUPPORTED_COUNTRIES = {'spain', 'netherlands', 'germany', 'belgium', 'denmark'}

# Tags/urls copied verbatim from tests/test_classification.py's TEST_CASES -
# these are real OSM ways checked in person, not synthetic.
TEST_CASES = [
    dict(
        category="Two-way side bike lane",
        name="Avinguda Paral·lel (Ejemplo 1)",
        url="https://www.openstreetmap.org/#map=16/41.37500/2.15871&layers=Y",
        country="spain", municipality="Barcelona",
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
        country="spain", municipality="Barcelona",
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
        country="spain", municipality="Barcelona",
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
        country="spain", municipality="Barcelona",
        tags={
            "cycleway:surface": "asphalt", "highway": "cycleway", "lanes": "1",
            "lit": "yes", "name": "Carrer d'Aragó", "oneway": "yes",
            "segregated": "yes", "smoothness": "excellent", "surface": "asphalt",
        },
    ),
    dict(
        category="Bus-bike lane",
        name="Carrer de la Creu Coberta",
        url="https://www.openstreetmap.org/edit#map=19/41.375534/2.141368",
        country="spain", municipality="Barcelona",
        tags={
            "bicycle": "yes", "cycleway:both": "share_busway", "foot": "yes",
            "highway": "tertiary", "lanes": "4", "lanes:backward": "2",
            "lanes:forward": "2", "lit": "yes", "maxspeed": "30",
            "name": "Carrer de la Creu Coberta", "oneway": "no",
            "ref": "N-340", "shoulder": "no", "surface": "asphalt",
        },
    ),
    dict(
        category="Bike lane on sidewalk",
        name="Avinguda del Litoral",
        url="https://www.openstreetmap.org/edit#map=19/41.390423/2.201212",
        country="spain", municipality="Barcelona",
        tags={
            "bicycle": "yes", "highway": "footway", "lit": "yes",
            "surface": "compacted", "wheelchair": "yes",
        },
    ),
    dict(
        category="Bike lane on sidewalk",
        name="Han Lammersbrug",
        url="https://www.openstreetmap.org/edit?way=173838280#map=20/52.3830349/4.8913260",
        country="netherlands", municipality=None,
        tags={
            "alt_name": "Voorzitter Stekbrug", "bicycle": "yes", "bridge": "yes",
            "bridge:ref": "2202", "highway": "footway", "layer": "1", "lit": "yes",
            "name": "Han Lammersbrug",
            "panoramax": "a5a1a90c-7eea-4a31-960e-41ba7f0283cd",
            "smoothness": "good", "surface": "asphalt", "traffic_sign": "NL:G7",
            "wikidata": "Q2417086",
            "wikimedia_commons": "Category:Brug 2202, Han Lammersbrug",
            "wikipedia": "nl:Han Lammersbrug",
        },
    ),
    dict(
        category="Bike lane on sidewalk",
        name="Polonceau-kade",
        url="https://www.openstreetmap.org/edit?way=173838280#map=16/52.38744/4.87231",
        country="netherlands", municipality=None,
        tags={
            "bicycle": "yes", "highway": "footway", "lit": "yes",
            "name": "Polonceau-kade", "oneway": "no", "smoothness": "intermediate",
            "surface": "concrete:plates", "surface:colour": "grey", "width": "2.00",
            "zone:traffic": "NL:urban",
        },
    ),
    dict(
        # was "Calmed zone at 10 km/h" under the old Barcelona taxonomy
        category="Pacified zone at 10 km/h",
        name="Carrer del Comte Borrell (#1)",
        url="https://www.openstreetmap.org/edit#map=19/41.377226/2.163290",
        country="spain", municipality="Barcelona",
        tags={
            "bicycle": "yes", "cycleway:both": "no", "highway": "living_street",
            "lanes": "1", "maxspeed": "10", "name": "Carrer del Comte Borrell",
            "oneway": "yes", "oneway:bicycle": "no", "parking:both": "no",
            "parking:both:restriction": "no_parking", "smoothness": "excellent",
            "surface": "asphalt",
        },
    ),
    dict(
        category="Pacified zone at 10 km/h",
        name="Carrer del Comte Borrell (#2)",
        url="https://www.openstreetmap.org/#map=20/41.3768840/2.1632110&layers=Y",
        country="spain", municipality="Barcelona",
        tags={
            "bicycle": "yes", "cycleway:both": "no", "highway": "living_street",
            "lanes": "1", "maxspeed": "10", "name": "Carrer del Comte Borrell",
            "oneway": "yes", "oneway:bicycle": "no", "parking:both": "no",
            "parking:both:restriction": "no_parking", "smoothness": "excellent",
            "surface": "asphalt",
        },
    ),
    dict(
        category="Greenway",  # was "Greenway (parks)"
        name="Rijwielpad Noordvoort",
        url="",
        country="netherlands", municipality=None,
        tags={
            "highway": "cycleway", "lit": "no", "mofa": "no", "moped": "no",
            "name": "Rijwielpad Noordvoort", "surface": "asphalt",
            "traffic_sign": "NL:G13",
        },
    ),
    dict(
        # Real way, verified on openstreetmap.org. highway=track with no
        # bicycle tag carrying NL:G11 (verplicht fietspad - mandatory
        # dedicated bike path) - exercises legal_evidence_sign_codes' path/
        # track cycleway-equivalence check (steps 3-4). Falls through to
        # None without it (track isn't in CAR_ROADS_HW, no bicycle/foot tag
        # for the sidewalk-bike-lane branch).
        category="Two-way side bike lane",
        name="Noordelijk Slingepad",
        url="https://www.openstreetmap.org/way/441956959",
        country="netherlands", municipality=None,
        tags={
            "highway": "track", "maxspeed": "60", "mofa": "yes", "moped": "no",
            "name": "Noordelijk Slingepad", "segregated": "no",
            "smoothness": "intermediate", "surface": "paving_stones",
            "tracktype": "grade1", "traffic_sign": "NL:G11",
        },
    ),
    dict(
        # Real way, verified on openstreetmap.org. highway=footway with no
        # bicycle tag carrying NL:G11 - exercises legal_evidence_sign_codes'
        # step-5 sidewalk-bike-lane extension. Falls through to None without
        # it (footway isn't in CAR_ROADS_HW either).
        category="Bike lane on sidewalk",
        name="Stormzwaluw",
        url="https://www.openstreetmap.org/way/6963072",
        country="netherlands", municipality=None,
        tags={
            "highway": "footway", "moped": "no", "surface": "paving_stones",
            "traffic_sign": "NL:G11", "name": "Stormzwaluw",
        },
    ),
    dict(
        category="Greenway",
        name="Carrilet Girona - Sant Feliu de Guíxols",
        url="https://www.openstreetmap.org/edit#map=18/41.951875/2.836758",
        # Girona province, not Barcelona city - no region override applies.
        country="spain", municipality=None,
        tags={
            "highway": "cycleway",
            "maxspeed": "30",
            "railway": "abandoned",
            "source": "Ajuntament de Girona",
        },
    ),
    dict(
        # Real way, verified on openstreetmap.org. highway=path with no
        # bicycle tag and railway=abandoned - exercises the step-1 gate's
        # greenway-evidence exception (_has_unpaved_or_abandoned_evidence).
        # Before that fix: bicycle=='' and path_default_legal=False (Spain)
        # -> excluded outright at step 1, never reaches the Greenway check.
        # After: railway=abandoned rescues it from the gate, then _is_greenway
        # catches it immediately after.
        category="Greenway",
        name="Camí de l'antiga via ferroviària de Manresa a Súria",
        url="https://www.openstreetmap.org/way/184672814",
        country="spain", municipality=None,
        tags={
            "highway": "path",
            "historic:end_date": "1996-05-13",
            "historic:start_date": "1924-08-13",
            "name": "Camí de l'antiga via ferroviària de Manresa a Súria",
            "railway": "abandoned",
        },
    ),
    dict(
        category="Greenway",
        name="Shared greenway near Rimini",
        url="https://www.openstreetmap.org/#map=19/44.062943/12.580397&layers=Y",
        # Italy isn't one of the 5 countries this repo has config for - no
        # documented rule exists, so this falls back to a bare default
        # config with no special legal rule (see get_config_for_case below).
        country="italy", municipality=None,
        tags={
            "bicycle": "designated", "foot": "designated", "highway": "cycleway",
            "lanes": "2", "lit": "yes", "motorcar": "no", "motorcycle": "no",
            "oneway": "no", "segregated": "no", "smoothness": "excellent",
            "surface": "asphalt", "width": "4",
        },
    ),
    dict(
        category="Two-way side bike lane",
        name="Passeig de Sant Joan (Ejemplo 3)",
        url="https://www.openstreetmap.org/edit#map=19/41.398792/2.170759",
        country="spain", municipality="Barcelona",
        tags={
            "cycleway:surface": "asphalt", "highway": "cycleway", "lanes": "2",
            "layer": "1", "lit": "yes", "motor_vehicle": "no",
            "name": "Passeig de Sant Joan", "oneway": "no", "segregated": "yes",
            "surface": "asphalt", "width": "2",
        },
    ),
    dict(
        category="One-way side bike lane",
        name="Avinguda Diagonal",
        url="https://www.openstreetmap.org/edit#map=19/41.398642/2.167801",
        country="spain", municipality="Barcelona",
        tags={
            "cycleway:surface": "asphalt", "highway": "cycleway", "lit": "yes",
            "name": "Avinguda Diagonal", "segregated": "yes", "surface": "asphalt",
            "width": "4",
        },
    ),
    dict(
        category="Bus-bike lane",
        name="Via Laietana",
        url="https://www.openstreetmap.org/edit#map=20/41.3856865/2.1763170",
        country="spain", municipality="Barcelona",
        tags={
            "cycleway:left": "separate", "cycleway:right": "share_busway",
            "highway": "secondary", "lanes": "3", "lanes:backward": "1",
            "lanes:forward": "2", "lit": "yes", "maxspeed": "30",
            "motor_vehicle:backward": "permit", "name": "Via Laietana",
            "oneway": "no", "smoothness": "excellent", "surface": "asphalt",
        },
    ),
    dict(
        # was "Calmed zone <= 10 km/h" under the old Barcelona taxonomy
        category="Pacified zone at 10 km/h",
        name="Carrer de Tamarit",
        url="https://www.openstreetmap.org/edit#map=19/41.378283/2.160448",
        country="spain", municipality="Barcelona",
        tags={
            "cycleway:both": "no", "highway": "living_street", "lanes": "1",
            "lit": "yes", "maxspeed": "10", "name": "Carrer de Tamarit",
            "oneway": "yes", "oneway:bicycle": "no", "surface": "asphalt",
        },
    ),
    dict(
        # cycleway:both=share_busway - a genuine bus-bike shared lane, not a
        # plain 30 km/h shared street. Was mislabeled "Shared street at
        # 30 km/h" in ground truth; classify_area_road's step-6 share_busway
        # match was correct all along (same rule that correctly classifies
        # "Carrer de la Creu Coberta" above).
        category="Bus-bike lane",
        name="Carrer de Sants",
        url="https://www.openstreetmap.org/edit#map=19/41.375085/2.136874",
        country="spain", municipality="Barcelona",
        tags={
            "bicycle": "yes", "cycleway:both": "share_busway", "foot": "yes",
            "highway": "tertiary", "lanes": "4", "lit": "yes",
            "maxspeed": "30", "name": "Carrer de Sants", "oneway": "no",
            "ref": "N-340", "surface": "asphalt",
        },
    ),
]


def get_config_for_case(case) -> CyclingLegalConfig:
    if case["country"] not in SUPPORTED_COUNTRIES:
        return CyclingLegalConfig()
    return get_cycling_config(case["country"], case.get("municipality"))


def _predict(case):
    row = pd.Series(case["tags"])
    return classify_area_road(row, get_config_for_case(case))


# Known failures - marked xfail so they are visible in `pytest -rx` rather
# than silently passing or breaking the suite. Each maps a case name to the
# reason classify_area_road currently gets it wrong.
KNOWN_FAILING_CASES = {
    "Rijwielpad Noordvoort": (
        "Ground truth per domain owner: this is a Greenway. classify_area_road's "
        "tag-only Greenway heuristic has no signal to key off here (no "
        "railway=abandoned, no unpaved surface/tracktype), and NL:G13 "
        "(onverplicht fietspad) is deliberately wired as legal-cycling "
        "evidence only, not a forced category (CyclingLegalConfig."
        "legal_evidence_sign_codes) - ~20k other NL ways carry G13 and most "
        "are ordinary paved paths, not greenways, so force-mapping the sign "
        "would misclassify those. Falls through to 'Two-way side bike lane' "
        "via the plain highway=cycleway rule instead."
    ),
    "Shared greenway near Rimini": (
        "Paved (surface=asphalt), segregated=no shared path with no "
        "railway/tracktype/unpaved-surface signal - the tag-only Greenway "
        "heuristic can't distinguish this from an ordinary urban cycleway, "
        "so it falls through to 'Two-way side bike lane'."
    ),
    "Avinguda Diagonal": (
        "No oneway tag present on this way at all (unlike Carrer de Manso / "
        "Carrer d'Aragó, the other one-way side-lane cases, which both carry "
        "an explicit oneway=yes). classify_area_road's step-3 two-way rule "
        "(plain highway=cycleway with is_oneway False) fires by default on a "
        "missing oneway tag, so this comes out as 'Two-way side bike lane' "
        "instead of 'One-way side bike lane'."
    ),
}
# Note: "Carrilet Girona - Sant Feliu de Guíxols" was ALSO a known failure
# under the old classify_cycling_path_bcn (see test_classification.py's
# KNOWN_FAILING_CASES), but classify_area_road's tag-only heuristic checks
# railway=abandoned directly and gets this one right - a genuine improvement
# over the old classifier for this specific case, despite the remaining gaps
# above.


def _case_param(case):
    marks = []
    if case["name"] in KNOWN_FAILING_CASES:
        marks.append(pytest.mark.xfail(reason=KNOWN_FAILING_CASES[case["name"]], strict=True))
    return pytest.param(case, id=case["name"], marks=marks)


@pytest.mark.parametrize("area_case", [_case_param(c) for c in TEST_CASES])
def test_ground_truth_classification(area_case):
    predicted = _predict(area_case)
    assert predicted == area_case["category"], (
        f"{area_case['name']} ({area_case['url']}): "
        f"expected {area_case['category']!r}, got {predicted!r}"
    )


def test_overall_accuracy_matches_known_baseline():
    """
    Mirrors test_classification.py's summary test: computes accuracy across
    all real, ground-truthed cases and reports every mismatch at once.
    Asserts against the CURRENT known baseline (n_total - len(KNOWN_FAILING_CASES)
    - see KNOWN_FAILING_CASES) rather than 100%, so this test catches
    regressions without being permanently red over the pre-existing,
    unresolved cases.
    """
    results = []
    for tc in TEST_CASES:
        predicted = _predict(tc)
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
