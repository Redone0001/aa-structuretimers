"""Give every independent StratCom installation its own federation identity."""

from django.db import migrations


def create_identity(apps, schema_editor):
    apps.get_model("aa_stratcom", "TimerSyncConfig").objects.using(
        schema_editor.connection.alias
    ).get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [("aa_stratcom", "0002_alter_mapfleettoken_creator_and_more")]
    operations = [migrations.RunPython(create_identity, migrations.RunPython.noop)]
