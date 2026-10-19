"""Link every existing scheduled timer to a database record, creating records."""

from django.db import migrations

PRELIMINARY = "PL"
COPY_FIELDS = (
    "eve_solar_system_id",
    "location_details",
    "structure_type_id",
    "structure_name",
    "owner_name",
    "eve_corporation_id",
    "eve_alliance_id",
    "objective",
    "visibility",
    "is_opsec",
    "user_id",
    "eve_character_id",
    "reinforcement_time",
)


def link_timers(apps, schema_editor):
    # Mirrors TimerManager.find_or_create_database_entry with historical models,
    # without the "last updated" bump, so old timers don't look like fresh intel.
    Timer = apps.get_model("structuretimers", "Timer")
    timers = (
        Timer.objects.exclude(timer_type=PRELIMINARY)
        .filter(database_entry__isnull=True, eve_solar_system__isnull=False)
        .order_by("date", "pk")
    )
    for timer in timers.iterator():
        name = (timer.structure_name or "").strip()
        entry = None
        if name and timer.structure_type_id:
            entry = (
                Timer.objects.filter(
                    timer_type=PRELIMINARY,
                    eve_solar_system_id=timer.eve_solar_system_id,
                    structure_type_id=timer.structure_type_id,
                    structure_name__iexact=name,
                )
                .order_by("-last_updated_at")
                .first()
            )
        if entry is None:
            entry = Timer.objects.create(
                timer_type=PRELIMINARY,
                date=None,
                discord_timerboard=False,
                **{field: getattr(timer, field) for field in COPY_FIELDS},
            )
            Timer.objects.filter(pk=entry.pk).update(
                last_updated_at=timer.last_updated_at
            )
        Timer.objects.filter(pk=timer.pk).update(database_entry=entry)


class Migration(migrations.Migration):
    dependencies = [("structuretimers", "0018_timer_database_entry")]

    operations = [migrations.RunPython(link_timers, migrations.RunPython.noop)]
