from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("structuretimers", "0007_alter_timer_notification_rules"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="schedulednotification",
            name="celery_task_id",
        ),
    ]
