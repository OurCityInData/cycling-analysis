"""
Constants shared across every city's cycling-infrastructure analysis.

These were duplicated verbatim across amsterdam_vs_official.ipynb,
barcelona_vs_bikeneat.ipynb, and freiburg_vs_bikeneat.ipynb. Confirmed
byte-identical (module-level assignment comparison) before being pulled
out here — this is the one shared source of truth now.
"""

# Highway types where cycling access is NOT assumed by default and requires
# explicit tag evidence (bicycle=yes / cycleway=* / cyclestreet=yes) before
# being counted as cycling infrastructure. See has_cycling_infrastructure().
PLAIN_HIGHWAY = {
    'residential', 'unclassified', 'tertiary',
    'secondary', 'primary', 'trunk',
}

# cycleway=* values (or cycleway:left/right/both=* sub-tag values) that count
# as "useful" cycling infrastructure rather than a crossing/no-op tag.
CYCLEWAY_USEFUL_VALUES = {
    'lane', 'track', 'opposite_lane', 'opposite_track',
    'sidepath', 'sidewalk', 'share_busway', 'opposite', 'yes',
}

# cycleway=* / cycleway:left|right=* values that represent a painted or
# physically-separated lane (as opposed to sidepath/sidewalk-style infra).
CYCLEWAY_LANE_VALUES = {'lane', 'track', 'opposite_lane', 'opposite_track'}

# OSM tag keys the BikeNEAT classifier (bikeneat.py) reads off each row.
# Used to build a uniform tag dict regardless of whether pyrosm exposed a
# given tag as its own column or bundled it into a 'tags' JSON column.
OSM_KEYS = [
    "name", "highway", "access", "oneway", "bridge", "tunnel", "junction",
    "service", "maxspeed", "lanes", "lanes:forward", "lanes:backward", "width",
    "surface", "tracktype", "smoothness", "bicycle", "cycle_highway",
    "motor_vehicle", "cyclestreet", "bicycle_road",
    "cycleway", "cycleway:both", "cycleway:left", "cycleway:right",
    "foot", "footway", "sidewalk", "sidewalk:both", "sidewalk:left",
    "sidewalk:right", "sidewalk:foot", "segregated", "indoor", "tram",
    "traffic_sign", "traffic_sign:forward", "cycleway:bicycle",
    "cycleway:left:bicycle", "cycleway:left:lane", "cycleway:left:segregated",
    "cycleway:left:oneway", "cycleway:left:foot", "cycleway:left:traffic_sign",
    "cycleway:right:bicycle", "cycleway:right:lane", "cycleway:right:segregated",
    "cycleway:right:oneway", "cycleway:right:foot", "cycleway:right:traffic_sign",
    "cycleway:both:bicycle", "cycleway:both:lane", "cycleway:both:segregated",
    "cycleway:both:oneway", "cycleway:both:foot", "cycleway:both:traffic_sign",
    "sidewalk:left:bicycle", "sidewalk:left:segregated", "sidewalk:left:oneway",
    "sidewalk:left:foot", "sidewalk:left:traffic_sign",
    "sidewalk:right:bicycle", "sidewalk:right:segregated", "sidewalk:right:oneway",
    "sidewalk:right:foot", "sidewalk:right:traffic_sign",
    "sidewalk:both:bicycle", "sidewalk:both:segregated", "sidewalk:both:oneway",
    "sidewalk:both:foot", "sidewalk:both:traffic_sign",
]
OSM_KEYS_SET = set(OSM_KEYS)
