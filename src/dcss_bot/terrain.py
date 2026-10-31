"""Public terrain IDs for pinned DCSS 0.34.1 / save major 34.

Resolved from dungeon-feature-type.h, not glyphs. Walking across an altar,
fountain, hatch or branch stair does not authorize worship, drinking or traversal.
Water, traps, transporters and sealed/runed doors remain outside safe routing.
"""
CLOSED_DOORS = frozenset({1, 176})
OPEN_DOORS = frozenset({34, 177, 196, 197})
# Solid walls, trees, statues and sealed/runed doors in save major 34.
# Crawl permits diagonal movement between them; this is not a LOS guarantee.
SOLID_CORNER_SIDES = frozenset(range(2, 19))
DOWNSTAIRS = frozenset({42, 43, 44})
UPSTAIRS = frozenset({46, 47, 48})
LAIR_ENTRANCE = 71
LAIR_EXIT = 88
ALTARS = frozenset({103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113,
                    114, 115, 116, 117, 118, 119, 120, 121, 160, 161, 162,
                    165, 167, 168, 170, 195})
FOUNTAINS = frozenset({122, 123, 124, 125, 126, 127, 128, 211})
SAFE_WALKABLE = (CLOSED_DOORS | OPEN_DOORS | DOWNSTAIRS | UPSTAIRS | ALTARS | FOUNTAINS
                 | {33, 45, 49, 60, LAIR_ENTRANCE, LAIR_EXIT, 213})
