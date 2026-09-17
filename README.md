# Structure Timers II

An app for keeping track of Eve Online structure timers with Alliance Auth and Discord.

[![release](https://img.shields.io/pypi/v/aa-structuretimers?label=release)](https://pypi.org/project/aa-structuretimers/)
[![python](https://img.shields.io/pypi/pyversions/aa-structuretimers)](https://pypi.org/project/aa-structuretimers/)
[![django](https://img.shields.io/pypi/djversions/aa-structuretimers?label=django)](https://pypi.org/project/aa-structuretimers/)
[![CI/CD Pipeline](https://github.com/AllianceAuth-Apps/aa-structuretimers/actions/workflows/ci-cd.yaml/badge.svg)](https://github.com/AllianceAuth-Apps/aa-structuretimers/actions/workflows/ci-cd.yaml)
[![codecov](https://codecov.io/github/AllianceAuth-Apps/aa-structuretimers/graph/badge.svg?token=HAHvTzJj2X)](https://codecov.io/github/AllianceAuth-Apps/aa-structuretimers)
[![license](https://img.shields.io/badge/license-MIT-green)](https://github.com/AllianceAuth-Apps/aa-structuretimers#MIT-1-ov-file)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![chat](https://img.shields.io/discord/790364535294132234)](https://discord.gg/zmh52wnfvM)

## Contents

- [Overview](#overview)
- [Features](#features)
- [Screenshots](#screenshots)
- [Installation](#installation)
- [Settings](#settings)
- [Notification Rules](#notification-rules)
- [Staging system](#staging-system)
- [Permissions](#permissions)
- [Management commands](#management-commands)

## Overview

**Structure Timers II** is an enhanced version of the Alliance Auth's Structure Timers app. It offers many additional and useful features and an improved UI. It also provides integrations with several other community apps, e.g. aa-structures.

## Features

Here is an overview of **Structure Timers II**'s main features.

- Create and edit timers for structure and moon mining events
- Add notes and screenshots to timers (e.g. with the structure's fitting)
- Get automatic notifications about upcoming timers on Discord
- Restrict timer access to your corporation, alliance or users with special clearance ("OPSEC")
- Find timers quickly with filters and full text search
- Automatic cleanup of elapsed timers
- Integrated with several other community apps

**Structure Timers II** is integrated with the following community apps:

- [aa-structures](https://github.com/AllianceAuth-Apps/aa-structures): Automatically adds new timers from structure and moon mining notifications
- [allianceauth-opcalendar](https://gitlab.com/paulipa/allianceauth-opcalendar): Shows timers in a calendar view and with other events
- [allianceauth-restapi](https://gitlab.com/munsking/allianceauth-restapi): Adds a REST API for fetching and adding timers

## Screenshots

### List of timers

![timerboard](https://i.imgur.com/MNa2IGl.png)

### Details for a timer

![timerboard](https://i.imgur.com/ZEbl2Vc.png)

### Creating a new timer

![timerboard](https://i.imgur.com/LPCEQNr.png)

### Notification on Discord

![notification](https://i.imgur.com/Knq2bif.png)

## Installation

### Step 1 - Check Preconditions

Please make sure you meet all preconditions before proceeding:

1. Structure Timers is a plugin for [Alliance Auth](https://gitlab.com/allianceauth/allianceauth). If you don't have Alliance Auth running already, please install it first before proceeding. (see the official [AA installation guide](https://allianceauth.readthedocs.io/en/latest/installation/auth/allianceauth/) for details)

2. Structure Timers needs the app [django-eveuniverse](https://github.com/AllianceAuth-Apps/django-eveuniverse) to function. Please make sure it is installed, before continuing.

Note that Structure Timers is compatible with Alliance Auth's Structure Timer app and can be installed in parallel.

### Step 2 - Install app

Make sure you are in the virtual environment (venv) of your Alliance Auth installation. Then install the newest release from PyPI:

```bash
pip install aa-structuretimers
```

### Step 3 - Configure settings

Configure your Auth settings (`local.py`) as follows:

- Add `'structuretimers'` to `INSTALLED_APPS`
- Add the following lines to your settings file:

```python
CELERYBEAT_SCHEDULE['structuretimers_housekeeping'] = {
    'task': 'structuretimers.tasks.housekeeping',
    'schedule': 10800,  # 3 hours
}
CELERYBEAT_SCHEDULE['structuretimers_dispatch_scheduled_notifications'] = {
    'task': 'structuretimers.tasks.dispatch_scheduled_notifications',
    'schedule': 60,
}
```

> **Note**: `structuretimers_dispatch_scheduled_notifications` must be added for scheduled notifications to be delivered at all, since notifications are no longer self-scheduling via Celery ETA. If you are upgrading from an earlier version, make sure to add this periodic task alongside your existing `structuretimers_housekeeping` entry.

- Optional: Add additional settings if you want to change any defaults. See [Settings](#settings) for the full list.

### Step 4 - Finalize installation

Run migrations & copy static files

```bash
python manage.py migrate
python manage.py collectstatic
```

Restart your supervisor services for Auth

### Step 5 - Preload Eve Universe data

In order to be able to select solar systems and structure types for timers you need to preload some data from ESI once. If you already have run those commands previously you can skip this step.

Load Eve Online map:

```bash
python manage.py eveuniverse_load_data map
```

```bash
python manage.py structuretimers_load_eve
```

You may want to wait until the data loading is complete before starting to create new timers.

### Step 6 - Migrate existing timers

If you have already been using the classic app from Auth, you can migrate your existing timers over to **Structure Timers II**. Just run the following command:

```bash
python manage.py structuretimers_migrate_timers
```

> [!NOTE]
> We suggest migration timers before setting up notification rules to avoid potential notification spam for migrated timers.

### Step 7 - Setup notification rules

If you want to receive notifications about timers on Discord you can setup notification rules on the admin site. e.g. you can setup a rule to send notifications 60 minutes before a timer elapses. Please see [Notification Rules](#notification-rules) for details.

### Step 8 - Setup permissions

Another important step is to setup permissions, to ensure the right people have access features. Please see [Permissions](#permissions) for an overview of all permissions.

## Settings

Here is a list of available settings for this app. They can be configured by adding them to your Auth settings file (`local.py`).

> [!TIP]
> All settings are optional and the app will use the documented default settings if they are not used.

Name | Description | Default
-- | -- | --
`STRUCTURETIMERS_MAX_AGE_FOR_NOTIFICATIONS` | Grace period in minutes. A scheduled notification will still be sent if its timer elapsed less than this many minutes ago, and discarded as outdated otherwise | `15`
`STRUCTURETIMERS_NOTIFICATIONS_ENABLED` | Whether notifications for timers are scheduled at all | `True`
`STRUCTURETIMERS_TIMERS_OBSOLETE_AFTER_DAYS` | Minimum age in days for a timer to be considered obsolete. Obsolete timers will automatically be deleted. If you want to keep all timers, set to `None` | `30`
`STRUCTURETIMERS_DEFAULT_PAGE_LENGTH` | Default page size for timerboard. Must be an integer value from the available options in the app. | `10`
`STRUCTURETIMERS_PAGING_ENABLED` | Whether paging is enabled on the timerboard. | `True`
`STRUCTURETIMER_NOTIFICATION_SET_AVATAR` | Whether structuretimers sets the name and avatar icon of a webhook. When False the webhook will use its own values as set on the platform. | `True`

## Notification Rules

In **Structure Timers II** you can receive automatic notifications on Discord for timers by setting up notification rules. Notification rules allow you to define in detail what event and which kind of timers should trigger notifications.

> [!NOTE]
> In general all rules are independent from each other and all enabled rules will be executed for every timer one by one.

### Example setup

Here is an example for a basic setup of rules:

#### Example 1: Notify about every newly created timer without ping (e.g. into a scouts channel)

- Trigger: New timer created
- Scheduled Time: -
- Webhook: YOUR-WEBHOOK
- Ping Type: (no ping)

#### Example 2: Notify 45 minutes before any timer elapses with ping (e.g. into the FC channel)

- Trigger: Scheduled time reached
- Scheduled Time: T - 45 minutes
- Webhook: YOUR-WEBHOOK
- Ping Type: @here

### Key concepts

Here are some key concepts. For all details please see the onscreen help text when creating rules.

#### Triggers

Notifications can be triggered by two kinds of events:

- When a new timers is created
- When the remaining time of timer has reached a defined threshold (e.g. 10 minutes before timer elapses)

#### Webhooks

Each rule has exactly one webhook. You can of course define multiple rules for the same webhook or define rules for different webhooks.

#### Timer clauses

Almost every property of a timer can be used to define rules. For example you can define to get notifications only for timers which hostile objective or only for final timers.

Setting a timer clause is optional - clauses that aren't set will always match any timer.

## Staging system

You can define one or multiple staging systems. Then you can see the distance in jumps and LY from your currently selected staging system to any timer (except for WH systems).

Staging systems can be added or modified on the admin site under: Structure Timers/Staging Systems.

## Permissions

Here are all relevant permissions:

Codename | Description
-- | --
`general - Can access this app and see timers` | Basic permission required by anyone to access this app. Gives access to the list of timers (which timers a user sees can depend on other permissions and settings for a timers)
`general - Can create new timers and edit own timers` | Users with this permission can create new timers and edit or delete their own timers.
`general - Can edit and delete any timer` | Users with this permission can edit and delete any timer.
`general - Can create and see opsec timers` | Users with this permission can create and view timers that are opsec restricted.

## Management commands

The following management commands are available:

- **structuretimers_load_eve**: Preload all eve objects required for this app to function
- **structuretimers_migrate_timers**: Migrate pending timers from Alliance Auth's Structure Timers app


### Recon campaigns

The **Recon campaigns** navigation link lists active and finished campaigns.
Grant `structuretimers.recon_coordinator` and `structuretimers.basic_access`
to a coordinator user or group in Alliance Auth to enable campaign creation and
coordination. Coordinators enter system names (commas or new lines) and/or select
regions. Region systems are imported from ESI; overlapping selections are deduplicated.
System imports run in the background; failures leave a retryable campaign without
publishing partial region membership.

Users with basic access can reserve multiple systems, release their reservations,
and mark their reserved systems complete. Within reserved systems they can add,
edit, refresh, or delete visible preliminary timers, including another scout's
recon. Existing OPSEC visibility is respected. Coordinators can work on any campaign
system and see reservation identities. Everyone sees reservation and completion
status on the campaign page; refresh to load changes made by other users.

A campaign finishes automatically once every system is marked complete, including
systems with no structures. Coordinators may reopen systems, which reactivates the
campaign. Completed systems are read-only within the campaign until reopened.

After upgrading, run `python manage.py migrate` to install the campaign tables and
permission, then assign the coordinator permission to the appropriate group.


Campaigns also offer a **Map view** with one schematic per region. Bundled DOTLAN regional
positions drive the layout; no external map service is contacted. Each selectable system
shows its name and visible preliminary-timer count, with reservation/completion
colors. Selections are shared with the list and use the same bulk actions.
Use the zoom controls and scroll within each map to inspect larger regions.
The chosen view is remembered in the current browser tab. Hold the left mouse
button and drag to pan.

New campaigns import gate connections in the background. For existing campaigns, coordinators can
use **Load / refresh gate connections** in Map view. Missing gate data is shown
explicitly; disconnected campaign systems remain selectable. Maps include only
campaign systems and connections between them within a region, and use bundled coordinates extracted from DOTLAN's regional PDFs.


#### Self-contained regional layouts

The package includes system positions for the available DOTLAN regional maps in
`structuretimers/data/region_layouts.json`. The browser receives coordinates from
Alliance Auth and draws its own interactive SVG: no PDF/image embedding, external
website links, CDN scripts, or requests to DOTLAN are required. Attribution is
plain text. Layout source: DOTLAN EveMaps / Wollari; EVE universe by CCP Games.

Unsupported regions retain the gate-based schematic with an explicit notice.
Systems absent from a bundled map appear below its fixed layout, so existing
system positions never move when the campaign selection changes.

To update bundled positions, developers can run
`python structuretimers/tools/build_region_layouts.py SOURCE_DIRECTORY OUTPUT_JSON`
with local regional PDFs and an `index.json` mapping each region name to its
`file` and list of `systems`. Only this offline build tool requires `pdfplumber`;
production installations have no PDF parser or map download dependency.


#### Campaign responsiveness

Campaign creation saves and redirects immediately, without waiting for ESI. Explicit
systems are available immediately; selected regions show **Preparing** until the
Celery worker finishes importing their systems. Completed region membership is
cached for one hour for subsequent campaigns. Gate imports run separately and
reuse existing universe data; coordinators can request a background refresh.
Import failures and broker errors show a retry control. Pending system imports
cannot be reserved or marked complete. Pages poll a small status endpoint and
refresh on completion unless the user has selected systems.

Map data is requested only on opening Map view. Visible timer counts are aggregated
in the database, and region layout scaling is cached by the server. No recon
visibility restrictions are bypassed or shared between users.

This update requires `python manage.py migrate` and restarting Celery workers so
they discover the campaign import task. The regular Alliance Auth Celery worker
and broker must be running for region imports and gate refreshes to finish.


#### Campaign administration

Django admin lists campaigns under **Structure Timers → Recon campaigns**.
Administrators can search by name or creator, filter import/completion status,
rename campaigns, and delete individual or multiple campaigns. Deletion removes
the campaign's reservations and progress, while keeping recon timers and universe
systems. System rows allow adding/removing systems, changing reservations, and
toggling completion; campaign completion is recalculated automatically.

Import metadata is read-only. System editing is disabled during pending/failed
system imports, and the **Retry failed campaign imports** action queues retries.
Create new campaigns through the regular campaign creation page. Non-superuser
staff need the appropriate Django campaign and campaign-system model permissions.
