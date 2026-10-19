"""Standalone D-scan imports using ship type IDs and the installed EVE SDE.

The type-ID counting and SDE ship/group classification approach follows AA Intel
Tool (https://github.com/ppfeufer/aa-intel-tool). This implementation is independent
and does not import or require that application.
"""

import re
import time
from collections import Counter
from html.parser import HTMLParser

import requests

from django.apps import apps

MAX_SCAN = 50000
MAX_PAGE = 2 * 1024 * 1024
LOGISTICS = {"logistics", "logistics frigate"}
SCAN_URL = re.compile(r"https://dscan\.info/v/[a-fA-F0-9]{12}/?\Z")


def summarize(ships, classes):
    """Choose the most numerous ship and class, with logistics kept separate."""
    if not ships or not classes:
        raise ValueError("No ships found. Paste an EVE D-scan or check the scan URL.")
    # Alphabetical ties make the result stable regardless of query/page order.
    ship = min(ships, key=lambda name: (-ships[name], name.casefold()))
    return {
        "ship_name": ship,
        "dps": max(
            (
                count
                for name, count in classes.items()
                if name.casefold() not in LOGISTICS
            ),
            default=0,
        ),
        "logi": max(
            (count for name, count in classes.items() if name.casefold() in LOGISTICS),
            default=0,
        ),
    }


def parse_paste(text):
    """Count copied tab-separated D-scan rows; ignore non-ship SDE types."""
    if not isinstance(text, str) or len(text) > MAX_SCAN:
        raise ValueError("D-scan text must be at most 50,000 characters.")
    counts = Counter()
    invalid = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        columns = line.split("\t")
        # Work from the ends: player-given object names can contain tabs.
        if len(columns) < 4 or not columns[0].strip().isdigit():
            invalid += 1
            continue
        type_id = int(columns[0].strip())
        if type_id > 2147483647:
            invalid += 1
            continue
        counts[type_id] += 1
    if invalid:
        raise ValueError("Invalid D-scan rows. Copy the full scan directly from EVE.")
    ships, classes = Counter(), Counter()
    types = list(
        apps.get_model("eve_sde", "ItemType")
        .objects.filter(pk__in=counts)
        .values_list("pk", "name", "group__name", "group__category_id")
    )
    if set(counts) - {row[0] for row in types}:
        raise ValueError(
            "Some scan types are missing from the local EVE SDE. Update the SDE and retry."
        )
    for type_id, name, group, category in types:
        if category == 6:
            ships[name] += counts[type_id]
            classes[group] += counts[type_id]
    return summarize(ships, classes)


class ScanPage(HTMLParser):
    """Read only dscan.info's Ships and Ship Classes list entries."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lists = {"ships": Counter(), "shipclasses": Counter()}
        self.section = None
        self.entry = None
        self.capture = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "ul":
            self.section = attributes.get("id")
        elif tag == "li" and self.section in self.lists:
            self.entry = {"name": "", "count": ""}
        elif self.entry is not None:
            if tag == "b":
                self.capture = "name"
            elif tag == "span" and "badge" in attributes.get("class", "").split():
                self.capture = "count"

    def handle_data(self, data):
        if self.entry is not None and self.capture:
            self.entry[self.capture] += data

    def handle_endtag(self, tag):
        if tag in {"b", "span"}:
            self.capture = None
        elif tag == "li" and self.entry is not None:
            name, count = self.entry["name"].strip(), self.entry["count"].strip()
            if name and count.isdigit() and 0 < int(count) <= 2147483647:
                self.lists[self.section][name] += int(count)
            self.entry = None
        elif tag == "ul":
            self.section = None


def fetch_scan(url):
    """Fetch a bounded public scan; never follow redirects to other resources."""
    if not isinstance(url, str) or not SCAN_URL.fullmatch(url):
        raise ValueError("Use a URL like https://dscan.info/v/03b7e51b4e04.")
    try:
        started = time.monotonic()
        with requests.get(
            url, timeout=(5, 10), stream=True, allow_redirects=False
        ) as response:
            if response.status_code != 200:
                raise ValueError(
                    "D-scan page unavailable. Check the URL or paste the scan."
                )
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=16384):
                if time.monotonic() - started > 20:
                    raise ValueError(
                        "D-scan page took too long. Paste the scan instead."
                    )
                size += len(chunk)
                if size > MAX_PAGE:
                    raise ValueError(
                        "D-scan page is too large. Paste the scan instead."
                    )
                chunks.append(chunk)
    except requests.RequestException as exc:
        raise ValueError(
            "Could not load dscan.info. Try again or paste the scan."
        ) from exc
    page = ScanPage()
    page.feed(b"".join(chunks).decode("utf-8", errors="replace"))
    return summarize(page.lists["ships"], page.lists["shipclasses"])


def import_scan(body):
    """Pasted text wins, including when the lower-priority URL is invalid."""
    text = body.get("dscan", "")
    url = body.get("dscan_url", "")
    if not isinstance(text, str) or not isinstance(url, str):
        raise ValueError("D-scan inputs must be text.")
    if text.strip():
        return parse_paste(text)
    if url.strip():
        return fetch_scan(url.strip())
    raise ValueError("Enter a scan URL or paste a D-scan to import.")
