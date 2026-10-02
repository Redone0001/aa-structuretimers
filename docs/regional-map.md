# Regional system map

The **Regional map** section sits beside Recon campaigns and is independent of campaign membership. It uses the installed EVE SDE, not Dotlan images, external map links, ESI geography requests, or a force-directed layout. Recon campaigns use the same SDE adapter and renderer, with campaign status, bulk selection and reservation-scoped recon actions.

## Installation

This change adds `django-eveonline-sde>=0.2.0,<0.3` as a dependency. In the Alliance Auth `local.py`, enable `modeltranslation` first and `eve_sde` once:

```python
INSTALLED_APPS = ["modeltranslation"] + INSTALLED_APPS
INSTALLED_APPS += ["eve_sde"]
```

Do not duplicate either entry if another app already enables it. Run the normal dependency upgrade, `python manage.py migrate`, `python manage.py esde_load_sde`, and `python manage.py collectstatic --noinput`; restart the application. No Structure Timers model migration is introduced. SDE tables and imported data are required. Missing configuration and empty imports have explicit UI states.

Follow the [SDE package's update-task instructions](https://github.com/Solar-Helix-Independent-Transport/django-eveonline-sde#setup) to keep geography current. The adapter was checked against version 0.2.0 and Alliance Auth 5.4.0.

## Region selection

The picker lists only regions containing an SDE known-space system (system ID 30,000,000–30,999,999). This follows the SDE library's system-ID space classification and excludes wormhole regions such as A-R00001/C-R00001, Abyssal regions and empty imports. Names and system counts are not used: small regions and names containing numbers remain eligible. Missing schematic coordinates do not hide an otherwise eligible region. Global origin search remains unrestricted, and this is a discovery filter rather than a new data-access restriction.

## Saved preferences and campaign map

The browser stores versioned first-party cookies for 180 days (`SameSite=Lax`, `Secure` on HTTPS). `st_regional_map` remembers region, relationship, timer window, range/origin, spacing, overlay toggles, selected system and viewport. `st_campaign_map` remembers campaign list/map mode, region, spacing and overlay toggles. No timer data, reservation identities, permissions or bulk selections are stored. Invalid cookies fall back to defaults; unavailable regions fall back to the first valid option.

A821-A, UUA-F4 and J7HZ-F are hidden in the regional picker unless **Show CCP test regions** is checked. That preference is also remembered. This does not remove explicit campaign systems from a campaign's region selector.

Campaign Map view uses `campaign_sde_map.js` with `system_map.js` and the shared `geography_payload`/`structure_payload` adapters. Its endpoint returns regional `nodes` and `edges`, with node `entryId`, `status`, visible preliminary `count` and grouped `indicators`. Campaigns normalize against the complete SDE region before restricting nodes to campaign membership. Missing SDE systems retain null positions and remain accessible through the selector/List view. The sidebar reuses the permission-filtered list content, so reservation-scoped edit rights and coordinator-only identities stay unchanged. Bulk actions still submit the existing CSRF-protected form and preserve map viewport/selection. New campaigns no longer queue separate ESI gate imports.

## What the map means

- **Relationship:** friendly (blue), neutral (grey), hostile (danger/red), undefined (warning/yellow). These are timer objectives, not EVE standings or sovereignty. Symbols F/N/H/? and tooltips accompany the colours. Friendly uses AA's Bootstrap blue token; danger/warning intentionally follow the selected theme's palette.
- **Timers:** preliminary; active within the next 4 hours; active within the next 24 hours; or all upcoming active timers. Active excludes preliminary records, expired timers and undated records. Limits are evaluated on the server in UTC.
- **Range:** none, Super (6 LY), Carrier (7 LY), Command Carrier (7.5 LY), inclusive. Presets use this module's established distances. Search an origin from any SDE region. Distance is Euclidean geographic distance divided by 9,460,000,000,000,000 metres, not a stargate route or a check of jump-drive skills, security restrictions or destination eligibility.
- **AND:** the same permitted timer must match relationship and time window; its system must also be in range when a range preset is enabled. The dashed outline identifies matching systems. Unknown distance never counts as within range.
- **Indicators:** grouped by system, actual structure type ID and relationship. The bottom-right number counts matching timer records, not deduplicated physical structures (the module has no authoritative structure-instance identifier). Unknown types have a local fallback. Four icons per row, categories start separate rows, at most twelve groups plus a `+N` action that opens all matching timer details in the sidebar.
- **Selection:** a solid neutral outline; hover and keyboard focus use a separate primary outline. Selection does not change the highlight filters.

Structure, gate and label layers toggle independently. With structures off, structure/detail requests and timer matching are paused. Range requests only run when a preset and origin are selected. The geometry request includes only the region's base gate network, with no private module information.

## Architecture and files

| File | Responsibility |
| --- | --- |
| `structuretimers/regional_map.py` | SDE adapter, visible timer aggregation, distance calculation, permitted sidebar actions, JSON endpoints |
| `static/structuretimers/js/system_map.js` | Generic SVG renderer, geometry, indicator layout, selection, pan/zoom, fit and spacing; no module requests or timer logic |
| `static/structuretimers/js/map_preferences.js` | Versioned first-party preference cookies |
| `static/structuretimers/js/campaign_sde_map.js` | Campaign selection, region/overlay controls and reservation-aware sidebar |
| `static/structuretimers/js/regional_map.js` | Filter state, separate/cancellable requests, sidebar and CSRF-protected recon refresh |
| `static/structuretimers/css/regional_map.css` | Scoped AA theme tokens and responsive map/sidebar layout |
| `templates/structuretimers/regional_map.html` | AA Bootstrap page, controls, legend, loading/error status and sidebar |
| `templates/structuretimers/timer_list.html`, `urls.py` | Navigation and routes |
| `pyproject.toml`, `testauth/settings_aa{4,5}/local.py` | SDE dependency and test-project configuration |
| `tests/test_regional_map.py`, `tests/js/test_system_map.cjs`, `tests/browser/*` | Adapter, geometry and browser regression coverage |

The page inherits the existing `allianceauth/base-bs5.html` integration. No extra Bootstrap, jQuery or icon library is loaded.

## Endpoint contract

All endpoints are GET-only, require login and `structuretimers.basic_access`, and have private/no-store cache headers. Timer queries always start with `Timer.objects.visible_to_user(user)`, preserving corporation/alliance ownership and OPSEC rules. No invisible timer names, counts, objectives or action URLs are sent. UI visibility does not replace server permission checks on mutations.

Base: `/structuretimers/map/data/<layer>/`.

- `regions` (optional `include_test=1` reveals CCP test regions): `{ "regions": [{"id": 10000001, "name": "Region"}] }`
- `search?q=...`: `{ "systems": [{"id": 30000001, "name": "System"}] }`; minimum two characters; at most 30 results from all regions.
- `geography?region=...`: public regional data, as below. Gate IDs are canonical sorted pairs. Missing schematic position is `null`, and the system remains in the selector.
- `structures?region=...&relationship=all|FR|NE|HO|UN&window=preliminary|4|24|all`: `{ "systems": [{"id": 30000001, "indicators": [...]}] }`. Only systems with matching visible records appear. SQL aggregates counts before serialization.
- `range?region=...&range=super|carrier|command&source=...`: `{ "limit_ly": 6, "source": {"id": ..., "name": ...}, "systems": [{"id": ..., "distance_ly": 2.5}] }`; missing geographic coordinates produce `null`. The source may be outside the selected region.
- `details?region=...&system=...&relationship=...&window=...`: `{ "timers": [...] }`; fields are `id`, `name`, `type`, `relationship`, `timer_type`, `date`, `owner`, `location`, `edit_url`, `delete_url`, `refresh_url`. Unauthorized actions are null. Delete links lead to the existing confirmation form; recon refresh uses the existing POST endpoint with CSRF. Dates are ISO 8601 or null.

```json
{
  "region": {"id": 10000001, "name": "Region"},
  "origin": [1000, 2000],
  "scale": 1,
  "nodes": [{"id": 30000001, "name": "SYSTEM", "constellation": "Group", "position": [0, 0]}],
  "edges": [{"id": "gate-30000001-30000002", "source": 30000001, "target": 30000002, "kind": "gate", "directed": false}]
}
```

Example indicator:

```json
{"id":"structure-30000001-FR-35832","category":"friendly","label":"Astrahus","type_id":35832,"count":2,"symbol":"F","tooltip":"Astrahus · friendly · 2 timer record(s)"}
```

Renderer extensions may provide connection `color`, `width`, `dashed`, `directed`, `tooltip` and `label`; no module-specific connections are currently emitted. Indicators can supply `action`, handled only by the host's `onIndicator` callback. Data strings are constructed with DOM text APIs, never injected HTML.

## Placement and renderer behaviour

Normalization is based on the full region: origin is minimum X / maximum Y; scale is the median positive nearest-neighbour schematic distance divided by 150 (fallback 1). Output is `(x_2d - origin_x) / scale`, `-(y_2d - origin_y) / scale`. Geography is loaded once per region selection, so timer refreshes and filters cannot change normalization. SDE import changes can change placement on the next geography load.

Spacing multiplies only centre coordinates by 1.0, 1.6 or 2.2 and refits. Nodes retain their map-unit sizes. Actual SVG label width determines the rectangle width, including any symbol supplied in its name. Fit includes indicator/label bounds. Optional label changes refit; refreshes preserve the existing viewport.

Undirected gates are deduplicated on the server. The renderer groups connections by unordered endpoint pair, sorts by stable edge ID and assigns symmetric curve lanes. Opposite directions use consistent physical offsets. Endpoints intersect each rectangle using its measured width and height with a small arrowhead gap. Curves do not merge types or directions. Self-loops use a separate cubic loop. No such synthetic connections are fabricated by the timer adapter.

Each asynchronous layer has an AbortController and response identity check. Filter generations protect overall status from stale results. Selected details also check system identity. Turning a layer off cancels its requests and clears its display. Refresh runs manually and every 60 seconds while the page is visible and focus is outside the map section; automatic refresh waits while the user is interacting. Explicit refresh retains selection, filters, viewport and available focus targets.

## Verification

Verified in a local, real AA 5.4.0 / Django 5.2.17 application with synthetic SDE and timer models:

- Flatly, Darkly and Materia at desktop and 390px mobile width; screenshot inspection and browser-error checks, including the shared campaign map.
- Cookie restoration of region, filters, overlays, spacing and viewport; malformed/obsolete cookie handling; hidden CCP test regions and opt-in display.
- Campaign keyboard multi-selection, reservations, completion, missing-coordinate access and viewport retention after actions.
- Long labels measured inside rectangles, dense systems, several icon rows, overflow indicator, failed icon requests, missing structure types and missing schematic/geographic coordinates.
- Empty region and 500-system region; responsive layout without horizontal page overflow.
- Relationship/time filtering, external origin search and geographic range, selection, keyboard Enter, pan without accidental selection, zoom/fit, and viewport retention through refresh.
- Delayed detail responses and rapid selections; disabled structure layer performs no timer requests.
- Read-only action suppression and hidden OPSEC/corporation records; adapter permission tests and CSRF-protected refresh in the browser.
- Synthetic parallel/directed connections: distinct curves, visible arrows outside measured rectangles, theme contrast.
- 321 Django tests passed; JavaScript map/distribution geometry suites passed. Python formatting and lint checked for changed files.

To repeat browser coverage, use a **fresh disposable AA5 test database**, with `modeltranslation` and `eve_sde` installed and migrated. Never run the fixture on a production/imported SDE database: it creates synthetic EVE IDs and authenticated test sessions.

```sh
MAP_ALLOW_SYNTHETIC_SEED=1 python manage.py shell < structuretimers/tests/browser/seed_regional_map.py
python manage.py runserver 127.0.0.1:8782
# In an environment with Playwright and Chromium installed:
MAP_BASE_URL=http://127.0.0.1:8782 node structuretimers/tests/browser/regional_map.cjs
MAP_BASE_URL=http://127.0.0.1:8782 node structuretimers/tests/browser/map_preferences.cjs
python manage.py test structuretimers.tests.test_regional_map
node --test structuretimers/tests/js/*.cjs
```

`MAP_THEME` selects `flatly`, `darkly` or `materia` for the preference/campaign suite; `MAP_CAMPAIGN_ID` selects a fresh campaign (default `1`). That suite reserves and completes its synthetic entries, so reset/reseed the disposable campaign before rerunning it.

`MAP_SESSION_FILE` selects the local fixture session file (default `/tmp/structure-map-cookies.json`); `MAP_SCREENSHOT_DIR` selects screenshot output. Keep the session file private and delete the test database/sessions after testing.

## Remaining limitations

- No new sovereignty, workforce, capital or Ansiblex data model is introduced. The reusable renderer supports additional connections, but this adapter emits base stargates only.
- Cross-region gates are omitted from a region's graph. Use region selection to inspect other regions.
- Schematic coordinates are preserved, including overlaps already present in the source. Very long names or dense indicator grids may overlap neighbours at Compact spacing; use Comfortable/Spacious and zoom. The map is not a collision-avoidance layout.
- Item art uses EVE's image service; offline/failed requests retain local symbols. The map layout itself is self-contained in the installed SDE.
- Dynamic map/controller strings are currently English; the server template uses Django translations. Additional translated catalog entries are not included.
- Introduced in version 3.5.0 on the `map_view` branch.
