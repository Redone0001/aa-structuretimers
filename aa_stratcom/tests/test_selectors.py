from django.test import TestCase
from eveuniverse.tests.testdata.factories_2 import EveTypeFactory

from aa_stratcom.constants import EveCategoryId, EveGroupId, EveTypeId
from aa_stratcom.selectors import supported_eve_types


class TestSupportedEveTypes(TestCase):
    def test_should_return_supported_types(self):
        # given
        structure = EveTypeFactory(eve_group__eve_category__id=EveCategoryId.STRUCTURE)
        control_tower = EveTypeFactory(
            eve_group__id=EveGroupId.CONTROL_TOWER, published=True
        )
        mobile_depot = EveTypeFactory(
            eve_group__id=EveGroupId.MOBILE_DEPOT, published=True
        )
        mercenary_den = EveTypeFactory(
            eve_group__id=EveGroupId.MERCENARY_DEN, published=True
        )
        pirate_fob = EveTypeFactory(
            eve_group__id=EveGroupId.PIRATE_FORWARD_OPERATING_BASE
        )
        customs_office = EveTypeFactory(id=EveTypeId.CUSTOMS_OFFICE)
        orbital_skyhook = EveTypeFactory(id=EveTypeId.ORBITAL_SKYHOOK)
        ihub = EveTypeFactory(id=EveTypeId.IHUB)
        tcu = EveTypeFactory(id=EveTypeId.TCU)

        EveTypeFactory(eve_group__eve_category__id=25)  # Asteroid - should be ignored

        # when
        got = supported_eve_types()

        # then
        self.assertCountEqual(
            got,
            [
                structure,
                control_tower,
                mobile_depot,
                mercenary_den,
                pirate_fob,
                customs_office,
                orbital_skyhook,
                ihub,
                tcu,
            ],
        )
