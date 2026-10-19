from django.core.management import call_command
from django.core.management.base import BaseCommand

from allianceauth.services.hooks import get_extension_logger

from aa_stratcom import __title__
from aa_stratcom.constants import EveCategoryId, EveGroupId, EveTypeId

logger = get_extension_logger(__name__)


class Command(BaseCommand):
    help = "Preloads data required for this app from ESI"

    def handle(self, *args, **options):
        call_command(
            "eveuniverse_load_types",
            __title__,
            "--category_id",
            str(EveCategoryId.STRUCTURE.value),
            "--group_id",
            str(EveGroupId.CONTROL_TOWER.value),
            "--group_id",
            str(EveGroupId.MOBILE_DEPOT.value),
            "--group_id",
            str(EveGroupId.MERCENARY_DEN.value),
            "--group_id",
            str(EveGroupId.PIRATE_FORWARD_OPERATING_BASE.value),
            "--type_id",
            str(EveTypeId.CUSTOMS_OFFICE.value),
            "--type_id",
            str(EveTypeId.ORBITAL_SKYHOOK.value),
            "--type_id",
            str(EveTypeId.TCU.value),
            "--type_id",
            str(EveTypeId.IHUB.value),
        )
