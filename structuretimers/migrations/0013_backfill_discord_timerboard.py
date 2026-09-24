"""Opt existing scheduled timers into flag-only Discord boards."""

from django.db import migrations


def flag_existing_timers(apps, schema_editor):
    Timer = apps.get_model("structuretimers", "Timer")
    Timer.objects.using(schema_editor.connection.alias).exclude(timer_type="PL").update(
        discord_timerboard=True
    )


class Migration(migrations.Migration):
    dependencies = [
        ("structuretimers", "0012_discordtimerboard_timer_discord_timerboard_and_more"),
    ]
    operations = [migrations.RunPython(flag_existing_timers, migrations.RunPython.noop)]
