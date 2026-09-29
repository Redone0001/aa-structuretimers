# Alliance Auth 5 frontend review

Reviewed locally on 2026-09-29 for version 3.4.12.

## Reference and environment

The reference was the actual installed Alliance Auth package, especially
`allianceauth/templates/allianceauth/base-bs5.html`, its asset bundles,
`allianceauth/theme/templatetags/theme_tags.py`, the Flatly/Darkly/Materia
`auth_hooks.py` files, and the menu/sidebar integration.

| Component | Verified version |
| --- | --- |
| Alliance Auth | 5.4.0 |
| Django | 5.2.17 |
| Python | 3.12 |
| Flatly, Darkly, Materia | Bootswatch 5.3.3, through AA's installed theme hooks |
| Bootstrap JS | 5.3.3, loaded by AA |
| jQuery | 3.7.1, loaded by AA |
| django-bootstrap5 | 26.3 |
| allianceauth-app-utils | 1.33.2 |
| django-eveuniverse | 2.1.0 |

A real Django/AA server rendered the module, authenticated synthetic users with
Django sessions, handled CSRF-protected requests and persisted changes. The
isolated environment used SQLite, an in-memory Redis-compatible cache
(fakeredis), and a Celery memory broker. No production database, live EVE data,
Discord server or background worker was used. The unused MySQL driver was not
installed; Redis server-version checks were omitted from this temporary setup.
These service substitutions did not replace or mock the rendered AA pages.

Synthetic fixtures included a coordinator, a creator, a read-only user, 1,001
additional character profiles, current/past/preliminary timers, a hidden OPSEC
timer, long names/notes, and a three-system campaign with different statuses.

## Changes

- Retained AA's `base-bs5.html`, sidebar/menu hook and Bootstrap asset bundles.
  Restored AA's default character controls and corrected navbar list markup.
- Removed broad modal/form overrides and unused global layout CSS. Ordinary
  Bootstrap form styling now comes from the selected AA theme.
- Shared scoped theme-token styling between list and form Select2 widgets;
  scoped the datepicker rules to this module's own picker. No new frontend
  dependency was introduced.
- Improved warning-row/link and status-badge contrast using paired Bootstrap
  background/text tokens. Corrected Darkly and Materia outline controls,
  including Materia's explicit colour rules. Removed fixed map fallback colours.
- Added proper tab/panel associations, modal labelling/scrolling, decorative
  image alternatives, meaningful icon-link names and autocomplete labels.
  Assignment saves restore keyboard focus to the selector.
- Wrapped crowded timer actions and campaign controls; long labels wrap and
  wide tables/maps remain inside their scroll containers.
- Added a timer-form double-submit guard. Campaign actions now submit their
  existing CSRF forms asynchronously, retaining server authorization, reporting
  errors, and disabling repeated submissions.
- Campaign updates retain selected systems, map zoom/pan and content scroll;
  map statuses/counts and checkbox accessibility state update in place. Polling
  displays a refresh notice instead of unexpectedly reloading the page.
- Fixed recon filter reset to clear the actual checkbox filters and their labels.
- Closed a visibility bypass in timer copying: both GET and POST now retrieve the
  source through `visible_to_user`, before constructing the form.

## Browser verification

Used the real in-app browser at 1440×1000 and 390×844. Screenshots were inspected
throughout, including final timer-table screenshots for each theme and the final
Materia mobile map. Mobile content was checked with AA's own sidebar collapsed;
AA's desktop-open sidebar can remain open when resizing the browser.

| Workflow | Flatly | Darkly | Materia |
| --- | --- | --- | --- |
| Timer list, long names, badges, controls | Desktop + mobile | Desktop + mobile | Desktop + mobile |
| Main-character autocomplete | Keyboard search and save | Search results and focus | Save while preserving a system filter |
| Timer detail modal | Desktop | Mobile | Desktop |
| Timer forms | Quick-create form inspected | Quick-create and edit saved; calendar inspected | Server-side invalid-paste validation |
| Campaign list/map | Desktop; conflict feedback | Keyboard selection, completion, retained zoom | Desktop + mobile, zoom controls |
| Read-only controls | Backend tests | Browser: no editors or OPSEC markers | Backend tests |

Also verified recon refresh with a system filter retained, reset to all rows,
loading/success feedback, and a campaign reservation conflict preserving the
selected system and map view. A completed campaign action retained the map's
200px SVG width and one selected system while updating the completion count.

The final browser console inspections reported no JavaScript errors or warnings.
The page loaded one AA jQuery script and one AA Bootstrap script; the module did
not load duplicates. Root-document width stayed within the tested viewport;
wide content uses local scrolling.

## Regression checks

- **313 Django tests passed**, including the existing form, view, campaign,
  notification and model suites.
- Added coverage for hidden-copy GET/POST rejection, absence of hidden markers
  from JSON/detail responses, read-only assignment controls, single AA asset
  loading and modal accessibility associations.
- Existing tests cover assignment authorization and CSRF rejection, campaign
  reservations and hidden recon/map data.
- `makemigrations --check --dry-run`: no changes detected.
- JavaScript syntax checks and `git diff --check`: passed.

The complete local test invocation was:

```sh
PYTHONPATH=/tmp:. DJANGO_SETTINGS_MODULE=structuretimers_test_settings \
  /tmp/structuretimers-aa5-review/bin/python \
  /tmp/structuretimers_review_manage.py test structuretimers.tests --noinput
```

The temporary settings and runner adapt the repository's AA 5 settings to the
isolated services described above. They are not part of the production module.

## Limits

This verifies representative workflows on **AA 5.4.0 with these three 5.3.3
Bootswatch themes**, not every AA 5.x version, custom theme or browser engine.
Physical touch devices, screen-reader output and formal WCAG conformance were
not certified. Production MySQL/Redis/Celery, ESI imports, real gate downloads,
Discord delivery and deployed static-file caching were not exercised. Browser
map tests used synthetic fallback layouts; the existing map/data regressions
also passed. Background import completion was not exercised end-to-end with a
live worker. This module has no frontend CSV export or clipboard-copy workflow
to exercise; access tests cover the underlying HTML and JSON responses.
