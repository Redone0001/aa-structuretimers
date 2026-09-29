from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("structuretimers", "0013_backfill_discord_timerboard"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="timer",
            name="assigned_to",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+", to=settings.AUTH_USER_MODEL,
                verbose_name="Assigned to",
            ),
        ),
    ]
