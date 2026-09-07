"""
Single, area-adjustable cycling infrastructure classifier for the country-level
screening pipeline (cycling_analysis/country.py's run_country_analysis()).

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

    # "Layer 3" extension point: an untagged highway=path (no bicycle tag)
    # defaults to cycling-legal when True. False (the default) = unproven
    # legality on an untagged path -> excluded, same as every other country.
    #
    # NOT currently set True for any country. It was set True for the
    # Netherlands (ported from classify_cycling_path_amsterdam,
    # classification.py:171-174, itself commented "NL: onverplicht
    # fietspad") under a "Dutch law" label that turned out not to hold up:
    # under RVV 1990, a path is only formally a fietspad if marked with
    # G11/G12a/G13 signage (see legal_evidence_sign_codes below, which
    # already covers exactly that, precisely) - an unmarked path merely
    # LOOKING like a bike path isn't one under the letter of the law. What
    # this flag actually modeled was a practical assumption from the
    # original NL06 notebook (most unmarked off-road paths in NL cities/
    # parks are open to cyclists absent a posted C-series "verboden voor
    # fietsers" prohibition), not a codified default - and no citation for
    # that softer claim was found either. Left False pending a real legal
    # hook; removing it (rather than leaving it mislabeled "Dutch law")
    # measurably reduces recall on unsigned NL park/city paths - accepted
    # tradeoff, not a bug.
    path_default_legal: bool = False

    # National speed-tier default: any way with maxspeed <= this value counts
    # as legal cycling infrastructure even without an explicit bicycle tag.
    # None = no such rule.
    #
    # Populated for Spain (30) per Real Decreto 970/2020 (BOE-A-2020-13969),
    # in force since 2021-05-11, which rewrote RGC Article 50 to set
    # nationwide default speed tiers for "vias urbanas y travesias" (urban
    # roads and town-crossings) by street geometry rather than signage:
    # 20 km/h on single-platform streets (plataforma unica de calzada y
    # acera), 30 km/h on single-lane-per-direction streets, 50 km/h on
    # multi-lane streets. Spanish traffic law's general default already
    # permits cyclists on ordinary roads (RGC Art. 36/38 - only
    # motorway/autopista is barred outright, handled separately via
    # EXCLUDED_HW), so a signed maxspeed <= 30 is legal-cycling evidence
    # nationwide, not just in Barcelona. This value used to be modeled as a
    # Barcelona-only municipal override (Ordenanca de Circulacio) before RD
    # 970/2020 was identified as the actual national-level source of the
    # same threshold - see REGION_CYCLING_CONFIG_OVERRIDES for why that
    # override was removed instead of kept alongside this.
    #
    # CAVEAT (not fixed here): only fires on ways carrying an explicit
    # maxspeed tag (see classify_area_road step 10) - does not infer the RD
    # 970/2020 default for UNSIGNED urban streets from lane count, so those
    # are still undercounted relative to the law's literal default. Also
    # applied uniformly across a municipality's full GADM boundary, which
    # mixes urban core with rural/interurban roads outside the "vias urbanas
    # y travesias" scope of RD 970/2020 - a rural road signed <=30 for an
    # unrelated reason (a sharp curve, a hamlet crossing) will still match.
    # Accepted approximation; revisit if it inflates rural municipalities.
    #
    # Does NOT touch classify_cycling_path_bcn / has_cycling_infrastructure_
    # bcn in classification.py - those remain the separate, high-precision
    # Barcelona-vs-AMB-official pipeline (cities/barcelona.py), untouched by
    # this change. This field only affects the country-level screening
    # pipeline (country.py).
    layer3_maxspeed_threshold: Optional[int] = None

    # Optional, opt-in, empty by default. Maps one of the CATEGORIES strings
    # -> a tuple of substrings to match against the traffic_sign /
    # traffic_sign:forward tag value. BikeNEAT-style extension point
    # (bikeneat.py's German StVO sign-code predicates) but intentionally NOT
    # populated with real sign codes here - future work, per-country, once
    # OSM sign-tagging density is verified.
    sign_code_rules: dict = field(default_factory=dict)

    # Optional, opt-in, empty by default. Tuple of lowercase traffic_sign /
    # traffic_sign:forward substrings that count as evidence of legal
    # cycling access EQUIVALENT to bicycle in ('yes', 'designated',
    # 'permissive') wherever that's used as gating/evidence in
    # classify_area_road() (the path-legality gate, the path/track
    # cycleway-equivalence check, the sidewalk-bike-lane check, the speed-
    # cascade evidence check). Unlike sign_code_rules above, this does NOT
    # jump straight to a category - the rest of the cascade (oneway,
    # surface, cycleway subtags, etc.) still determines the final category.
    # Populated for the Netherlands with the RVV 1990 (Reglement
    # verkeersregels en verkeerstekens) Bijlage I G-series bicycle-path
    # signs: G11 (verplicht fietspad - mandatory dedicated bike path), G12a
    # (verplicht fiets-/bromfietspad - mandatory bike/moped path), G13
    # (onverplicht fietspad - optional/non-mandatory bike path, legal but
    # not obligatory). G7 (fietsers oversteken - a cyclist-CROSSING warning
    # sign, not a facility-type sign) is deliberately excluded. Density
    # verified against data/netherlands-latest.osm.pbf (2026-07): ~194k
    # ways carry a traffic_sign tag, ~81% of them one of these three codes
    # (G11 ~74k, G12a ~63k, G13 ~21k) - real, dense, legally-sourced
    # evidence. See CLAUDE.md.
    legal_evidence_sign_codes: tuple = ()


COUNTRY_CYCLING_CONFIG: dict = {
    # path_default_legal stays False (the default) - no citable statutory
    # basis found for treating an unsigned path as legal by default under
    # Dutch law; see CyclingLegalConfig.path_default_legal. G11/G12a/G13-
    # signed paths are still fully covered via legal_evidence_sign_codes.
    'netherlands': CyclingLegalConfig(
        legal_evidence_sign_codes=('nl:g11', 'nl:g12a', 'nl:g13'),
    ),
    'spain': CyclingLegalConfig(
        layer3_maxspeed_threshold=30,  # RD 970/2020 nationwide urban tier - see CyclingLegalConfig
    ), 
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

# country -> GADM muni_col value -> override config. Empty for now: the
# <=30 km/h rule formerly modeled here as a Barcelona-only municipal
# override (Ordenanca de Circulacio) was superseded by RD 970/2020, a
# national rule carrying the same threshold - see CyclingLegalConfig.
# layer3_maxspeed_threshold and COUNTRY_CYCLING_CONFIG['spain']. Left as an
# empty dict (not deleted) as the place to add a genuinely municipality-
# specific rule - e.g. a documented local ordinance provision stricter than
# national law - if one is found later.
REGION_CYCLING_CONFIG_OVERRIDES: dict = {}


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


def _has_sign_legal_evidence(tags: dict, config: CyclingLegalConfig) -> bool:
    """
    True if traffic_sign/traffic_sign:forward contains any of
    config.legal_evidence_sign_codes (substring match, same convention as
    sign_code_rules). Equivalent-weight evidence to
    bicycle in ('yes', 'designated', 'permissive') - see
    CyclingLegalConfig.legal_evidence_sign_codes.
    """
    if not config.legal_evidence_sign_codes:
        return False
    sign_text = tags['traffic_sign'] + ' ' + tags['traffic_sign:forward']
    return any(code in sign_text for code in config.legal_evidence_sign_codes)


def _has_unpaved_or_abandoned_evidence(tags: dict) -> bool:
    """
    Tag-only evidence that a way is physically an unpaved/abandoned-corridor
    path, independent of any bicycle-legality tagging: railway=abandoned/
    disused, tracktype grade2-5, or an unpaved-family surface value. Shared
    by _is_greenway's off-road branch and classify_area_road's step-1
    path-legality gate exception (an untagged path with this evidence is
    rescued from the gate for Greenway purposes even without proof of
    general cycling legality - the physical evidence IS the proof, for
    Greenway purposes specifically).
    """
    if tags['railway'] in ('abandoned', 'disused'):
        return True
    if tags['tracktype'] in ('grade2', 'grade3', 'grade4', 'grade5'):
        return True
    if tags['surface'] in ('unpaved', 'compacted', 'gravel', 'fine_gravel',
                            'dirt', 'ground', 'grass', 'sand', 'woodchips'):
        return True
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
    if _has_unpaved_or_abandoned_evidence(tags):
        return True
    # Currently inactive for every country (path_default_legal is False
    # everywhere - see CyclingLegalConfig.path_default_legal); kept as the
    # extension point for a country where this blanket rule does get a real
    # citation.
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
    sign_legal_evidence = _has_sign_legal_evidence(tags, config)

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

    # 1. Path-legality gate. An untagged highway=path (no bicycle tag) with
    # no proof of legal cycling access is excluded - a deliberate behavior
    # change vs. the old classify_road, which counted every untagged path/
    # track/cycleway/footway in every country by default. path_default_legal
    # (an opt-in blanket exception) is currently False for every country -
    # see CyclingLegalConfig.path_default_legal for why. Two narrower
    # exceptions still rescue a path from this gate without needing general
    # cycling-legality proof:
    #  - _has_unpaved_or_abandoned_evidence: independent physical evidence
    #    (railway=abandoned, unpaved surface/tracktype) that the way is a
    #    greenway-type corridor is proof enough for Greenway purposes -
    #    without this, the tag-only Greenway check at step 2 never gets a
    #    chance to run for these paths. Measured against
    #    data/cataluna-latest.osm.pbf (2026-07): of 106,007 untagged
    #    highway=path ways in Catalonia, 14,813 carry this evidence and are
    #    now correctly classified as Greenway instead of discarded.
    #  - sign_legal_evidence: actively load-bearing for the Netherlands
    #    (its only currently-populated legal_evidence_sign_codes) - an
    #    untagged highway=path/track carrying an RVV G11/G12a/G13 sign is
    #    rescued here even though path_default_legal is False for NL.
    if (hw == 'path' and bicycle == '' and not config.path_default_legal
            and not _has_unpaved_or_abandoned_evidence(tags)
            and not sign_legal_evidence):
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
    # A highway=path/track carrying a legal-evidence sign (e.g. NL's RVV
    # G11/G12a "verplicht fietspad") is functionally a dedicated cycleway -
    # treat it as one for the oneway-aware side-lane split below only, not
    # elsewhere (EXCLUDED_HW/CAR_ROADS_HW etc. still see the real highway
    # value).
    is_signed_cycle_path = hw in ('path', 'track') and sign_legal_evidence

    # 3. Two-way side bike lane (contraflow lanes fold in here).
    if cw_both in ('track', 'lane', 'yes'):
        return 'Two-way side bike lane'
    if cw_left in ('track', 'lane') and cw_right in ('track', 'lane'):
        return 'Two-way side bike lane'
    if (hw == 'cycleway' or is_signed_cycle_path) and not is_oneway:
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
    if (hw == 'cycleway' or is_signed_cycle_path) and is_oneway:
        return 'One-way side bike lane'

    # 5. Bike lane on sidewalk.
    if hw in ('footway', 'path', 'pedestrian') and (
            bicycle in ('yes', 'designated', 'permissive') or sign_legal_evidence):
        return 'Bike lane on sidewalk'
    # Currently inactive for every country - see the comment on the
    # equivalent branch in _is_greenway above.
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
        has_cycle_evidence = (
            bicycle in ('yes', 'designated', 'permissive') or sign_legal_evidence
        )
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
