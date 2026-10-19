"""Views."""

# pylint: disable=too-many-ancestors,missing-function-docstring, missing-class-docstring

import math
from copy import deepcopy
from typing import Iterable

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.timezone import now
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)

from allianceauth.eveonline.evelinks import dotlan
from allianceauth.services.hooks import get_extension_logger
from app_utils.views import JSONResponseMixin, link_html, yesno_str

from structuretimers import (
    __title__,
    information,
    lookup_views,
    standings,
    structures_bridge,
)
from structuretimers.app_settings import (
    STRUCTURETIMERS_DEFAULT_PAGE_LENGTH,
    STRUCTURETIMERS_PAGING_ENABLED,
)
from structuretimers.constants import EveTypeId
from structuretimers.distance_ranges import JUMP_RANGES, distance_range
from structuretimers.forms import (
    FastTimerForm,
    ReconForm,
    TimerForm,
    parse_eve_timer_text,
)
from structuretimers.models import (
    DistancesFromStaging,
    Operation,
    Organization,
    ReconCampaign,
    StagingSystem,
    StandingsSource,
    Structure,
    StructureDistance,
    Timer,
)
from structuretimers.operation_assignments import (
    assignment_html,
    operation_prefetch,
)
from structuretimers.selectors import timer_type_rules
from structuretimers.sync_policy import read_only, require_local_write

# Keep the established view import paths available to callers.
ReconActionView = lookup_views.ReconActionView
Select2DatabaseEntriesView = lookup_views.Select2DatabaseEntriesView
Select2OwnersView = lookup_views.Select2OwnersView
Select2SolarSystemsView = lookup_views.Select2SolarSystemsView
Select2StructureTypesView = lookup_views.Select2StructureTypesView
StandingSetView = lookup_views.StandingSetView
StandingsSyncView = lookup_views.StandingsSyncView
add_standings_source = lookup_views.add_standings_source

logger = get_extension_logger(__name__)
DATETIME_FORMAT = "%Y-%m-%d %H:%M"
MAX_HOURS_PASSED = 2


def bootstrap5_label_html(text: str, label: str = "secondary") -> str:
    """Return HTML for a Bootstrap 5 label."""
    return format_html('<span class="badge text-bg-{}">{}</span>', label, text)


def distance_range_badge_html(light_years: float | None) -> str:
    """Return the most restrictive jump-range badge for a distance."""
    badge = distance_range(light_years)
    return bootstrap5_label_html(*badge) if badge else ""


def timer_action_button_html(url, icon, style, label):
    return format_html(
        '<a href="{}" class="btn btn-{}" title="{}" aria-label="{}">'
        '<i class="{}" aria-hidden="true"></i></a>',
        url,
        style,
        _(label),
        _(label),
        icon,
    )


def fit_buttons_html(fitting: str) -> str:
    """View fit / Copy fit buttons for a structure's fitting, if it has one."""
    if not fitting:
        return ""
    return format_html(
        '<button type="button" class="btn btn-info st-view-fit" title="View fit" '
        'aria-label="View fit" data-fit="{}"><i class="fas fa-list"></i></button>'
        '<button type="button" class="btn btn-secondary st-copy-fit" title="Copy fit" '
        'aria-label="Copy fit" data-fit="{}"><i class="fas fa-clipboard"></i></button>',
        fitting,
        fitting,
    )


class TimerListView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """View for showing a list of timer."""

    template_name = "structuretimers/timer_list.html"
    permission_required = "structuretimers.basic_access"

    def _standings_context(self) -> dict:
        if not self.request.user.has_perm("structuretimers.recon_coordinator"):
            return {}
        organizations = list(Organization.objects.select_related("alliance"))
        for organization in organizations:
            organization.effective = standings.standing_label(
                standings.effective_standing(organization)
            )
            # A corp without its own standing takes its alliance's.
            organization.inherited = (
                organization.standing_override is None
                and organization.standing_auto is None
                and organization.alliance is not None
                and (
                    organization.alliance.standing_override is not None
                    or organization.alliance.standing_auto is not None
                )
            )
        return {
            "organizations": organizations,
            "standing_choices": Organization.Standing.choices,
            "standings_source": StandingsSource.objects.select_related("token").first(),
        }

    def _selected_tab(self) -> str:
        tab = self.request.GET.get("tab", "current")
        tab = {"manage-recon": "preliminary"}.get(tab, tab)
        if tab in ("preliminary", "recon-campaigns") and not self.request.user.has_perm(
            "structuretimers.recon_member"
        ):
            return "current"
        if tab == "standings" and not self.request.user.has_perm(
            "structuretimers.recon_coordinator"
        ):
            return "current"
        if tab == "ops" and not any(
            self.request.user.has_perm("structuretimers." + role)
            for role in ("op_coordinator", "op_viewer")
        ):
            return "current"
        return tab

    def get_context_data(self, **kwargs):
        staging_systems_qs = StagingSystem.objects.select_related(
            "eve_solar_system", "eve_solar_system__eve_constellation__eve_region"
        ).filter(eve_solar_system__isnull=False)
        selected_staging_system = None
        staging_system_name = self.request.GET.get("staging")
        if staging_system_name:
            try:
                selected_staging_system = staging_systems_qs.get(
                    eve_solar_system__name=self.request.GET.get("staging")
                )
            except (StagingSystem.DoesNotExist, ValueError):
                pass
        if not selected_staging_system:
            selected_staging_system = staging_systems_qs.filter(is_main=True).first()
            if not selected_staging_system:
                selected_staging_system = staging_systems_qs.first()
        stageing_systems = staging_systems_qs.order_by("eve_solar_system__name")
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "current_time": now().strftime("%H:%M"),
                "max_hours_expired": MAX_HOURS_PASSED,
                "title": __title__,
                "data_tables_page_length": STRUCTURETIMERS_DEFAULT_PAGE_LENGTH,
                "data_tables_paging": STRUCTURETIMERS_PAGING_ENABLED,
                "selected_staging_system": selected_staging_system,
                "stageing_systems": stageing_systems,
                "tab": self._selected_tab(),
                "jump_ranges": JUMP_RANGES,
                **self._standings_context(),
                "campaigns": ReconCampaign.objects.all(),
                "can_view_ops": any(
                    self.request.user.has_perm("structuretimers." + role)
                    for role in ("op_coordinator", "op_viewer")
                ),
                "operations": Operation.visible_to(self.request.user),
                "recon_translations": {
                    "noMatches": _("No matching recon"),
                    "stale": _("Older than 30 days"),
                    "overlap": _(
                        "%(start)s–%(end)s UTC: average window overlap: %(count)s"
                    ),
                    "peak": _("Peak overlap: %(count)s"),
                    "missing": _(
                        "Without vulnerability windows: %(count)s · excluded from scale"
                    ),
                    "loadError": _(
                        "Could not load recon. Reload the page to try again."
                    ),
                    "confirmDestroy": _(
                        "Mark this structure as destroyed? Its information and timer history will be kept."
                    ),
                    "saving": _("Saving…"),
                    "removed": _("Marked as destroyed. Information and history kept."),
                    "refreshed": _("Recon refreshed to today."),
                    "saveError": _(
                        "Could not save this change. Reload the page and try again."
                    ),
                    "all": _("All"),
                    "solarSystem": _("Solar System"),
                    "region": _("Region"),
                    "structureType": _("Structure Type"),
                    "owner": _("Owner"),
                    "objective": _("Objective"),
                    "table": {
                        "emptyTable": _("No recon available"),
                        "zeroRecords": _("No matching recon"),
                        # Translators: Keep DataTables tokens such as _START_ unchanged.
                        "info": _("Showing _START_ to _END_ of _TOTAL_ entries"),
                        "infoEmpty": _("Showing 0 to 0 of 0 entries"),
                        "infoFiltered": _("(filtered from _MAX_ total entries)"),
                        "lengthMenu": _("Show _MENU_ entries"),
                        "search": _("Search:"),
                        "loadingRecords": _("Loading…"),
                        "processing": _("Processing…"),
                        "paginate": {
                            "first": _("First"),
                            "last": _("Last"),
                            "next": _("Next"),
                            "previous": _("Previous"),
                        },
                        "aria": {
                            "sortAscending": _("Activate to sort column ascending"),
                            "sortDescending": _("Activate to sort column descending"),
                        },
                    },
                },
            }
        )
        return context


class TimerListDataView(
    LoginRequiredMixin, PermissionRequiredMixin, JSONResponseMixin, ListView
):
    """Produce timer list in JSON for AJAX call."""

    model = Timer
    permission_required = "structuretimers.basic_access"

    def render_to_response(self, context, **response_kwargs):
        return self.render_to_json_response(context, **response_kwargs)

    def get_queryset(self):
        qs = super().get_queryset()
        timers_qs = self.filter_timers(qs)
        timers_qs = timers_qs.select_related(
            "eve_solar_system__eve_constellation__eve_region",
            "structure_type__eve_group",
            "eve_character",
            "assigned_to__profile__main_character",
            "eve_corporation",
            "eve_alliance",
            "owner_corporation__alliance",
            "structure",
            "map_state",
        )
        return timers_qs.prefetch_related(operation_prefetch(self.request.user))

    def filter_timers(self, qs):
        """Apply board membership separately from the common row presentation."""
        return (
            qs.visible_to_user(self.request.user)
            .filter(structure__destroyed_at__isnull=True)
            .filter_by_tab(
                tab_name=self.kwargs.get("tab_name"), max_hours_passed=MAX_HOURS_PASSED
            )
        )

    def get_data(self, context):
        data = []
        timers: Iterable[Timer] = self.object_list
        for timer in timers:
            location = self._calc_location_for_timer(timer)
            distances, distance_text = self._calc_distance_for_timer(timer)
            structure = self._calc_structure_for_timer(timer)
            is_restricted, objective = self._calc_objective(timer)
            owner_name, name = self._calc_owner_name(timer)
            visibility = self._calc_visibility(timer)
            distances_light_years = distances.light_years if distances else None
            data.append(
                {
                    "id": timer.id,
                    "local_time": timer.date.isoformat() if timer.date else "",
                    "date": timer.date.isoformat() if timer.date else "",
                    "location": location,
                    "structure_details": structure,
                    "reinforcement_time": (
                        timer.reinforcement_time.strftime("%H:%M")
                        if timer.reinforcement_time is not None
                        else None
                    ),
                    "window_minutes": (
                        30
                        if timer.structure_type_id
                        in {EveTypeId.ANSIBLEX, EveTypeId.METENOX_MOON_DRILL}
                        else 180
                    ),
                    "name_objective": name,
                    "owner": objective,
                    # "creator": creator,
                    "distance": {
                        "display": distance_text,
                        "sort": distances_light_years,
                    },
                    "distance_light_years": distances_light_years,
                    "distance_jumps": distances.jumps if distances else None,
                    "actions": self._get_data_actions(timer),
                    "timer_type_name": timer.get_timer_type_display(),
                    "objective_name": self._calc_standing_name(timer),
                    # Plain values for the map section, which draws table rows.
                    "map": {
                        "system_id": timer.eve_solar_system_id,
                        "region_id": (
                            timer.eve_solar_system.eve_constellation.eve_region_id
                            if timer.eve_solar_system
                            else None
                        ),
                        "objective": timer.objective,
                        "structure_type_id": timer.structure_type_id,
                        "structure_name": timer.structure_name,
                        "location_details": timer.location_details,
                        "timer_type_name": (
                            timer.get_timer_type_display() if timer.date else ""
                        ),
                    },
                    "system_name": timer.eve_solar_system.name,
                    "region_name": timer.eve_solar_system.eve_constellation.eve_region.name,
                    "structure_type_name": (
                        timer.structure_type.name
                        if timer.structure_type
                        else _("(unknown)")
                    ),
                    "owner_name": owner_name,
                    "assignment": self._calc_assignment(timer),
                    "assigned_character_name": "",
                    "visibility": visibility,
                    "opsec_str": yesno_str(timer.is_opsec),
                    "is_opsec": timer.is_opsec,
                    "is_passed": timer.date < now() if timer.date else None,
                    "is_important": timer.is_important,
                    "is_restricted": is_restricted,
                    "last_updated_at": timer.last_updated_at.isoformat(),
                }
            )

        return data

    def _calc_visibility(self, timer):
        if timer.eve_corporation:
            corporation_name = timer.eve_corporation.corporation_name
        else:
            corporation_name = "-"

        visibility = ""
        if timer.visibility == Timer.Visibility.ALLIANCE and timer.eve_alliance:
            visibility = timer.eve_alliance.alliance_name
        elif timer.visibility == Timer.Visibility.CORPORATION:
            visibility = corporation_name
        return visibility

    def _calc_owner_name(self, timer):
        if timer.owner_corporation_id:
            owner_name = timer.owner_corporation.name
            owner = timer.owner_corporation.display_name
        elif timer.owner_name:
            owner_name = timer.owner_name
            owner = owner_name
        else:
            owner = "-"
            owner_name = ""

        structure_name = timer.structure_name if timer.structure_name else "-"
        name = format_html("{}<br>{}", structure_name, owner)
        return owner_name, name

    def _calc_assignment(self, timer):
        return assignment_html(
            timer, self.request.user, editable=self.kwargs.get("tab_name") == "current"
        )

    @staticmethod
    def _calc_standing_name(timer) -> str:
        if timer.owner_corporation_id:
            return standings.standing_label(timer.owner_corporation.effective_standing)
        return timer.get_objective_display()

    def _calc_objective(self, timer):
        tags = []
        is_restricted = False
        if timer.is_opsec:
            tags.append(bootstrap5_label_html("OPSEC", "danger"))
            is_restricted = True

        if timer.visibility != Timer.Visibility.UNRESTRICTED:
            tags.append(bootstrap5_label_html(timer.get_visibility_display(), "info"))
            is_restricted = True

        if timer.is_important:
            tags.append(bootstrap5_label_html("Important", "warning"))

        objective = format_html(
            "{}<br>{}",
            mark_safe(
                bootstrap5_label_html(
                    self._calc_standing_name(timer), timer.label_type_for_objective()
                )
            ),
            mark_safe(" ".join(tags)),
        )

        return is_restricted, objective

    def _calc_structure_for_timer(self, timer):
        if timer.structure_type:
            structure_type_icon_url = timer.structure_type.icon_url(size=64)
            structure_type_name = timer.structure_type.name
        else:
            structure_type_icon_url = ""
            structure_type_name = _("(unknown)")

        context = {
            "type_icon_url": structure_type_icon_url,
            "type_name": structure_type_name,
            "timer_name": timer.get_timer_type_display(),
            "timer_style": timer.label_type_for_timer_type(),
            "reinforcement_time": (
                None
                if self.kwargs.get("tab_name") == "preliminary"
                else timer.reinforcement_time
            ),
        }
        return render_to_string("structuretimers/partials/structure_box.html", context)

    @staticmethod
    def _calc_location_for_timer(timer: Timer):
        location = link_html(
            dotlan.solar_system_url(timer.eve_solar_system.name),
            timer.eve_solar_system.name,
        )
        if timer.location_details:
            location += format_html("<br><em>{}</em>", timer.location_details)

        location += format_html(
            "<br>{}", timer.eve_solar_system.eve_constellation.eve_region.name
        )
        return location

    def _calc_distance_for_timer(self, timer: Timer):
        staging_system_pk = self.request.GET.get("staging")
        if staging_system_pk:
            distances_map = {
                obj.timer_id: obj
                for obj in DistancesFromStaging.objects.filter(
                    staging_system__pk=staging_system_pk
                ).all()
            }
        else:
            distances_map = {}
        try:
            distances = distances_map[timer.id]
        except KeyError:
            distance_text = "?"
            distances = None
        else:
            light_years_text = (
                f"{math.ceil(distances.light_years * 10) / 10} ly"
                if distances.light_years is not None
                else "N/A"
            )
            jumps_text = (
                f"{distances.jumps} jumps" if distances.jumps is not None else "N/A"
            )
            range_badge = distance_range_badge_html(distances.light_years)
            if range_badge:
                distance_text = format_html(
                    "{}<br>{}<br>{}", light_years_text, jumps_text, range_badge
                )
            else:
                distance_text = format_html("{}<br>{}", light_years_text, jumps_text)
        return distances, distance_text

    def _get_data_actions(self, timer: Timer):
        actions = ""
        if any((timer.details_image_url, timer.details_notes)) or (
            (timer.structure_id or timer.structures_structure_id)
            and self.request.user.has_perm("structuretimers.recon_member")
        ):
            disabled_html = ""
            button_type = "primary"
            data_toggle = 'data-bs-toggle="modal" data-bs-target="#modalTimerDetails" '
            title = "Show details of this timer"
        else:
            button_type = "secondary"
            disabled_html = " disabled"
            data_toggle = ""
            title = "No details available"
        actions += (
            format_html(
                '<button type="button" '
                'class="btn btn-{}" title="{}" aria-label="{}" '
                "{}"
                'data-timerpk="{}"{}>'
                '<i class="fas fa-search-plus"></i>'
                "</button>",
                button_type,
                title,
                title,
                mark_safe(data_toggle),
                timer.pk,
                mark_safe(disabled_html),
            )
            + "&nbsp;"
        )
        if timer.user_can_edit(self.request.user):
            actions += (
                timer_action_button_html(
                    reverse("structuretimers:delete", args=(timer.pk,)),
                    "far fa-trash-alt",
                    "danger",
                    "Delete this timer",
                )
                + "&nbsp;"
                + timer_action_button_html(
                    reverse("structuretimers:edit", args=(timer.pk,)),
                    "far fa-edit",
                    "warning",
                    "Edit this timer",
                )
            )
        if (
            self.request.user.has_perm("structuretimers.create_timer")
            and not read_only()
        ):
            actions += "&nbsp;" + timer_action_button_html(
                reverse("structuretimers:copy", args=(timer.pk,)),
                "far fa-copy",
                "success",
                "Copy this timer",
            )
        actions += fit_buttons_html(getattr(timer.structure, "fitting", ""))
        return format_html(
            '<div class="st-timer-actions d-flex flex-wrap justify-content-center gap-1">{}</div>',
            mark_safe(actions.replace("&nbsp;", "")),
        )


class ManageReconDataView(TimerListDataView):
    """All Database records visible to this user, with recon actions."""

    model = Structure
    permission_required = (
        "structuretimers.basic_access",
        "structuretimers.recon_member",
    )

    def get_queryset(self):
        self.kwargs["tab_name"] = "preliminary"
        structures = Structure.objects.visible_to_user(self.request.user)
        if self.request.GET.get("include_destroyed") != "1":
            structures = structures.filter(destroyed_at__isnull=True)
        return structures.select_related(
            "eve_solar_system__eve_constellation__eve_region",
            "structure_type__eve_group",
            "eve_character",
            "eve_corporation",
            "eve_alliance",
            "owner_corporation__alliance",
        )

    def get_data(self, context):
        data = super().get_data(context)
        destroyed = {
            record.pk: record.destroyed_at
            for record in self.object_list
            if record.destroyed_at
        }
        counts = dict(
            Timer.objects.visible_to_user(self.request.user)
            .filter(structure__in=[row["id"] for row in data])
            .values("structure")
            .annotate(count=Count("id"))
            .values_list("structure", "count")
        )
        for row in data:
            row["is_destroyed"] = row["id"] in destroyed
            row["destroyed_at"] = (
                destroyed[row["id"]].isoformat() if row["is_destroyed"] else None
            )
            if row["is_destroyed"]:
                row["name_objective"] = format_html(
                    '{}<br><span class="badge text-bg-danger">{}</span>',
                    row["name_objective"],
                    _("Destroyed"),
                )
            row["timer_count"] = count = counts.get(row["id"], 0)
            if count:
                row["name_objective"] = format_html(
                    '{}<br><span class="badge text-bg-secondary">{}</span>',
                    row["name_objective"],
                    ngettext("%(count)d timer", "%(count)d timers", count)
                    % {"count": count},
                )
        staging_pk = self.request.GET.get("staging")
        staging_system = (
            StagingSystem.objects.select_related("eve_solar_system")
            .filter(pk=staging_pk)
            .first()
            if staging_pk and staging_pk.isdigit()
            else None
        )
        data += structures_bridge.database_rows(
            self.request.user,
            staging_system,
            Timer.objects.visible_to_user(self.request.user),
        )
        return data

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._distances_map = None

    def _calc_distance_for_timer(self, timer):
        staging_system_pk = self.request.GET.get("staging")
        if self._distances_map is None:
            self._distances_map = (
                {
                    obj.structure_id: obj
                    for obj in StructureDistance.objects.filter(
                        staging_system__pk=staging_system_pk
                    )
                }
                if staging_system_pk
                else {}
            )
        distances = self._distances_map.get(timer.id)
        if distances is None:
            return None, "?"
        light_years_text = (
            f"{math.ceil(distances.light_years * 10) / 10} ly"
            if distances.light_years is not None
            else "N/A"
        )
        jumps_text = (
            f"{distances.jumps} jumps" if distances.jumps is not None else "N/A"
        )
        range_badge = distance_range_badge_html(distances.light_years)
        if range_badge:
            return distances, format_html(
                "{}<br>{}<br>{}", light_years_text, jumps_text, range_badge
            )
        return distances, format_html("{}<br>{}", light_years_text, jumps_text)

    def _calc_assignment(self, timer):
        return ""

    def _get_data_actions(self, timer):
        return render_to_string(
            "structuretimers/partials/recon_actions.html",
            {
                "timer": timer,
                "can_edit": timer.user_can_edit(self.request.user),
                "fit_buttons": fit_buttons_html(timer.fitting),
            },
        )


class StructureDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """A Database record with its fitting and reinforcement history."""

    permission_required = (
        "structuretimers.basic_access",
        "structuretimers.recon_member",
    )
    model = Structure
    template_name = "structuretimers/structure_detail.html"

    def get_queryset(self):
        return Structure.objects.visible_to_user(self.request.user).select_related(
            "eve_solar_system", "structure_type", "owner_corporation__alliance"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["now"] = now()
        context["standing"] = standings.standing_label(
            standings.effective_standing(self.object.owner_corporation)
        )
        context["linked_timers"] = (
            Timer.objects.visible_to_user(self.request.user)
            .filter(structure=self.object)
            .order_by("-date")
        )
        context["outcomes"] = self.object.visible_history(self.request.user)
        return context


class TimerDetailDataView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """View for showing details of a timer."""

    permission_required = "structuretimers.basic_access"
    model = Timer

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.visible_to_user(self.request.user).select_related(
            "structure_type", "eve_solar_system", "assigned_to__profile__main_character"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["now"] = now()
        context["operation_assignment"] = assignment_html(
            self.object, self.request.user
        )
        user = self.request.user
        if user.has_perm("structuretimers.recon_member"):
            if self.object.structure_id:
                context["database_entry"] = (
                    Structure.objects.visible_to_user(user)
                    .filter(pk=self.object.structure_id)
                    .first()
                )
            elif self.object.structures_structure_id:
                structure = structures_bridge.get_structure(
                    user, self.object.structures_structure_id
                )
                if structure:
                    context["structures_structure"] = {
                        "name": structures_bridge.display_name(structure),
                        "url": reverse("structures:index"),
                    }
        return context


class TimerManagementView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """View for editing a timer."""

    model = Timer
    form_class = TimerForm
    title = _("Edit Structure Timer")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = self.title
        return context

    def send_success_message(self, keyword: str) -> None:
        """Inform user about result of his action via message."""
        timer = self.object
        messages.info(self.request, f"{keyword}: {timer}.")


class AddUpdateMixin:
    def get_form_kwargs(self):
        """Inject the request user into the kwargs passed to the form."""
        kwargs = super().get_form_kwargs()
        kwargs.update({"user": self.request.user})
        return kwargs


class CreateTimerView(TimerManagementView, AddUpdateMixin, CreateView):
    template_name_suffix = "_create_form"
    permission_required = (
        "structuretimers.basic_access",
        "structuretimers.create_timer",
    )
    title = _("Create New Timer")

    def dispatch(self, request, *args, **kwargs):
        require_local_write()
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        result = super().form_valid(form)
        timer = self.object
        logger.info(
            "Created new timer in %s at %s by user %s",
            timer.eve_solar_system,
            timer.date,
            self.request.user,
        )
        self.send_success_message(_("Added"))
        return result


class AddInformationView(CreateTimerView):
    """One place to add anything: paste, parse, check, save.

    Saving updates the picked or matching Database record instead of creating a
    duplicate, and adds a reinforcement timer when a date is given.
    """

    template_name = "structuretimers/add_information.html"
    title = "Add information"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["timer_type_rules"] = timer_type_rules()
        try:
            context["parsed_timer_event"] = parse_eve_timer_text(
                context["form"].data.get("paste_info", "")
            ).timer_event
        except ValueError:
            context["parsed_timer_event"] = ""
        return context


class ParseInformationView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Read a paste and return the form values found, with the matching record."""

    permission_required = (
        "structuretimers.basic_access",
        "structuretimers.create_timer",
    )

    def post(self, request):
        return JsonResponse(
            information.parse(request.POST.get("text", ""), request.user)
        )


class FastCreateTimerView(CreateTimerView):
    """Create a timer from text copied from EVE Online."""

    form_class = FastTimerForm
    template_name = "structuretimers/timer_fast_create_form.html"
    title = _("Quick Add Timer")


class CreateReconView(CreateTimerView):
    form_class = ReconForm
    template_name = "structuretimers/recon_create_form.html"
    title = _("Add recon")
    permission_required = (
        "structuretimers.basic_access",
        "structuretimers.create_timer",
        "structuretimers.recon_member",
    )


class EditTimerMixin:
    permission_required = "structuretimers.basic_access"

    def dispatch(self, request, *args, **kwargs):
        # Check object-level permission before processing the request - an
        # edit/delete mutates the DB immediately, so checking afterward
        # would be too late.
        if request.user.is_authenticated and self.has_permission():
            self.object = self.get_object()
            can_view = (
                Timer.objects.filter(pk=self.object.pk)
                .visible_to_user(self.request.user)
                .exists()
            )
            can_edit = self.object.user_can_edit(self.request.user)
            if not can_view or not can_edit:
                raise PermissionDenied()

        return super().dispatch(request, *args, **kwargs)


class EditTimerView(EditTimerMixin, TimerManagementView, AddUpdateMixin, UpdateView):
    template_name_suffix = "_update_form"

    def get_success_url(self):
        if self.request.GET.get("tab") in ("preliminary", "manage-recon"):
            return reverse("structuretimers:timer_list") + "?tab=preliminary"
        return super().get_success_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.GET.get("tab") in ("preliminary", "manage-recon"):
            context["cancel_url"] = (
                reverse("structuretimers:timer_list") + "?tab=preliminary"
            )
        return context

    def form_valid(self, form):
        result = super().form_valid(form)
        self.send_success_message(_("Updated"))
        return result


class EditStructureView(TimerManagementView, AddUpdateMixin, UpdateView):
    """Edit a Database record, optionally adding a reinforcement timer."""

    model = Structure
    form_class = ReconForm
    template_name = "structuretimers/timer_update_form.html"
    title = "Edit Database record"
    permission_required = (
        "structuretimers.basic_access",
        "structuretimers.recon_member",
    )

    def get_queryset(self):
        return Structure.objects.visible_to_user(self.request.user)

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.has_permission():
            if not self.get_object().user_can_edit(request.user):
                raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["cancel_url"] = (
            reverse("structuretimers:timer_list") + "?tab=preliminary"
        )
        return context

    def get_success_url(self):
        return reverse("structuretimers:timer_list") + "?tab=preliminary"

    def form_valid(self, form):
        result = super().form_valid(form)
        self.send_success_message(_("Updated"))
        return result


class RemoveStructureView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """Delete a Database record."""

    model = Structure
    template_name = "structuretimers/timer_confirm_delete.html"
    permission_required = (
        "structuretimers.basic_access",
        "structuretimers.recon_member",
    )

    def get_queryset(self):
        return Structure.objects.visible_to_user(self.request.user)

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.has_permission():
            if not self.get_object().user_can_edit(request.user):
                raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return reverse("structuretimers:timer_list") + "?tab=preliminary"


class CopyTimerView(CreateTimerView):
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        old_obj = get_object_or_404(
            Timer.objects.visible_to_user(self.request.user), pk=self.kwargs["pk"]
        )
        new_obj = deepcopy(old_obj)
        new_obj.pk = None
        new_obj.date = None
        kwargs["instance"] = deepcopy(new_obj)
        return kwargs


class RemoveTimerView(
    EditTimerMixin, LoginRequiredMixin, PermissionRequiredMixin, DeleteView
):
    model = Timer

    def get_success_url(self) -> str:
        return self.object.get_absolute_url()
