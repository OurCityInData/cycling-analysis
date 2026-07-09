"""
Single, area-adjustable cycling infrastructure classifier for the country-level
screening pipeline (cycling_analysis/country.py's run_country_analysis()).

This REPLACES classify_road() (the old country.py taxonomy) and its greenway
spatial-join machinery. It does NOT touch classify_cycling_path_amsterdam /
classify_cycling_path_bcn in classification.py — those remain separate,
high-precision, per-city classifiers validated against official reference
datasets, and the team already tried (and documented why they rejected)
unifying that specific pair into one config-driven function. classify_road()
was a different situation: it was already a single function applied
identically to every country with zero legal differentiation, so making it
config-driven here is a genuine improvement, not a repeat of that experiment.

Design:
  - Generic OSM tag interpretation (highway=*, cycleway=*, bicycle=*, foot=*,
    busway=*, maxspeed, access=*) lives directly in classify_area_road() and
    is identical for every country/region.
  - The handful of genuinely jurisdiction-specific legal rules are named,
    explicit fields on CyclingLegalConfig (not a generic rule-DSL — see the
    module docstring in classification.py for why a generic config schema
    was rejected there; the same reasoning applies here).
  - Only two such rules are currently documented anywhere in this repo:
      * the Netherlands' "Layer 3" default (an untagged highway=path is
        cycling-legal by default) — ported from classify_cycling_path_amsterdam.
      * Barcelona's municipal ordinance granting cycling access to any road
        with maxspeed <= 30 km/h — ported from has_cycling_infrastructure_bcn.
    Every other country/region defaults to "no special rule" until someone
    verifies an equivalent local law and adds it here.
  - Traffic-sign-code matching (BikeNEAT's approach for Germany, e.g. StVO
    sign 241 = mandatory path) is supported as an optional, per-country
    opt-in extension point (CyclingLegalConfig.sign_code_rules) but is left
    empty for every country today — populating it requires first verifying
    that OSM contributors in that country tag traffic_sign=* with useful
    density, which hasn't been done.

Greenway detection is tag-only (no spatial/polygon join of any kind, by
explicit decision) and is therefore lower-recall than the old landuse/leisure/
natural polygon intersection it replaces — an accepted quality tradeoff.
"""

from dataclasses import dataclass, field
from typing import Optional

from .constants import PLAIN_HIGHWAY

# ── Fixed output category vocabulary ─────────────────────────────────────────
# Exact strings, exact order. Used directly as both the classification result
# and the final report column name/order - no separate labels dict needed.
CATEGORIES = [
    'Greenway',
    'Two-way side bike lane',
    'One-way side bike lane',
    'Bike lane on sidewalk',
    'Bus-bike lane',
    'Pacified zone at 10 km/h',
    'Pacified street at 20 km/h',
    'Shared street at 30 km/h',
    'Service road',
    '2-1 road',
    'Mixed traffic road',
]

# Highway values excluded outright, regardless of country (moved verbatim
# from the old classify_road - references nothing country-specific).
EXCLUDED_HW = {
    'motorway', 'motorway_link',
    'proposed', 'construction', 'raceway',
    'abandoned', 'razed', 'disused',
}

# Ordinary car-accessible road classes that fall back to "Mixed traffic road"
# absent any more specific cycling infrastructure signal (moved verbatim from
# the old classify_road; PLAIN_HIGHWAY from constants.py is a subset of this).
CAR_ROADS_HW = {
    'trunk', 'trunk_link',
    'primary', 'primary_link',
    'secondary', 'secondary_link',
    'tertiary', 'tertiary_link',
    'residential', 'unclassified', 'road',
}
assert PLAIN_HIGHWAY <= CAR_ROADS_HW

# Extra OSM attribute columns pyrosm must fetch for classify_area_road() to
# work. Superset of the old CYCLING_TAGS: adds surface/tracktype/railway
# (tag-only Greenway heuristic), traffic_sign(:forward) (sign_code_rules
# extension point - unused today, but the plumbing needs to exist now so
# populating sign_code_rules later is a config-only change), and
# separation/buffer (physical-separation Greenway signal - see
# SUBSTANTIAL_SEPARATION_VALUES below).
EXTRA_OSM_ATTRIBUTES = [
    'cycleway', 'cycleway:left', 'cycleway:right', 'cycleway:both',
    'bicycle', 'foot', 'segregated', 'busway',
    'cyclestreet', 'bicycle_road', 'access', 'oneway:bicycle',
    'surface', 'tracktype', 'railway',
    'traffic_sign', 'traffic_sign:forward',
    'separation', 'cycleway:separation',
    'cycleway:left:separation', 'cycleway:right:separation', 'cycleway:both:separation',
    'buffer', 'cycleway:buffer',
    'cycleway:left:buffer', 'cycleway:right:buffer', 'cycleway:both:buffer',
]

# separation=* / cycleway(:left|right|both):separation=* values that imply a
# real physical gap (typically >=1m: a grass verge, hedge, ditch, guard rail,
# ...) between cycling infrastructure and the road, as opposed to paint or a
# narrow post/kerb (kerb is usually ~10-15cm; bollards, dashed/solid lines,
# and vertical panels are paint/post markers with negligible real separation
# - none of those count here). Per boss's rule: distance is what matters, not
# just "is it protected in some way." parking_lane/hard_shoulder (~2m) is
# deliberately excluded even though it technically clears 1m - a
# parking-protected on-street lane doesn't match the intended "off in its own
# space" meaning of Greenway as well as a verge/hedge/ditch does; revisit if
# that's wrong.
SUBSTANTIAL_SEPARATION_VALUES = {
    'grass_verge', 'verge', 'hedge', 'tree_row', 'ditch',
    'guard_rail', 'fence', 'wall',
}
MIN_SEPARATION_BUFFER_M = 1.0

_SEPARATION_KEYS = (
    'separation', 'cycleway:separation', 'cycleway:left:separation',
    'cycleway:right:separation', 'cycleway:both:separation',
)
_BUFFER_KEYS = (
    'buffer', 'cycleway:buffer', 'cycleway:left:buffer',
    'cycleway:right:buffer', 'cycleway:both:buffer',
)


@dataclass(frozen=True)
class CyclingLegalConfig:
    """
    Named, per-country/region legal-rule knobs for classify_area_road().

    Add a new field here ONLY for a real, documented legal/tagging divergence
    (see classification.py's module docstring on why a generic rule-DSL was
    rejected for the city taxonomies - the same reasoning applies here). Do
    not add a generic "rules: list" field.
    """

    # Netherlands "Layer 3" rule: highway=path with no explicit bicycle tag
    # defaults to cycling-legal (Dutch law). False = unproven legality on an
    # untagged path -> excluded. Ported from classify_cycling_path_amsterdam
    # (classification.py:171-174).
    path_default_legal: bool = False

    # Barcelona Ordenanca de Circulacio: any way with maxspeed <= this value
    # counts as legal cycling infrastructure even without an explicit bicycle
    # tag. None = no such rule (the default everywhere except Barcelona).
    # Ported from has_cycling_infrastructure_bcn's Layer-3 rule
    # (classification.py:295).
    layer3_maxspeed_threshold: Optional[int] = None

    # Optional, opt-in, empty by default. Maps one of the CATEGORIES strings
    # -> a tuple of substrings to match against the traffic_sign /
    # traffic_sign:forward tag value. BikeNEAT-style extension point
    # (bikeneat.py's German StVO sign-code predicates) but intentionally NOT
    # populated with real sign codes here - future work, per-country, once
    # OSM sign-tagging density is verified.
    sign_code_rules: dict = field(default_factory=dict)


COUNTRY_CYCLING_CONFIG: dict = {
    'netherlands': CyclingLegalConfig(path_default_legal=True),
    'spain': CyclingLegalConfig(),  # national default: no Layer-3 path rule
    # TODO: no documented layer-3/maxspeed default rule for Germany in this
    # repo - verify against StVO before adding.
    'germany': CyclingLegalConfig(),
    # TODO: no documented layer-3/maxspeed default rule for Belgium in this
    # repo - verify before adding.
    'belgium': CyclingLegalConfig(),
    # TODO: no documented layer-3/maxspeed default rule for Denmark in this
    # repo - verify before adding.
    'denmark': CyclingLegalConfig(),
}

# country -> GADM muni_col value -> override config. Barcelona's ≤30 km/h
# rule is a municipal ordinance, not Spanish national law, hence the
# municipality-level (not country-level) override.
REGION_CYCLING_CONFIG_OVERRIDES: dict = {
    'spain': {
        'Barcelona': CyclingLegalConfig(layer3_maxspeed_threshold=30),
    },
}


def get_cycling_config(country: str, municipality: Optional[str] = None) -> CyclingLegalConfig:
    overrides = REGION_CYCLING_CONFIG_OVERRIDES.get(country, {})
    if municipality is not None and municipality in overrides:
        return overrides[municipality]
    return COUNTRY_CYCLING_CONFIG[country]


def _v(row, field_name: str) -> str:
    v = row.get(field_name, None)
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


def _has_physical_separation(tags: dict) -> bool:
    """
    True if a separation/buffer tag indicates real physical space (a grass
    verge, hedge, ditch, guard rail, ... or an explicit buffer width >=
    MIN_SEPARATION_BUFFER_M) between cycling infrastructure and the road, as
    opposed to paint or a narrow post/kerb. Paint/kerb/bollard-only
    separation does NOT count, even though it's still a protected lane in
    casual usage.
    """
    for key in _SEPARATION_KEYS:
        if tags.get(key) in SUBSTANTIAL_SEPARATION_VALUES:
            return True
    for key in _BUFFER_KEYS:
        val = tags.get(key)
        if not val:
            continue
        try:
            if float(val) >= MIN_SEPARATION_BUFFER_M:
                return True
        except ValueError:
            continue
    return False


def _is_greenway(tags: dict, config: CyclingLegalConfig) -> bool:
    hw = tags['highway']
    bicycle = tags['bicycle']
    if bicycle in ('no', 'dismount'):
        return False

    # Physical-separation signal: applies to ANY cycling lane candidate
    # (whether a standalone highway=cycleway or a cycleway:*=track/lane sub-
    # tag on an ordinary road), not just off-road path/track types - a
    # residential street with a grass-verge-separated track is functionally
    # closer to a Greenway than to a painted-lane "side bike lane".
    cw, cw_left, cw_right, cw_both = (
        tags['cycleway'], tags['cycleway:left'], tags['cycleway:right'], tags['cycleway:both']
    )
    has_cycle_lane = (
        cw_both in ('track', 'lane', 'yes')
        or (cw_left in ('track', 'lane') and cw_right in ('track', 'lane'))
        or cw in ('track', 'lane', 'shared_lane')
        or cw_right in ('track', 'lane', 'yes') or cw_left in ('track', 'lane', 'yes')
        or hw == 'cycleway'
    )
    if has_cycle_lane and _has_physical_separation(tags):
        return True

    # Tag-only off-road signals (no separation tag needed - these highway
    # types are inherently apart from car traffic already).
    if hw not in ('path', 'track', 'cycleway'):
        return False
    if tags['railway'] in ('abandoned', 'disused'):
        return True
    if tags['tracktype'] in ('grade2', 'grade3', 'grade4', 'grade5'):
        return True
    if tags['surface'] in ('unpaved', 'compacted', 'gravel', 'fine_gravel',
                            'dirt', 'ground', 'grass', 'sand', 'woodchips'):
        return True
    if (hw == 'path' and bicycle == '' and config.path_default_legal
            and tags['foot'] not in ('yes', 'designated')):
        return True
    return False


def classify_area_road(row, config: CyclingLegalConfig):
    """Classify one OSM way into one of the CATEGORIES strings, or None."""
    tags = {
        'highway': _v(row, 'highway'),
        'bicycle': _v(row, 'bicycle'),
        'access': _v(row, 'access'),
        'busway': _v(row, 'busway'),
        'cycleway': _v(row, 'cycleway'),
        'cycleway:left': _v(row, 'cycleway:left'),
        'cycleway:right': _v(row, 'cycleway:right'),
        'cycleway:both': _v(row, 'cycleway:both'),
        'cyclestreet': _v(row, 'cyclestreet'),
        'bicycle_road': _v(row, 'bicycle_road'),
        'oneway': _v(row, 'oneway'),
        'foot': _v(row, 'foot'),
        'surface': _v(row, 'surface'),
        'tracktype': _v(row, 'tracktype'),
        'railway': _v(row, 'railway'),
        'traffic_sign': _v(row, 'traffic_sign'),
        'traffic_sign:forward': _v(row, 'traffic_sign:forward'),
        'separation': _v(row, 'separation'),
        'cycleway:separation': _v(row, 'cycleway:separation'),
        'cycleway:left:separation': _v(row, 'cycleway:left:separation'),
        'cycleway:right:separation': _v(row, 'cycleway:right:separation'),
        'cycleway:both:separation': _v(row, 'cycleway:both:separation'),
        'buffer': _v(row, 'buffer'),
        'cycleway:buffer': _v(row, 'cycleway:buffer'),
        'cycleway:left:buffer': _v(row, 'cycleway:left:buffer'),
        'cycleway:right:buffer': _v(row, 'cycleway:right:buffer'),
        'cycleway:both:buffer': _v(row, 'cycleway:both:buffer'),
    }
    maxspeed = parse_maxspeed(_v(row, 'maxspeed'))

    hw = tags['highway']
    bicycle = tags['bicycle']
    access = tags['access']

    # 0. Pre-filter (country-agnostic, reused verbatim from the old classify_road).
    if hw in EXCLUDED_HW:
        return None
    if bicycle in ('no', 'dismount') and hw != 'cycleway':
        return None
    if access == 'no' and bicycle not in ('yes', 'designated', 'permissive'):
        return None

    # 0.5. Optional per-country sign-code extension point (no-op everywhere today).
    sign_text = tags['traffic_sign'] + ' ' + tags['traffic_sign:forward']
    for category, codes in config.sign_code_rules.items():
        if any(code in sign_text for code in codes):
            return category

    # 1. Netherlands-style path-legality gate. Everywhere else, an untagged
    # highway=path with no proof of legal cycling access is excluded - a
    # deliberate behavior change vs. the old classify_road, which counted
    # every untagged path/track/cycleway/footway in every country by default.
    if hw == 'path' and bicycle == '' and not config.path_default_legal:
        return None

    # 2. Greenway (tag-only).
    if _is_greenway(tags, config):
        return 'Greenway'

    cw = tags['cycleway']
    cw_left = tags['cycleway:left']
    cw_right = tags['cycleway:right']
    cw_both = tags['cycleway:both']
    oneway = tags['oneway']
    is_oneway = oneway in ('yes', '1', '-1', 'true')

    # 3. Two-way side bike lane (contraflow lanes fold in here).
    if cw_both in ('track', 'lane', 'yes'):
        return 'Two-way side bike lane'
    if cw_left in ('track', 'lane') and cw_right in ('track', 'lane'):
        return 'Two-way side bike lane'
    if hw == 'cycleway' and not is_oneway:
        return 'Two-way side bike lane'
    if cw in ('opposite', 'opposite_lane', 'opposite_track'):
        return 'Two-way side bike lane'
    if cw_left in ('opposite', 'opposite_lane', 'opposite_track'):
        return 'Two-way side bike lane'
    if cw_right in ('opposite', 'opposite_lane', 'opposite_track'):
        return 'Two-way side bike lane'

    # 4. One-way side bike lane.
    if cw in ('track', 'lane', 'shared_lane'):
        return 'One-way side bike lane'
    if cw_right in ('track', 'lane', 'yes') or cw_left in ('track', 'lane', 'yes'):
        return 'One-way side bike lane'
    if hw == 'cycleway' and is_oneway:
        return 'One-way side bike lane'

    # 5. Bike lane on sidewalk.
    if hw in ('footway', 'path', 'pedestrian') and bicycle in ('yes', 'designated', 'permissive'):
        return 'Bike lane on sidewalk'
    if (hw == 'path' and bicycle == '' and config.path_default_legal
            and tags['foot'] in ('yes', 'designated')):
        return 'Bike lane on sidewalk'

    # 6. Bus-bike lane.
    if tags['busway'] in ('lane', 'yes') and bicycle != 'no':
        return 'Bus-bike lane'
    if 'share_busway' in (cw, cw_left, cw_right, cw_both):
        return 'Bus-bike lane'

    # 7. 2-1 road. Known pre-existing conflation inherited unchanged from the
    # old classify_road: NL "fietsstraat" tagging isn't the same physical
    # infrastructure as a German-style 2-1 road/Schutzstreifen. Not fixed here.
    if tags['cyclestreet'] in ('yes', '1', 'true') or tags['bicycle_road'] in ('yes', '1', 'true'):
        return '2-1 road'

    # 8. Service road (checked before the maxspeed cascade - priority fix vs.
    # the old classify_road, where a service road tagged maxspeed=10 was
    # miscategorized as a pacified zone).
    if hw == 'service' and bicycle != 'no' and access != 'no':
        return 'Service road'

    # 9. Pacified zone at 10 km/h via living_street (universal, no config).
    if hw == 'living_street':
        return 'Pacified zone at 10 km/h'

    # 10. Speed cascade. layer3_maxspeed_threshold plugs in here - it never
    # gates inclusion/exclusion (step 11 already catches any plain road with
    # bicycle != 'no' regardless of country), only which bucket a low-speed
    # road lands in: the finer Pacified/Shared tiers vs. the coarse Mixed
    # traffic road catch-all.
    if maxspeed is not None:
        has_cycle_evidence = bicycle in ('yes', 'designated', 'permissive')
        has_layer3 = (
            config.layer3_maxspeed_threshold is not None
            and maxspeed <= config.layer3_maxspeed_threshold
            and bicycle != 'no'
        )
        if has_cycle_evidence or has_layer3:
            if maxspeed <= 10:
                return 'Pacified zone at 10 km/h'
            if maxspeed <= 20:
                return 'Pacified street at 20 km/h'
            if maxspeed <= 30:
                return 'Shared street at 30 km/h'

    # 11. Mixed traffic road (catch-all).
    if hw in CAR_ROADS_HW and bicycle != 'no':
        return 'Mixed traffic road'

    return None
