"""Seed a disposable, migrated AA5 database; run with manage.py shell."""

import json
import os
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from eve_sde.models import Constellation, Region, SolarSystem, Stargate

from django.test import Client
from django.utils.timezone import now
from eveuniverse.tests.testdata.factories_2 import EveSolarSystemFactory

from structuretimers.tests.testdata.factory import (
    CitadelTypeFactory,
    TimerFactory,
    UserWithAccessFactory,
    UserWithCreateFactory,
)

if os.environ.get("MAP_ALLOW_SYNTHETIC_SEED") != "1":
    raise RuntimeError(
        "Use only a disposable database; set MAP_ALLOW_SYNTHETIC_SEED=1."
    )

creator = UserWithCreateFactory(username="map-creator")
reader = UserWithAccessFactory(username="map-reader")
r = Region.objects.create(id=10000001, name="A · Synthetic dense region")
c = Constellation.objects.create(id=20000001, name="Dense constellation", region=r)
types = [
    CitadelTypeFactory(id=35832, name="Astrahus"),
    CitadelTypeFactory(id=35833, name="Fortizar"),
    CitadelTypeFactory(id=35834, name="Keepstar"),
]
for i in range(24):
    s = SolarSystem.objects.create(
        id=30000001 + i,
        name=(
            "Very long synthetic system name for fit verification"
            if i == 0
            else f"SYSTEM-{i:02}"
        ),
        constellation=c,
        x_2d=(i % 6) * 100 if i != 23 else None,
        y_2d=(i // 6) * 100 if i != 23 else None,
        x=i * 1e15,
        y=0,
        z=0,
    )
    if i > 0:
        Stargate.objects.create(
            id=50000000 + i, name="Gate", solar_system_id=30000000 + i, destination=s
        )
    system = EveSolarSystemFactory(id=s.pk, name=s.name)
    with patch(
        "structuretimers.models._task_calc_timer_distances_for_all_staging_systems",
        Mock(),
    ):
        if i in [0, 1, 23]:
            for relation in ["FR", "NE", "HO", "UN"]:
                for kind in types:
                    TimerFactory(
                        user=creator,
                        eve_solar_system=system,
                        structure_type=kind,
                        structure_name=f"{relation} {kind.name}",
                        timer_type="PL",
                        date=None,
                        objective=relation,
                    )
            TimerFactory(
                user=creator,
                eve_solar_system=system,
                structure_type=types[0],
                structure_name="Grouped duplicate",
                timer_type="PL",
                date=None,
                objective="FR",
            )
            TimerFactory(
                user=creator,
                eve_solar_system=system,
                structure_type=None,
                structure_name="Unknown type",
                timer_type="PL",
                date=None,
                objective="UN",
            )
            TimerFactory(
                user=creator,
                eve_solar_system=system,
                structure_type=types[0],
                structure_name="SECRET-OPSEC",
                timer_type="PL",
                date=None,
                objective="FR",
                is_opsec=True,
            )
            TimerFactory(
                user=creator,
                eve_solar_system=system,
                structure_type=types[0],
                structure_name="Upcoming active",
                timer_type="AR",
                date=now() + timedelta(hours=2),
                objective="HO",
            )
r2 = Region.objects.create(id=10000002, name="B · Large synthetic region")
c2 = Constellation.objects.create(id=20000002, name="Large constellation", region=r2)
SolarSystem.objects.bulk_create(
    [
        SolarSystem(
            id=30001000 + i,
            name=f"LARGE-{i}",
            constellation=c2,
            x_2d=i % 25 * 100,
            y_2d=i // 25 * 100,
            x=i * 1e15,
            y=0,
            z=0,
        )
        for i in range(500)
    ]
)
Stargate.objects.bulk_create(
    [
        Stargate(
            id=51000000 + i,
            name="Gate",
            solar_system_id=30001000 + i - 1,
            destination_id=30001000 + i,
        )
        for i in range(1, 500)
    ]
)
Region.objects.create(id=10000003, name="C · Empty region")
SolarSystem.objects.create(id=32000000, name="External origin", x=0, y=0, z=0)
cookies = {}
for name, user in [("creator", creator), ("reader", reader)]:
    for theme, hook in [
        ("flatly", "flatly.auth_hooks.FlatlyThemeHook"),
        ("darkly", "darkly.auth_hooks.DarklyThemeHook"),
        ("materia", "materia.auth_hooks.MateriaThemeHook"),
    ]:
        client = Client()
        client.force_login(user)
        session = client.session
        session["THEME"] = "allianceauth.theme." + hook
        session.save()
        cookies[name + "-" + theme] = client.cookies["sessionid"].value
Path(os.environ.get("MAP_SESSION_FILE", "/tmp/structure-map-cookies.json")).write_text(
    json.dumps(cookies)
)
print("Seeded synthetic map fixtures")
