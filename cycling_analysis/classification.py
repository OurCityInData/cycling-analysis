"""
OSM-row → cycling-infrastructure classification logic.

IMPORTANT DESIGN NOTE — read before "simplifying" this file:

`classify_cycling_path_amsterdam()` and `classify_cycling_path_bcn()` look
like they should collapse into one config-driven function (that's what a
first pass at deduplication assumes). They can't, safely. Diffing the two
line-by-line with comments stripped shows they encode genuinely different
national/municipal road-classification rules:

  - Amsterdam (NL06 taxonomy) has no speed-based categories, has a "Bicycle
    on the road" / "Moped/Bicycle path" split, and treats highway=path with
    an inferred Layer-3 default (unmarked paths are cycle-legal in NL).
  - Barcelona (AMB taxonomy) has NO Layer-3 default for paths (illegal under
    Catalan/Spanish law without an explicit bicycle tag), but adds
    maxspeed-driven categories ("Calmed zone at 10 km/h", "Shared street at
    30 km/h") that don't exist in the Amsterdam scheme at all.

  Forcing these into one function would either silently drop a
  country-specific rule or require a config schema complex enough that it
  stops being simpler than two functions. So: two functions, dispatched by
  city config (see cities/amsterdam.py and cities/barcelona.py), not one
  function with a `country=` flag.

`has_cycling_infrastructure()`, by contrast, IS identical logic across
amsterdam_vs_official.ipynb, barcelona_vs_bikeneat.ipynb and
freiburg_vs_bikeneat.ipynb (confirmed by comment-stripped diff) — that one
really was pure duplication, and lives here as a single shared function.

`has_cycling_infrastructure_bcn()` is a distinct filter with real logic
differences from the shared one (see its docstring) — Barcelona's road law
grants cycling access on any road with maxspeed <= 30 km/h, which has no NL
equivalent, so it stays separate.
"""

from .constants import CYCLEWAY_LANE_VALUES, CYCLEWAY_USEFUL_VALUES, PLAIN_HIGHWAY


def has_cycling_infrastructure(row):
    """
    Return True if this OSM edge belongs in the cycling network.
    NL06 final version (FIX 1, 2, 7) — filter only, no categorisation.

    Shared verbatim by the Amsterdam vs. official comparison and both
    BikeNEAT comparisons (Barcelona, Freiburg) — see module docstring.
    """
    def _get(f):
        v = row.get(f)
        return str(v).lower().strip() if v is not None and str(v) != 'nan' else ''

    highway      = _get('highway')
    bicycle      = _get('bicycle')
    access       = _get('access')
    busway       = _get('busway')
    cw           = _get('cycleway')
    cyclestreet  = _get('cyclestreet')
    bicycle_road = _get('bicycle_road')

    # Gather any cycleway:* sub-tags present as columns
    cw_extra = set()
    for col in row.index:
        if 'cycleway' in str(col).lower().replace(':', '_') and str(col).lower() != 'cycleway':
            val = str(row[col]).lower().strip()
            if val not in ('nan', 'none', '', 'no', 'false'):
                cw_extra.add(val)

    cw_useful = (
        (cw and cw not in ('nan', 'none', 'no', 'false', 'crossing', 'traffic_island', ''))
        or bool(cw_extra & CYCLEWAY_USEFUL_VALUES)
    )

    # PLAIN_HIGHWAY: require explicit cycling evidence
    if highway in PLAIN_HIGHWAY:
        if bicycle in ('no', 'use_sidepath'):    return False  # FIX 1
        if cw_useful:                            return True
        if cyclestreet in ('yes', '1', 'true'):  return True   # FIX 2
        if bicycle_road in ('yes', '1', 'true'): return True
        if bicycle in ('yes', 'designated', 'permissive'): return True
        if access  in ('yes', 'permissive'):     return True
        return False

    if highway == 'cycleway': return True

    # highway=path — apply NL Layer 3 default (FIX 7)
    if highway == 'path':
        if bicycle in ('no', 'dismount', 'use_sidepath'): return False
        return True

    if highway in ('footway', 'pedestrian') and bicycle in ('yes', 'designated'): return True
    if highway == 'living_street': return True
    if highway == 'track' and bicycle in ('yes', 'designated', 'permissive'): return True
    if cw_useful:                              return True
    if busway in ('lane', 'yes'):              return True
    if cyclestreet in ('yes', '1', 'true'):    return True
    if bicycle_road in ('yes', '1', 'true'):   return True
    if bicycle in ('yes', 'designated', 'permissive'): return True
    if access  in ('yes', 'permissive'):       return True
    return False


def classify_cycling_path_amsterdam(row):
    """
    Map an OSM edge that passed has_cycling_infrastructure() to one of the
    NL06 cycling categories used in Amsterdam's official network data.
    """
    def get(field):
        val = row.get(field, None)
        return str(val).lower().strip() if val is not None and str(val) != 'nan' else ''

    highway       = get('highway')
    bicycle       = get('bicycle')
    foot          = get('foot')
    oneway        = get('oneway')
    maxspeed_raw  = get('maxspeed').replace('km/h', '').replace('mph', '').strip()
    busway        = get('busway')
    motor_vehicle = get('motor_vehicle')
    moped         = get('moped')
    mofa          = get('mofa')
    segregated    = get('segregated')    # fix 6: cyclists/pedestrians physically separated?
    cyclestreet   = get('cyclestreet')
    bicycle_road  = get('bicycle_road')

    cw = get('cycleway')
    if cw in ('crossing', 'traffic_island', 'no', 'nan', 'none', ''):
        cw_lane = ''
    else:
        cw_lane = cw

    cw_tags, cw_extra = set(), set()
    for col in row.index:
        col_norm = str(col).lower().replace(':', '_')
        if col_norm.startswith('cycleway') and col_norm != 'cycleway':
            val = str(row[col]).lower().strip()
            if val not in ('nan', 'none', '', 'no', 'false'):
                cw_tags.add(col_norm)
                cw_extra.add(val)

    has_explicit_cycle_tag = bool(cw_lane) or bool(cw_extra) or \
        cyclestreet in ('yes', '1', 'true') or \
        bicycle_road in ('yes', '1', 'true')

    if highway in PLAIN_HIGHWAY:
        if bicycle == 'no':
            return 'Other / unclassified'
        if has_explicit_cycle_tag:
            if cw_lane == 'shared_lane' or 'shared_lane' in cw_extra:
                return 'Bicycle on the road'
        elif bicycle in ('yes', 'designated', 'permissive'):
            return 'Bicycle on the road'
        else:
            return 'Other / unclassified'

    # ── DEDICATED CYCLEWAY (highway=cycleway) ─────────────────────────────────
    if highway == 'cycleway':
        if foot == 'no' or motor_vehicle == 'no' or moped == 'no' or mofa == 'no':
            return 'Cycle path - no mopeds'
        if foot == 'use_sidepath':
            return 'Cycle path - no mopeds'
        if foot in ('yes', 'designated', 'permissive'):
            return 'Bike path'
        if bicycle == 'designated' and foot not in ('yes', 'designated', 'permissive'):
            return 'Cycle path - no mopeds'
        if segregated == 'no':
            return 'Bike path'
        if segregated == 'yes':
            return 'Cycle path - no mopeds'
        return 'Moped/Bicycle path'

    # ── PATH without explicit bicycle tag (FIX 7: Layer 3 inferred) ───────────
    if highway == 'path' and bicycle not in ('yes', 'designated'):
        if foot in ('yes', 'designated'):
            return 'Bike path'          # shared foot+cycle path (NL: onverplicht fietspad)
        return 'Greenway (parks)'       # off-road path, cycling access by NL default

    if (highway in ('footway', 'pedestrian') and bicycle in ('yes', 'designated')
            or highway == 'path'
            and bicycle in ('yes', 'designated')
            and foot in ('yes', 'designated')
            or cw_lane in ('sidepath', 'sidewalk')
            or 'sidepath' in cw_extra or 'sidewalk' in cw_extra):
        return 'Bike lane on sidewalk'

    # ── CYCLE LANES painted on the road ──────────────────────────────────────
    if cw_lane in CYCLEWAY_LANE_VALUES:
        if cw_lane in ('opposite_lane', 'opposite_track', 'opposite'):
            return 'Two-way side bike lane'
        if ('cycleway_both' in cw_tags or
                ('cycleway_left' in cw_tags and 'cycleway_right' in cw_tags)):
            return 'Two-way side bike lane'
        return 'One-way side bike lane'

    if cw_extra & CYCLEWAY_LANE_VALUES:
        if ('cycleway_both' in cw_tags or
                ('cycleway_left' in cw_tags and 'cycleway_right' in cw_tags)):
            return 'Two-way side bike lane'
        return 'One-way side bike lane'

    # ── BUS-BIKE LANE ─────────────────────────────────────────────────────────
    if (cw_lane == 'share_busway' or 'share_busway' in cw_extra
            or busway in ('lane', 'yes')):
        return 'Bus-bike lane'

    # ── BICYCLE STREET (cyclestreet / bicycle_road) ───────────────────────────
    if cyclestreet in ('yes', '1', 'true') or bicycle_road in ('yes', '1', 'true'):
        return 'Bicycle street'

    # ── SHARED SPACE (living street) ──────────────────────────────────────────
    if highway == 'living_street':
        return 'Shared space'

    # ── SPEED-BASED CATEGORIES ──────────────────────────────────────────────────
    try:
        speed = float(maxspeed_raw)
        if speed <= 10 and has_explicit_cycle_tag:
            return 'Pacified zone at 10 km-h'
        if speed <= 20 and has_explicit_cycle_tag:
            return 'Pacified street at 20 km/h'
        if speed <= 30 and has_explicit_cycle_tag and bicycle != 'no':
            return 'Shared street at 30 km/h'
    except ValueError:
        pass

    # ── GREENWAY (off-road path through parks or countryside) ─────────────────
    if highway in ('path', 'track') and bicycle in ('yes', 'designated', 'permissive'):
        return 'Greenway (parks)'

    # ── CONTRAFLOW (cycling against the flow on a one-way street) ─────────────
    is_oneway = oneway in ('yes', 'true', '1', '-1')
    has_opposite = cw_lane in ('opposite', 'opposite_lane', 'opposite_track') or \
        bool(cw_extra & {'opposite', 'opposite_lane', 'opposite_track',
                          'opposite_share_busway'})
    if is_oneway and has_opposite:
        return 'Contraflow cycling'

    return 'Other / unclassified'


def has_cycling_infrastructure_bcn(row):
    """
    Return True if this OSM edge should be included in Barcelona's cycling network.

    Key differences from the shared NL06 filter (has_cycling_infrastructure):
      - NO Layer-3 path default: highway=path has no cycling default under
        Spanish/Catalan road law. Paths require an explicit bicycle=yes/designated tag.
      - 30 km/h addition: Barcelona's Ordenanca de Circulacio grants cyclists access
        to any road with maxspeed <= 30 km/h. These are part of the official network.
      - No moped logic: mopeds are banned from bike lanes in Barcelona.

    OSM access layers used:
      Layer 1 — explicit bicycle tag (bicycle=yes / designated / permissive)
      Layer 2 — parent access tag (access=yes / permissive)
      Layer 3 — road type + speed limit default (Spanish/Catalan law)
    """
    def get(f):
        v = row.get(f, None)
        return str(v).lower().strip() if v is not None and str(v) != 'nan' else ''

    highway      = get('highway')
    bicycle      = get('bicycle')
    access       = get('access')
    busway       = get('busway')
    cw           = get('cycleway')
    cyclestreet  = get('cyclestreet')
    bicycle_road = get('bicycle_road')
    maxspeed_raw = get('maxspeed').replace('km/h', '').replace('mph', '').strip()

    try:
        maxspeed = float(maxspeed_raw)
    except ValueError:
        maxspeed = None

    cw_extra = set()
    for col in row.index:
        col_norm = str(col).lower().replace(':', '_')
        if 'cycleway' in col_norm and col_norm != 'cycleway':
            val = str(row[col]).lower().strip()
            if val not in ('nan', 'none', '', 'no', 'false'):
                cw_extra.add(val)

    cw_useful = (cw and cw not in ('nan', 'none', 'no', 'false', 'crossing',
                                    'traffic_island', '')) or \
        bool(cw_extra & CYCLEWAY_USEFUL_VALUES)

    # ── PLAIN_HIGHWAY ─────────────────────────────────────────────────────────
    # Require explicit cycling evidence OR maxspeed <= 30 km/h (BCN city-wide network).
    if highway in PLAIN_HIGHWAY:
        if bicycle == 'no':           return False
        if bicycle == 'use_sidepath': return False
        if cw_useful:                 return True
        if cyclestreet in ('yes', '1', 'true'):  return True
        if bicycle_road in ('yes', '1', 'true'): return True
        if bicycle in ('yes', 'designated', 'permissive'): return True  # Layer 1
        if access in ('yes', 'permissive'):                return True  # Layer 2
        if maxspeed is not None and maxspeed <= 30:        return True  # Layer 3 (BCN)
        return False

    # ── DEDICATED CYCLEWAY ────────────────────────────────────────────────────
    if highway == 'cycleway': return True

    # ── PATH — no Layer-3 default under Spanish law ───────────────────────────
    if highway == 'path':
        if bicycle in ('no', 'dismount', 'use_sidepath'): return False
        if bicycle in ('yes', 'designated', 'permissive'): return True
        return False

    # ── FOOTWAY / PEDESTRIAN: require explicit bicycle tag ────────────────────
    if highway in ('footway', 'pedestrian') \
            and bicycle in ('yes', 'designated'): return True

    # ── OTHER TYPES ───────────────────────────────────────────────────────────
    if highway == 'living_street': return True
    if highway == 'track' \
            and bicycle in ('yes', 'designated', 'permissive'): return True

    if cw_useful:                            return True
    if busway in ('lane', 'yes'):            return True
    if cyclestreet in ('yes', '1', 'true'):  return True
    if bicycle_road in ('yes', '1', 'true'): return True
    if bicycle in ('yes', 'designated', 'permissive'): return True
    if access in ('yes', 'permissive'):                return True
    return False


def classify_cycling_path_bcn(row):
    """
    Map an OSM edge to one of the BCN/AMB cycling categories.

    Categories:
      One-way side bike lane   - physically protected or painted lane, one direction
      Two-way side bike lane   - bidirectional protected lane / standalone cycleway
      Bike lane on sidewalk    - path shared with pedestrians
      Greenway (parks)         - off-road path, bicycle=yes/designated, no pedestrian mixing
      Calmed zone at 10 km/h   - road with maxspeed <= 10
      Calmed street at 20 km/h - road with maxspeed <= 20
      Shared street at 30 km/h - road with maxspeed <= 30 (Barcelona city network)
      Bus-bike lane            - shared bus/bicycle lane
      Contraflow cycling       - contraflow on a one-way street
      Other / unclassified     - doesn't fit any category above

    NOTE: this is the version from classifier_accuracy_test.ipynb, which has a
    maxspeed-based fallback (checked against TEST_CASES) that was NOT present
    in barcelona_vs_official.ipynb's copy of this function. That's a real drift
    bug from copy-pasting between notebooks — see MIGRATION_NOTES.md. This
    version (the one the accuracy test actually validates) is used here.
    """
    def get(field):
        val = row.get(field, None)
        return str(val).lower().strip() if val is not None and str(val) != 'nan' else ''

    highway       = get('highway')
    bicycle       = get('bicycle')
    foot          = get('foot')
    oneway        = get('oneway')
    busway        = get('busway')
    motor_vehicle = get('motor_vehicle')
    segregated    = get('segregated')
    cyclestreet   = get('cyclestreet')
    bicycle_road  = get('bicycle_road')
    maxspeed_raw  = get('maxspeed').replace('km/h', '').replace('mph', '').strip()

    try:
        maxspeed = float(maxspeed_raw)
    except ValueError:
        maxspeed = None

    cw = get('cycleway')
    cw_lane = '' if cw in ('crossing', 'traffic_island', 'no', 'nan', 'none', '') else cw

    cw_tags, cw_extra = set(), set()
    for col in row.index:
        col_norm = str(col).lower().replace(':', '_')
        if col_norm.startswith('cycleway') and col_norm != 'cycleway':
            val = str(row[col]).lower().strip()
            if val not in ('nan', 'none', '', 'no', 'false'):
                cw_tags.add(col_norm)
                cw_extra.add(val)

    has_both  = any('cycleway_both' in t for t in cw_tags)
    oneway_bc = str(row.get('oneway:bicycle', '')).lower().strip()
    is_twoway_cycle = has_both or oneway_bc in ('no', 'false', '0')

    # ── DEDICATED CYCLEWAY (highway=cycleway) ─────────────────────────────────
    if highway == 'cycleway':
        if foot in ('yes', 'designated', 'permissive') and segregated in ('no', ''):
            return 'Bike lane on sidewalk'
        if foot in ('yes', 'designated', 'permissive') and segregated == 'yes':
            return 'Two-way side bike lane'
        if oneway in ('yes', '1', 'true') and oneway_bc not in ('no', 'false', '0'):
            return 'One-way side bike lane'
        return 'Two-way side bike lane'

    # ── PLAIN_HIGHWAY ─────────────────────────────────────────────────────────
    if highway in PLAIN_HIGHWAY:
        if bicycle == 'no':
            return 'Other / unclassified'

        if (cw_lane == 'share_busway' or 'share_busway' in cw_extra
                or busway in ('lane', 'yes')):
            return 'Bus-bike lane'

        if cyclestreet in ('yes', '1', 'true') or bicycle_road in ('yes', '1', 'true'):
            return 'Shared street at 30 km/h'

        if (cw_lane in ('opposite', 'opposite_lane', 'opposite_track')
                or cw_extra & {'opposite', 'opposite_lane', 'opposite_track'}):
            return 'Contraflow cycling'

        if cw_lane == 'track' or 'track' in cw_extra:
            return 'Two-way side bike lane' if is_twoway_cycle else 'One-way side bike lane'

        if cw_lane in ('lane', 'opposite_lane') or 'lane' in cw_extra:
            return 'One-way side bike lane'

        if cw_lane in ('sidepath', 'sidewalk') or cw_extra & {'sidepath', 'sidewalk'}:
            return 'Bike lane on sidewalk'

        if bicycle in ('yes', 'designated', 'permissive'):
            if maxspeed is not None:
                if maxspeed <= 10: return 'Calmed zone at 10 km/h'
                if maxspeed <= 20: return 'Calmed street at 20 km/h'
            return 'Shared street at 30 km/h'

        if maxspeed is not None:
            if maxspeed <= 10: return 'Calmed zone at 10 km/h'
            if maxspeed <= 20: return 'Calmed street at 20 km/h'
            if maxspeed <= 30: return 'Shared street at 30 km/h'

        return 'Other / unclassified'

    # ── FOOTWAY / PEDESTRIAN with cycling allowed ─────────────────────────────
    if highway in ('footway', 'pedestrian') and bicycle in ('yes', 'designated'):
        return 'Bike lane on sidewalk'

    # ── PATH with explicit bicycle tag ────────────────────────────────────────
    if highway == 'path' and bicycle in ('yes', 'designated', 'permissive'):
        if foot in ('yes', 'designated'):
            return 'Bike lane on sidewalk'
        return 'Greenway (parks)'

    # ── CYCLE LANES on non-plain-highway ──────────────────────────────────────
    if cw_lane in CYCLEWAY_LANE_VALUES or cw_extra & CYCLEWAY_LANE_VALUES:
        if (cw_lane == 'share_busway' or 'share_busway' in cw_extra
                or busway in ('lane', 'yes')):
            return 'Bus-bike lane'
        if (cw_lane in ('opposite', 'opposite_lane', 'opposite_track')
                or cw_extra & {'opposite', 'opposite_lane', 'opposite_track'}):
            return 'Contraflow cycling'
        if cw_lane == 'track' or 'track' in cw_extra:
            return 'Two-way side bike lane' if is_twoway_cycle else 'One-way side bike lane'
        return 'One-way side bike lane'

    # ── BUS-BIKE LANE ─────────────────────────────────────────────────────────
    if (cw_lane == 'share_busway' or 'share_busway' in cw_extra
            or busway in ('lane', 'yes')):
        return 'Bus-bike lane'

    # ── BICYCLE STREET / CYCLESTREET ──────────────────────────────────────────
    if cyclestreet in ('yes', '1', 'true') or bicycle_road in ('yes', '1', 'true'):
        return 'Shared street at 30 km/h'

    # ── LIVING STREET ─────────────────────────────────────────────────────────
    if highway == 'living_street':
        if maxspeed is not None:
            if maxspeed <= 10: return 'Calmed zone at 10 km/h'
            if maxspeed <= 20: return 'Calmed street at 20 km/h'
        return 'Shared street at 30 km/h'

    # ── CONTRAFLOW ────────────────────────────────────────────────────────────
    is_oneway    = oneway in ('yes', 'true', '1', '-1')
    has_opposite = (cw_lane in ('opposite', 'opposite_lane', 'opposite_track') or
                    bool(cw_extra & {'opposite', 'opposite_lane', 'opposite_track',
                                      'opposite_share_busway'}))
    if is_oneway and has_opposite:
        return 'Contraflow cycling'

    # ── TRACK with explicit bicycle tag ───────────────────────────────────────
    if highway == 'track' and bicycle in ('yes', 'designated', 'permissive'):
        return 'Greenway (parks)'

    # ── Speed-based catch-all ─────────────────────────────────────────────────
    if maxspeed is not None:
        if maxspeed <= 10: return 'Calmed zone at 10 km/h'
        if maxspeed <= 20: return 'Calmed street at 20 km/h'
        if maxspeed <= 30: return 'Shared street at 30 km/h'

    return 'Other / unclassified'
