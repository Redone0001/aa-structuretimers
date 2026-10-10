"""Managers."""

# pylint: disable=missing-class-docstring

from datetime import timedelta

from django.contrib.auth.models import User
from django.db import models
from django.utils.timezone import now

from .app_settings import STRUCTURETIMERS_TIMERS_OBSOLETE_AFTER_DAYS


class NotificationRuleQuerySet(models.QuerySet):
    def conforms_with_timer(self, timer: object) -> models.QuerySet:
        """Return new queryset based on current queryset,
        which only contains notification rules that conforms with the given timer.
        """
        matching_rule_pks = []
        for notification_rule in self:
            if notification_rule.is_matching_timer(timer):
                matching_rule_pks.append(notification_rule.pk)

        return self.filter(pk__in=matching_rule_pks)


class NotificationRuleManagerBase(models.Manager):
    pass


NotificationRuleManager = NotificationRuleManagerBase.from_queryset(
    NotificationRuleQuerySet
)


def _visible_to_user(qs: models.QuerySet, user: User) -> models.QuerySet:
    """Apply visibility and OPSEC rules shared by timers and structures."""
    user_characters_qs = user.character_ownerships.select_related("character").values(
        "character__corporation_id", "character__alliance_id"
    )
    user_corporation_ids = {x["character__corporation_id"] for x in user_characters_qs}
    user_alliance_ids = {x["character__alliance_id"] for x in user_characters_qs}
    if not user.has_perm("structuretimers.opsec_access"):
        qs = qs.exclude(is_opsec=True)
    return (
        qs.filter(visibility=qs.model.Visibility.UNRESTRICTED)
        | qs.filter(user=user)
        | qs.filter(
            visibility=qs.model.Visibility.CORPORATION,
            eve_corporation__corporation_id__in=user_corporation_ids,
        )
        | qs.filter(
            visibility=qs.model.Visibility.ALLIANCE,
            eve_alliance__alliance_id__in=user_alliance_ids,
        )
    )


class TimerQuerySet(models.QuerySet):
    def select_related_for_matching(self) -> models.QuerySet:
        """Apply select related for matching."""
        return self.select_related(
            "eve_solar_system",
            "eve_solar_system__eve_constellation__eve_region",
            "eve_corporation",
            "eve_alliance",
        )

    def conforms_with_notification_rule(
        self, notification_rule: object
    ) -> models.QuerySet:
        """Return new queryset based on current queryset,
        which only contains timers that conform with the given notification rule.
        """
        matching_timer_pks = [
            timer.pk
            for timer in self.select_related_for_matching()
            if notification_rule.is_matching_timer(timer)
        ]
        return self.filter(pk__in=matching_timer_pks)

    def visible_to_user(self, user: User) -> models.QuerySet:
        """returns updated queryset of all timers visible to the given user"""
        return _visible_to_user(
            self.select_related("structure_type", "eve_corporation", "eve_alliance"),
            user,
        )

    def filter_by_tab(self, tab_name: str, max_hours_passed: int) -> models.QuerySet:
        """Filter timers for tabs."""
        if tab_name == "current":
            return self.filter(date__gte=now() - timedelta(hours=max_hours_passed))
        if tab_name == "past":
            return self.filter(date__lt=now())
        raise ValueError(f"Invalid tab name: {tab_name}")


class TimerManagerBase(models.Manager):
    def delete_obsolete(self) -> int:
        """delete all timers that are considered obsolete"""
        if STRUCTURETIMERS_TIMERS_OBSOLETE_AFTER_DAYS:
            deadline = now() - timedelta(
                days=STRUCTURETIMERS_TIMERS_OBSOLETE_AFTER_DAYS
            )
            # Timers of a known structure are its reinforcement history.
            _, details = (
                self.filter(date__lt=deadline)
                .filter(structure__isnull=True, structures_structure_id__isnull=True)
                .delete()
            )
            key = f"{self.model._meta.app_label}.{self.model.__name__}"
            if key in details:
                deleted_count = details[key]
                return deleted_count
        return 0


TimerManager = TimerManagerBase.from_queryset(TimerQuerySet)


class DistancesFromStagingManager(models.Manager):
    def calc_timer_for_staging_system(
        self,
        timer: models.Model,
        staging_system: models.Model,
        force_update: bool = False,
    ):
        """Calculate distances for a timer from a staging system."""
        obj, created = self.get_or_create(timer=timer, staging_system=staging_system)
        if force_update or created:
            obj.calculate()
            obj.save()


class StructureQuerySet(models.QuerySet):
    def visible_to_user(self, user: User) -> models.QuerySet:
        """Database records this user may see: recon members only."""
        if not user.has_perm("structuretimers.recon_member"):
            return self.none()
        return _visible_to_user(
            self.select_related("structure_type", "eve_corporation", "eve_alliance"),
            user,
        )


class StructureManagerBase(models.Manager):
    # Fields a new Database record copies from the timer that created it.
    NEW_RECORD_FIELDS = (
        "eve_solar_system_id",
        "location_details",
        "structure_type_id",
        "structure_name",
        "owner_name",
        "owner_corporation_id",
        "eve_corporation_id",
        "eve_alliance_id",
        "objective",
        "visibility",
        "is_opsec",
        "user_id",
        "eve_character_id",
        "reinforcement_time",
    )
    # Blank fields on a matched record that a new timer may fill in.
    FILL_FIELDS = (
        "location_details",
        "owner_name",
        "owner_corporation_id",
        "reinforcement_time",
    )

    def find_matching(self, eve_solar_system_id, structure_type_id, name):
        """Return the record with this system, type and name, ignoring case."""
        name = (name or "").strip()
        if not name or not structure_type_id:
            return None
        return (
            self.filter(
                eve_solar_system_id=eve_solar_system_id,
                structure_type_id=structure_type_id,
                structure_name__iexact=name,
            )
            .order_by("-last_updated_at")
            .first()
        )

    def find_or_create_for_timer(self, timer: models.Model) -> models.Model:
        """Return the Database record of a timer's structure, creating it if needed.

        Timers without a name or type always get a new record, so unrelated
        structures are never merged by guesswork.
        """
        record = self.find_matching(
            timer.eve_solar_system_id, timer.structure_type_id, timer.structure_name
        )
        if record is None:
            record = self.model(
                **{f: getattr(timer, f) for f in self.NEW_RECORD_FIELDS}
            )
            record.save()
            return record
        changed = False
        for field in self.FILL_FIELDS:
            if getattr(timer, field) not in (None, "") and getattr(
                record, field
            ) in (None, ""):
                setattr(record, field, getattr(timer, field))
                changed = True
        if changed:
            record.save()
        else:
            # A new timer is fresh intel that the structure still exists.
            self.filter(pk=record.pk).update(last_updated_at=now())
        return record


StructureManager = StructureManagerBase.from_queryset(StructureQuerySet)
