"""Admin module."""

# pylint: disable=missing-class-docstring, missing-function-docstring

from typing import Any, Dict, List, Optional, Tuple

import requests

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import Lower
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.timezone import now
from esi.models import Token
from eveuniverse.models import EveEntity, EveRegion

from allianceauth.eveonline.models import EveAllianceInfo, EveCorporationInfo

from . import destruction, owners, standings, tasks
from .models import (
    DiscordTimerboard,
    DiscordWebhook,
    NotificationRule,
    Operation,
    Organization,
    ReconCampaign,
    ReconCampaignSystem,
    ScheduledNotification,
    StagingSystem,
    StandingsSource,
    Structure,
    StructureHistory,
    Timer,
)


@admin.register(StructureHistory)
class StructureHistoryAdmin(admin.ModelAdmin):
    list_display = ("structure", "action", "at", "scheduled_at", "user")
    list_filter = ("action",)
    search_fields = ("structure__structure_name",)
    raw_id_fields = ("structure", "timer", "next_timer", "user")


@admin.register(Operation)
class OperationAdmin(admin.ModelAdmin):
    list_display = ("name", "visibility", "created_by", "updated_at")
    list_filter = ("visibility",)
    search_fields = ("name", "description")
    raw_id_fields = ("timers",)
    readonly_fields = (
        "created_by",
        "updated_by",
        "created_at",
        "updated_at",
        "revision",
    )

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        obj.revision += 1 if change else 0
        super().save_model(request, obj, form, change)


@admin.register(Structure)
class StructureAdmin(admin.ModelAdmin):
    list_display = ("structure_name", "eve_solar_system", "owner_name", "destroyed_at")
    list_filter = (("destroyed_at", admin.EmptyFieldListFilter),)
    search_fields = ("structure_name", "owner_name", "eve_solar_system__name")
    list_select_related = ("eve_solar_system",)
    readonly_fields = ("destroyed_at", "destroyed_by", "destroyed_by_timer")
    fields = (
        "structure_name",
        "location_details",
        "fitting",
        "details_notes",
        "destroyed_at",
        "destroyed_by",
        "destroyed_by_timer",
    )
    actions = ("restore_structures", "mark_structures_destroyed")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Restore selected structures", permissions=["change"])
    def restore_structures(self, request, queryset):
        with transaction.atomic():
            for structure in queryset.select_for_update():
                destruction.restore_structure(structure, request.user)
        self.message_user(request, "Selected structures restored; history retained.")

    @admin.action(
        description="Mark selected structures destroyed", permissions=["change"]
    )
    def mark_structures_destroyed(self, request, queryset):
        with transaction.atomic():
            for structure in queryset.select_for_update():
                destruction.mark_destroyed(structure, request.user)
        self.message_user(request, "Selected structures archived; history retained.")


class StandingsSourceAdminForm(forms.ModelForm):
    class Meta:
        model = StandingsSource
        fields = ("token",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["token"].queryset = (
            Token.objects.all()
            .require_scopes(standings.CONTACTS_SCOPE)
            .order_by("character_name")
        )
        self.fields["token"].help_text = (
            "Explicitly select a character with alliance-contacts access. "
            "The alliance is verified from ESI when saved."
        )

    def clean(self):
        data = super().clean()
        if StandingsSource.objects.exclude(pk=self.instance.pk).exists():
            raise ValidationError(
                "Only one standings source is supported. Edit the existing source."
            )
        token = data.get("token")
        if token:
            try:
                response = requests.get(
                    f"{owners.ESI_URL}/characters/{token.character_id}/",
                    headers={"User-Agent": owners.USER_AGENT},
                    timeout=(5, 30),
                )
                response.raise_for_status()
                alliance_id = response.json().get("alliance_id")
                if not alliance_id:
                    raise ValidationError(
                        "The selected character is not in an alliance."
                    )
                alliance_name = EveEntity.objects.resolve_name(alliance_id)
            except (requests.RequestException, ValueError) as exc:
                raise ValidationError(
                    "Could not verify the character's alliance. Try again."
                ) from exc
            self.instance.alliance_id = alliance_id
            self.instance.alliance_name = alliance_name
        return data


@admin.register(StandingsSource)
class StandingsSourceAdmin(admin.ModelAdmin):
    form = StandingsSourceAdminForm
    list_display = (
        "alliance_name",
        "character",
        "added_by",
        "last_sync_at",
        "last_error",
    )
    list_select_related = ("token", "added_by")
    search_fields = ("alliance_name", "token__character_name")
    readonly_fields = (
        "alliance_id",
        "alliance_name",
        "added_by",
        "last_sync_at",
        "last_error",
    )
    fields = ("token",) + readonly_fields
    actions = ("sync_now",)

    @admin.display(description="Character")
    def character(self, obj):
        return obj.token.character_name

    def has_add_permission(self, request):
        return (
            super().has_add_permission(request) and not StandingsSource.objects.exists()
        )

    def save_model(self, request, obj, form, change):
        obj.added_by = request.user
        obj.last_sync_at = None
        obj.last_error = ""
        super().save_model(request, obj, form, change)
        self.sync_now(request, StandingsSource.objects.filter(pk=obj.pk))

    @admin.action(
        description="Sync selected standings source now", permissions=["change"]
    )
    def sync_now(self, request, queryset):
        for source in queryset.select_related("token"):
            try:
                standings.sync_source(source)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                source.last_error = str(exc)[:254]
                source.save(update_fields=["last_error"])
                self.message_user(
                    request,
                    f"Standings sync failed: {source.last_error}",
                    messages.ERROR,
                )
            else:
                self.message_user(
                    request,
                    f"Standings synced for {source.alliance_name}.",
                    messages.SUCCESS,
                )
        standings.refresh_objectives()

    def _clear_unused_standings(self):
        if not StandingsSource.objects.exists():
            Organization.objects.update(standing_auto=None)
            standings.refresh_objectives()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        self._clear_unused_standings()

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)
        self._clear_unused_standings()


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "category",
        "alliance",
        "standing_auto",
        "standing_override",
        "effective_standing",
    )
    list_filter = ("category", "standing_auto", "standing_override")
    search_fields = ("name", "=id", "alliance__name")
    list_select_related = ("alliance",)
    autocomplete_fields = ("alliance",)
    readonly_fields = ("standing_auto", "effective_standing", "updated_at")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        standings.refresh_objectives()


@admin.register(DiscordWebhook)
class DiscordWebhookAdmin(admin.ModelAdmin):
    list_display = ("name", "is_enabled", "_messages_in_queue")
    list_filter = ("is_enabled",)
    ordering = ("name",)

    def _messages_in_queue(self, obj):
        return obj.queue_size()

    actions = ["send_test_message", "purge_messages"]

    @admin.display(description="Purge queued messages of selected webhooks")
    def purge_messages(self, request, queryset):
        actions_count = 0
        killmails_deleted = 0
        for webhook in queryset:
            killmails_deleted += webhook.clear_queue()
            actions_count += 1
        self.message_user(
            request,
            f"Purged queued messages for {actions_count} webhooks, "
            f"deleting a total of {killmails_deleted} messages.",
        )

    @admin.display(description="Send test message to selected webhooks")
    def send_test_message(self, request, queryset):
        actions_count = 0
        for webhook in queryset:
            tasks.send_test_message_to_webhook.delay(webhook.pk, request.user.pk)
            actions_count += 1
        self.message_user(
            request,
            f"Initiated sending of {actions_count} test messages to "
            f"selected webhooks. You will receive a notification with the result.",
        )


def field_nice_display(name: str) -> str:
    return name.replace("_", " ").capitalize()


class NotificationRuleAdminForm(forms.ModelForm):
    def clean(self) -> Dict[str, Any]:
        cleaned_data = super().clean()
        _validate_not_same_options_chosen(
            cleaned_data,
            "require_timer_types",
            "exclude_timer_types",
            lambda x: _get_multiselect_display(x, Timer.Type.choices),
        )
        _validate_not_same_options_chosen(
            cleaned_data,
            "require_objectives",
            "exclude_objectives",
            lambda x: _get_multiselect_display(x, Timer.Objective.choices),
        )
        _validate_not_same_options_chosen(
            cleaned_data,
            "require_visibility",
            "exclude_visibility",
            lambda x: _get_multiselect_display(x, Timer.Visibility.choices),
        )
        _validate_not_same_options_chosen(
            cleaned_data,
            "require_corporations",
            "exclude_corporations",
        )
        _validate_not_same_options_chosen(
            cleaned_data,
            "require_alliances",
            "exclude_alliances",
        )
        _validate_not_same_options_chosen(
            cleaned_data,
            "require_regions",
            "exclude_regions",
        )
        _validate_not_same_options_chosen(
            cleaned_data,
            "require_space_types",
            "exclude_space_types",
            lambda x: _get_multiselect_display(x, Timer.SpaceType.choices),
        )
        if (
            cleaned_data["trigger"] == NotificationRule.Trigger.SCHEDULED_TIME_REACHED
            and cleaned_data["scheduled_time"] is None
        ):
            raise ValidationError(
                {
                    "scheduled_time": (
                        "You need to specify scheduled time for "
                        "the `Scheduled time reached` trigger"
                    )
                }
            )

        if cleaned_data["trigger"] == NotificationRule.Trigger.NEW_TIMER_CREATED:
            cleaned_data["scheduled_time"] = None

        return cleaned_data


def _validate_not_same_options_chosen(
    cleaned_data, field_name_1, field_name_2, display_func=lambda x: x
) -> None:
    same_options = set(cleaned_data[field_name_1]).intersection(
        set(cleaned_data[field_name_2])
    )
    if same_options:
        same_options_text = ", ".join(map(str, [display_func(x) for x in same_options]))
        raise ValidationError(
            f"Can not choose same options for {field_nice_display(field_name_1)} "
            f"& {field_nice_display(field_name_2)}: {same_options_text}"
        )


def _get_multiselect_display(value: Any, choices: List[Tuple[Any, str]]) -> str:
    for choice, text in choices:
        if str(choice) == str(value):
            return text

    raise ValueError(f"Invalid choice: {value}")


@admin.register(NotificationRule)
class NotificationRuleAdmin(admin.ModelAdmin):
    form = NotificationRuleAdminForm
    list_display = (
        "id",
        "is_enabled",
        "trigger",
        "_time",
        "webhook",
        "ping_type",
        "_timer_clauses",
    )
    list_filter = ("is_enabled", "trigger")
    ordering = ("id",)

    filter_horizontal = (
        "require_alliances",
        "exclude_alliances",
        "require_corporations",
        "exclude_corporations",
        "require_regions",
        "exclude_regions",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "trigger",
                    "scheduled_time",
                    "webhook",
                    "ping_type",
                    "is_enabled",
                )
            },
        ),
        (
            "Timer clauses",
            {
                "classes": ("extrapretty",),
                "fields": (
                    "require_timer_types",
                    "exclude_timer_types",
                    "require_objectives",
                    "exclude_objectives",
                    "require_corporations",
                    "exclude_corporations",
                    "require_alliances",
                    "exclude_alliances",
                    "require_regions",
                    "exclude_regions",
                    "require_space_types",
                    "exclude_space_types",
                    "require_visibility",
                    "exclude_visibility",
                    "is_important",
                    "is_opsec",
                ),
            },
        ),
    )

    @admin.display(ordering="scheduled time")
    def _time(self, obj) -> Optional[str]:
        if obj.scheduled_time is None:
            return None
        return obj.get_scheduled_time_display()

    def _timer_clauses(self, obj) -> str:
        clauses = []
        for field, func, choices in [
            ("require_timer_types", self._add_to_clauses_1, Timer.Type.choices),
            ("exclude_timer_types", self._add_to_clauses_1, Timer.Type.choices),
            ("require_objectives", self._add_to_clauses_1, Timer.Objective.choices),
            ("exclude_objectives", self._add_to_clauses_1, Timer.Objective.choices),
            ("require_visibility", self._add_to_clauses_1, Timer.Visibility.choices),
            ("exclude_visibility", self._add_to_clauses_1, Timer.Visibility.choices),
            ("require_corporations", self._add_to_clauses_2, None),
            ("exclude_corporations", self._add_to_clauses_2, None),
            ("require_alliances", self._add_to_clauses_2, None),
            ("exclude_alliances", self._add_to_clauses_2, None),
            ("require_regions", self._add_to_clauses_2, None),
            ("exclude_regions", self._add_to_clauses_2, None),
            (
                "require_space_types",
                self._add_to_clauses_1,
                Timer.SpaceType.choices,
            ),
            (
                "exclude_space_types",
                self._add_to_clauses_1,
                Timer.SpaceType.choices,
            ),
            ("is_important", self._add_to_clauses_3, None),
            ("is_opsec", self._add_to_clauses_3, None),
        ]:
            func(clauses, obj, field, choices)

        return mark_safe("<br>".join(clauses)) if clauses else ""

    def _add_to_clauses_1(self, clauses, obj, field, choices):
        if getattr(obj, field):
            text = ", ".join(
                map(
                    str,
                    [_get_multiselect_display(x, choices) for x in getattr(obj, field)],
                )
            )
            self._append_field_to_clauses(clauses, field, text)

    def _add_to_clauses_2(self, clauses, obj, field, choices=None):
        if getattr(obj, field).count() > 0:
            text = ", ".join(map(str, getattr(obj, field).all()))
            self._append_field_to_clauses(clauses, field, text)

    def _add_to_clauses_3(self, clauses, obj, field, choices=None):
        if getattr(obj, field) != NotificationRule.Clause.ANY:
            text = getattr(obj, f"get_{field}_display")()
            self._append_field_to_clauses(clauses, field, text)

    def _append_field_to_clauses(self, clauses, field, text):
        clauses.append(f"{field_nice_display(field)} = {text}")

    actions = ["enable_rule", "disable_rule"]

    @admin.display(description="Enable selected notification rules")
    def enable_rule(self, request, queryset):
        queryset.update(is_enabled=True)
        self.message_user(request, f"Enabled {queryset.count()} notification rules.")

    @admin.display(description="Disable selected notification rules")
    def disable_rule(self, request, queryset):
        queryset.update(is_enabled=False)
        self.message_user(request, f"Disabled {queryset.count()} notification rules.")

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        """overriding this formfield to have sorted lists in the form"""
        if db_field.name in {"require_alliances", "exclude_alliances"}:
            kwargs["queryset"] = EveAllianceInfo.objects.order_by(
                Lower("alliance_name")
            )
        elif db_field.name in {"require_corporations", "exclude_corporations"}:
            kwargs["queryset"] = EveCorporationInfo.objects.order_by(
                Lower("corporation_name")
            )
        elif db_field.name in {"require_regions", "exclude_regions"}:
            kwargs["queryset"] = EveRegion.objects.order_by(Lower("name"))
        return super().formfield_for_manytomany(db_field, request, **kwargs)


@admin.register(ScheduledNotification)
class ScheduledNotificationAdmin(admin.ModelAdmin):
    list_select_related = ("timer", "notification_rule")
    list_display = ("notification_date", "timer", "notification_rule")
    list_filter = ("notification_rule",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(notification_date__gt=now()).order_by("notification_date")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# @admin.register(Timer)
# class TimerAdmin(admin.ModelAdmin):
#     list_select_related = ("eve_solar_system", "structure_type", "user")
#     list_filter = (
#         "timer_type",
#         ("eve_solar_system", admin.RelatedOnlyFieldListFilter),
#         ("structure_type", admin.RelatedOnlyFieldListFilter),
#         "objective",
#         "owner_name",
#         ("user", admin.RelatedOnlyFieldListFilter),
#         "is_opsec",
#     )
#     ordering = ("-date",)
#     autocomplete_fields = ["eve_solar_system", "structure_type"]

#     """
#     def _scheduled_notifications(self, obj):
#         return sorted(
#             [
#                 x["notification_date"].strftime(DATETIME_FORMAT)
#                 for x in ScheduledNotification.objects.filter(
#                     timer=obj, notification_date__gt=now()
#                 ).values("notification_date", "notification_rule_id")
#             ]
#         )
#     """
#     actions = ["send_test_notification"]

#     def send_test_notification(self, request, queryset):
#         for timer in queryset:
#             for webhook in DiscordWebhook.objects.filter(is_enabled=True):
#                 timer.send_notification(
#                     webhook=webhook,
#                     content=f"Test notification sent by **{request.user}**",
#                 )

#             self.message_user(
#                 request, f"Initiated sending test notification for timer: {timer}"
#             )

#         for webhook in DiscordWebhook.objects.filter(is_enabled=True):
#             tasks.send_messages_for_webhook.delay(webhook.pk)

#     send_test_notification.short_description = (
#         "Send test notification for selected timers to all enabled webhooks"
#     )


class StagingSystemAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["eve_solar_system"].required = True

    class Meta:
        model = StagingSystem
        fields = ["eve_solar_system", "is_main"]


@admin.register(StagingSystem)
class StagingSystemAdmin(admin.ModelAdmin):
    list_display = ("eve_solar_system", "_region", "is_main")
    list_select_related = (
        "eve_solar_system",
        "eve_solar_system__eve_constellation__eve_region",
    )
    autocomplete_fields = ["eve_solar_system"]
    ordering = ("eve_solar_system__name",)
    form = StagingSystemAdminForm

    @admin.display(ordering="eve_solar_system__eve_constellation__eve_region")
    def _region(self, obj) -> str:
        if obj.eve_solar_system:
            return obj.eve_solar_system.eve_constellation.eve_region.name
        return ""

    actions = ["_recalc_timers"]

    @admin.display(description="Recalc timers for selected staging system")
    def _recalc_timers(self, request, queryset):
        for obj in queryset:
            tasks.calc_staging_system.delay(obj.pk, force_update=True)
            self.message_user(
                request, f"{obj}: Started to update timers for staging system..."
            )


class ReconCampaignSystemAdminForm(forms.ModelForm):
    completed = forms.BooleanField(required=False, label="Completed")

    class Meta:
        model = ReconCampaignSystem
        fields = ("solar_system", "reserved_by", "completed")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["completed"].initial = bool(self.instance.completed_at)

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.cleaned_data["completed"]:
            instance.completed_at = instance.completed_at or now()
        else:
            instance.completed_at = None
            instance.completed_by = None
        if commit:
            instance.save()
        return instance


class ReconCampaignSystemInline(admin.TabularInline):
    model = ReconCampaignSystem
    form = ReconCampaignSystemAdminForm
    extra = 0
    autocomplete_fields = ("solar_system",)
    raw_id_fields = ("reserved_by",)
    fields = (
        "solar_system",
        "reserved_by",
        "completed",
        "completed_at",
        "completed_by",
    )
    readonly_fields = ("completed_at", "completed_by")

    @admin.display(boolean=True, description="Completed")
    def completed(self, obj):
        return bool(obj.completed_at)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("solar_system", "reserved_by", "completed_by")
        )

    def has_add_permission(self, request, obj=None):
        return bool(
            obj and obj.import_status == "ready"
        ) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return bool(
            obj and obj.import_status == "ready"
        ) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return bool(
            obj and obj.import_status == "ready"
        ) and super().has_delete_permission(request, obj)


@admin.register(ReconCampaign)
class ReconCampaignAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "created_by",
        "created_at",
        "import_status",
        "gates_status",
        "progress",
        "finished_at",
    )
    list_filter = (
        "import_status",
        "gates_status",
        ("finished_at", admin.EmptyFieldListFilter),
    )
    search_fields = ("name", "created_by__username")
    list_select_related = ("created_by",)
    readonly_fields = (
        "created_by",
        "created_at",
        "finished_at",
        "import_status",
        "gates_status",
        "region_ids",
        "open_campaign",
    )
    fields = (
        "name",
        "open_campaign",
        "created_by",
        "created_at",
        "finished_at",
        "import_status",
        "gates_status",
        "region_ids",
    )
    inlines = (ReconCampaignSystemInline,)
    actions = ("retry_failed_imports",)

    def has_add_permission(self, request):
        # Region imports and background jobs are initialized by the campaign form.
        return False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                system_count=Count("systems"),
                complete_count=Count(
                    "systems", filter=Q(systems__completed_at__isnull=False)
                ),
            )
        )

    @admin.display(description="Systems completed")
    def progress(self, obj):
        return f"{obj.complete_count} / {obj.system_count}"

    @admin.display(description="Campaign page")
    def open_campaign(self, obj):
        return format_html('<a href="{}">Open campaign</a>', obj.get_absolute_url())

    def save_formset(self, request, form, formset, change):
        campaign = ReconCampaign.objects.select_for_update().get(pk=form.instance.pk)
        instances = formset.save(commit=False)
        for deleted in formset.deleted_objects:
            deleted.delete()
        for instance in instances:
            if instance.completed_at and not instance.completed_by_id:
                instance.completed_by = request.user
            instance.save()
        formset.save_m2m()
        complete = (
            campaign.import_status == "ready"
            and campaign.systems.exists()
            and not campaign.systems.filter(completed_at__isnull=True).exists()
        )
        campaign.finished_at = (campaign.finished_at or now()) if complete else None
        campaign.save(update_fields=["finished_at"])

    @admin.action(description="Retry failed campaign imports", permissions=["change"])
    def retry_failed_imports(self, request, queryset):
        from .campaign_jobs import enqueue_campaign_job

        count = 0
        for pk in queryset.values_list("pk", flat=True):
            with transaction.atomic():
                campaign = ReconCampaign.objects.select_for_update().get(pk=pk)
                if campaign.import_status == "failed":
                    campaign.import_status = campaign.gates_status = "pending"
                    kind = "systems"
                elif (
                    campaign.import_status == "ready"
                    and campaign.gates_status == "failed"
                ):
                    campaign.gates_status = "pending"
                    kind = "gates"
                else:
                    continue
                campaign.save(update_fields=["import_status", "gates_status"])
                transaction.on_commit(
                    lambda pk=pk, kind=kind: enqueue_campaign_job(pk, kind)
                )
                count += 1
        self.message_user(request, f"Queued retries for {count} campaigns.")


@admin.register(DiscordTimerboard)
class DiscordTimerboardAdmin(admin.ModelAdmin):
    list_display = ("name", "channel_id", "is_enabled", "include_unflagged")
    list_filter = ("is_enabled", "include_unflagged")
    fields = ("name", "channel_id", "is_enabled", "include_unflagged", "timer_types")
