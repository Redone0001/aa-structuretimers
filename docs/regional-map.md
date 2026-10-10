# Regional system map

The **Regional map** section sits beside Recon campaigns and is independent of campaign membership. It uses the installed EVE SDE, not Dotlan images, external map links, ESI geography requests, or a force-directed layout. Recon campaigns use the same SDE adapter and renderer, with campaign status, bulk selection and reservation-scoped recon actions.

## Installation

This change adds `django-eveonline-sde>=0.2.0,<0.3` as a dependency. In the Alliance Auth `local.py`, enable `modeltranslation` first and `eve_sde` once:

```python
INSTALLED_APPS = ["modeltranslation"] + INSTALLED_APPS
INSTALLED_APPS += ["eve_sde"]
```

Do not duplicate either entry if another app already enables it. Run the normal dependency upgrade, `python manage.py migrate`, `python manage.py esde_load_sde`, and `python manage.py collectstatic --noinput`; restart the application. The base SDE map introduced no Structure Timers model migration; the experimental battle timeline below requires migration 0016. SDE tables and imported data are required. Missing configuration and empty imports have explicit UI states.

Follow the [SDE package's update-task instructions](https://github.com/Solar-Helix-Independent-Transport/django-eveonline-sde#setup) to keep geography current. The adapter was checked against version 0.2.0 and Alliance Auth 5.4.0.

## Region selection

The picker lists only regions containing an SDE known-space system (system ID 30,000,000–30,999,999). This follows the SDE library's system-ID space classification and excludes wormhole regions such as A-R00001/C-R00001, Abyssal regions and empty imports. Names and system counts are not used: small regions and names containing numbers remain eligible. Missing schematic coordinates do not hide an otherwise eligible region. Global origin search remains unrestricted, and this is a discovery filter rather than a new data-access restriction.

## Saved preferences and campaign map

The browser stores versioned first-party cookies for 180 days (`SameSite=Lax`, `Secure` on HTTPS). `st_regional_map` remembers region, relationship, timer window, range/origin, spacing, overlay toggles, selected system and viewport. `st_campaign_map` remembers campaign region, spacing and overlay toggles. Campaigns open in Map view, with Map on the left and List on the right of the view selector. Available systems use the normal theme background, reserved systems warning fills, and completed systems success fills; symbols remain visible alongside the colours. No timer data, reservation identities, permissions or bulk selections are stored. Invalid cookies fall back to defaults; unavailable regions fall back to the first valid option.

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

Each asynchronous layer has an AbortController and response identity check. Filter generations protect overall status from stale results. Selected details also check system identity. Turning a layer off cancels its requests and clears its display. Refresh runs manually and every 15 minutes while the page is visible; local battle countdowns update every second without requests. Explicit refresh retains selection, filters, viewport and available focus targets.

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

## Experimental battle timeline

Branch: `codex/experimental-regional-battle-map`. Apply migration `0016_mapfleettoken_maptimerstate`, collect static files and restart the app before opening the new page. This adds two tables; it does not change scheduled timer dates or notification jobs.

The **Experimental battle timeline** checkbox enables a UTC day picker, minute-resolution slider and time input. **Live** follows server-corrected time once per second in the browser. Timer transitions, countdowns and map highlights use the loaded snapshot; scrubbing does not make requests. A snapshot refreshes every 15 minutes while visible, on returning after a stale interval, on UTC midnight/day/region changes, after a mutation, and on explicit Refresh. Relationship and range filters also apply to the hour-by-hour breakdown. Turning off the experiment restores the previous preliminary/upcoming overlays. Fleet overlays remain independently switchable.

Scheduled timers open at their timestamp and close after 15 minutes for Armor, Final (armor assumption), and Anchoring, or 30 minutes for Hull. Unanchoring is instantaneous: its occurrence is marked on the map for 60 seconds for discoverability, without a repair window. Other timer types have no inferred repair duration. Estimated repair completion is labeled as an estimate. The list includes the selected day's starts and earlier windows still open or paused at midnight.

**Pause** freezes remaining repair time; **Resume** continues it. **Mark killed** records Won for hostile timers, Lost for friendly timers, and Killed for neutral/undefined timers. **Undo killed** corrects a mistaken observation. These controls are available in Live mode only, use the existing timer edit permissions, and are checked again on the server. Timestamped observations replay when inspecting earlier times. Changing the underlying scheduled date invalidates observations tied to the old date. These are manual battle-map observations, not killboard integration or changes to the main timer/notification lifecycle. History follows the timer’s existing deletion/retention policy (30 days by default).

**Add token** opens a keyboard-accessible dialog, preselecting the selected system. System placement is required; all intelligence fields are optional. Blank counts mean unknown, distinct from zero. Gate and Foe are the defaults. Alliance autocomplete uses the public EVE Universe alliance name cache plus alliances already known to Auth; ship autocomplete uses ship types from the installed SDE. Both accept unmatched text and retain it. Exact matches resolve official alliance logos/ship icons; unmatched names use symbols. D-scan is stored and shown verbatim as text, with no parsing. Edit/move/delete are available from the fleet list or by clicking an editable token on the map.

Fleet intelligence is shared with **everyone who can access the regional map**, independent of private timer visibility. A token's creator and users with `manage_timer` may edit/delete it. Fleets show **latest intelligence**, not historical fleet movements, even when inspecting an earlier timer time. Updates appear locally immediately and on other clients at the next refresh. There is no presence/live collaboration push channel. Unknown positions are accessible from the system selector/list.

The `map/battle/snapshot` GET endpoint returns permission-filtered scheduled timers with battle event histories and regional fleets. `map/battle/lookup` supplies local autocomplete. `map/battle/timer` and `map/battle/fleet` accept CSRF-protected POSTs. Revision checks reject stale updates (409); row locks serialize mutations. All endpoints require login/basic access and disable caching. Snapshot and editor errors preserve an explicit retry path; an open editor is not overwritten by background refresh.

Verification: `tests/test_battle_map.py` covers visibility, authorization, CSRF, invalid counts/choices, optional fields, shared visibility, ship lookup, revision conflicts, pause/resume/kill/undo, and paused carryovers. `tests/js/test_battle_timeline.cjs` covers exact boundaries, multiple pauses across midnight, historical kill outcomes, UTC grouping and instant events. The feature was also exercised in a disposable AA5 browser preview with synthetic data.

### Fleet placement and drag controls

Fleet tokens render separately above the system box at half its measured width, with stance-colored rings around occupied systems (both rings for mixed forces). Multiple tokens occupy separate rows; the small structure indicators retain their layout. Fit includes token bounds. Alliance logos and ship art can appear together, with ship art inset over the logo.

Creators/managers can drag a fleet onto a system box or its surrounding ring. A dashed target ring previews the destination. Coordinates account for map zoom, pan and spacing. Dropping outside a system, releasing on the source, cancelling the pointer, or pressing Escape cancels without saving. A normal click or Enter still opens the editor; Edit / move remains the keyboard alternative and supports systems without map coordinates. Overlay refreshes are deferred during a drag so they cannot remove the token being moved.

The fleet POST endpoint accepts `action: "move"` with `id`, `revision`, and `system_id`. It validates access and destination, changes only location/revision/update time, and rejects stale revisions. The token moves after the server confirms the save; failures leave its prior displayed position and show a retry message. No migration is needed for these drag controls.

### Public alliance directory

Run after upgrading to populate fleet suggestions for all active player alliances, including those with no members in Auth:

```shell
python manage.py structuretimers_sync_alliances
```

This reads CCP ESI's public `/v1/alliances/` list and resolves missing names in batches through Eve Universe. No character token or additional scope is needed. Names/IDs are stored in `eveuniverse.EveEntity` (`eveuniverse_eveentity`, category `alliance`), not Auth's membership records. Suggestions and exact-name logo resolution use local queries only; unknown text remains accepted. Existing records remain usable during an ESI outage, and historical names are retained. This does not import tickers or standings. Existing tokens with unresolved names get their logo when saved again after the import.

To discover newly created alliances daily, add this entry to your existing `CELERYBEAT_SCHEDULE` in Auth settings and restart Celery workers and Beat:

```python
CELERYBEAT_SCHEDULE["structuretimers-sync-alliances"] = {
    "task": "structuretimers.tasks.sync_alliance_directory",
    "schedule": 86400.0,
}
```

Failed refreshes leave the local directory available; rerun the command or wait for the next scheduled run. No additional migration is needed.

### Battle system signals and pause correction

Map presets and web/Discord distance badges share `distance_ranges.JUMP_RANGES`, including Blops at 8 LY. Existing badge boundary semantics remain unchanged.

With the battle timeline enabled, systems use the active Auth theme's Bootstrap semantic colors: warning for timers opening within 15 minutes, info for paused timers, and danger for open repair windows. Open timers pulse in their final five minutes; paused timers never pulse. Reduced-motion preferences replace pulsing with a stronger border. If a system has multiple timers, open takes priority over paused, then upcoming; any open timer in its final five minutes enables the pulse. Relationship colors remain on structure/fleet indicators.

Paused timers expose **−1 min / +1 min** controls to correct remaining repair time after delayed reporting. Corrections are limited to zero through the full repair duration, require timer edit permission, and use the existing revision conflict checks. They are timestamped observations, replay on the timeline, and persist after Resume. The original scheduled timer and notifications are unchanged.

### Force range overlays

Each force card has a **Show ranges / Hide ranges** toggle beside Edit / move (also available to map readers). Gate mobility has no jump range, so its toggle is disabled. Super, Carrier, Conduit carrier, and Blops reuse the map's shared 6 / 7 / 7.5 / 8 LY presets and geographic distance endpoint. Dashed system boxes are blue for a single friendly force and red for a single hostile force. With multiple selected forces, only the intersection is outlined, in the theme's warning yellow. An empty intersection shows no force boxes; timer highlights are independent.

Selections are local to the current map view and clear on region changes. Moving or changing a selected force recalculates its range; deleting it removes its selection on refresh. Ranges are inclusive of the preset limit, exclude unavailable coordinates, and show geometric reach, not verified jump eligibility. Failed loads hide the incomplete intersection and allow toggling off/on to retry. Already loaded distances are reused while the origin and mobility stay unchanged.

### Cross-region force locations

The fleet editor searches solar systems across the installed SDE, with region names in suggestions. A valid system is required; arbitrary alliance and ship text remain accepted. The selected map system pre-fills the editor, including external locations.

The snapshot includes forces in the displayed region, regions directly connected to it by stargates (either direction), and other systems within 8 LY of any system in the displayed region. More distant forces remain stored and appear when viewing their own or a nearby region. Missing geographic coordinates do not exclude forces in directly neighboring regions.

External force systems are grouped into a shelf to the right of the map, labeled **Outside region**. This is schematic placement, with no invented gate connections. Multiple forces in one system share its box. Selecting, editing, dragging, and Show ranges work there; jump ranges still use real SDE coordinates. New shelf locations trigger Fit so newly moved forces remain visible. Regional timer queries remain limited to the displayed region.
