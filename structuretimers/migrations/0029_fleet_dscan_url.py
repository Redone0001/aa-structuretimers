from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("structuretimers", "0028_role_user_systems")]

    operations = [
        migrations.AddField(
            model_name="mapfleettoken",
            name="dscan_url",
            field=models.URLField(blank=True, max_length=500),
        ),
    ]
