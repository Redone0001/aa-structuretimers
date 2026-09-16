"""Bundled region positions; no remote service or PDF library is used at runtime."""

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def region_layouts():
    return json.loads(
        (Path(__file__).parent / "data" / "region_layouts.json").read_text()
    )["regions"]


def get_region_layout(name):
    return region_layouts().get(name)
