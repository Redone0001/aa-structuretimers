"""Bundled region positions; no remote service or PDF library is used at runtime."""

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def region_layouts():
    return json.loads(
        (Path(__file__).parent / "data" / "region_layouts.json").read_text()
    )["regions"]


@lru_cache(maxsize=128)
def get_region_layout(name):
    layout = region_layouts().get(name)
    if layout is None:
        return None
    scale = 3
    positions = list(layout["positions"].values())
    for i, (x, y) in enumerate(positions):
        for other_x, other_y in positions[i + 1 :]:
            dx, dy = abs(x - other_x), abs(y - other_y)
            if dx or dy:
                scale = max(
                    scale,
                    min(
                        140 / dx if dx else float("inf"),
                        66 / dy if dy else float("inf"),
                    ),
                )
    return {**layout, "scale": scale}
