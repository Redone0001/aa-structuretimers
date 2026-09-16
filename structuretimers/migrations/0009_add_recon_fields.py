from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("structuretimers", "0008_remove_schedulednotification_celery_task_id")
    ]

    operations = [
        migrations.AddField(
            model_name="timer",
            name="reinforcement_time",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="timer",
            name="structure_type",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="eveuniverse.evetype",
            ),
        ),
    ]
