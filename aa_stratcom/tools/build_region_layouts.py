"""Extract bundled map coordinates from local PDFs (development tool only).

Requires pdfplumber. The source directory contains index.json mapping region names
into {"file": "Region.pdf", "systems": ["System name", ...]}. No network is used.
"""

import argparse
import json
import re
from datetime import date
from pathlib import Path

import pdfplumber


def extract_layout(path, names):
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        positions = {}
        for name in names:
            hits = page.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])")
            hits = [h for h in hits if all(c["size"] < 10 for c in h["chars"])]
            if len(hits) > 1:
                # Sovereignty labels can repeat a system name (e.g. Amarr).
                # System names use larger type than the ownership caption.
                largest = max(max(c["size"] for c in h["chars"]) for h in hits)
                hits = [
                    h
                    for h in hits
                    if abs(max(c["size"] for c in h["chars"]) - largest) < 0.01
                ]
            if len(hits) != 1:
                raise ValueError(
                    f"{path.name}: {name}: expected one label, got {len(hits)}"
                )
            hit = hits[0]
            positions[name] = [
                round((hit["x0"] + hit["x1"]) / 2, 3),
                round((hit["top"] + hit["bottom"]) / 2, 3),
            ]
        left = min(p[0] for p in positions.values())
        top = min(p[1] for p in positions.values())
        positions = {
            name: [round(x - left, 3), round(y - top, 3)]
            for name, (x, y) in positions.items()
        }
        return {
            "width": max(p[0] for p in positions.values()),
            "height": max(p[1] for p in positions.values()),
            "positions": positions,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_directory", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    index = json.loads((args.source_directory / "index.json").read_text())
    regions, failures = {}, {}
    for region, source in sorted(index.items()):
        try:
            if "error" in source:
                raise ValueError(source["error"])
            regions[region] = extract_layout(
                args.source_directory / source["file"], source["systems"]
            )
        except Exception as error:
            failures[region] = str(error)
    if not regions:
        raise SystemExit("No valid layouts extracted; output not changed")
    args.output.write_text(
        json.dumps(
            {
                "version": 1,
                "source": "DOTLAN EveMaps / Wollari; EVE universe by CCP Games",
                "extracted": date.today().isoformat(),
                "regions": regions,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )
    print(
        f"Extracted {len(regions)} regions, {sum(len(r['positions']) for r in regions.values())} systems"
    )
    print("Unavailable:", json.dumps(failures))


if __name__ == "__main__":
    main()
