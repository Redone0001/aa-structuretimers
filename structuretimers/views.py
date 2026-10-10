"""Views."""

# pylint: disable=too-many-ancestors,missing-function-docstring, missing-class-docstring

import math
from copy import deepcopy
from typing import Iterable

import requests

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
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
from esi.decorators import token_required
from eveuniverse.models import EveEntity, EveSolarSystem, EveType

from allianceauth.eveonline.evelinks import dotlan
from allianceauth.services.hooks import get_extension_logger
from app_utils.views import (
    JSONResponseMixin,
    link_html,
    yesno_str,
)

from structuretimers import __title__, owners, standings, structures_bridge
from structuretimers.app_settings import (
    STRUCTURETIMERS_DEFAULT_PAGE_LENGTH,
    STRUCTURETIMERS_PAGING_ENABLED,
)
from structuretimers.constants import EveTypeId
from structuretimers.distance_ranges import distance_range
from structuretimers.forms import FastTimerForm, ReconForm, TimerForm
from structuretimers.models import (
    DistancesFromStaging,
    Organization,
    ReconCampaign,
    StagingSystem,
    StandingsSource,
    Timer,
)
from structuretimers.selectors import supported_eve_types

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
        url, style, _(label), _(label), icon,
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
                **self._standings_context(),
                "campaigns": ReconCampaign.objects.all(),
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
                        "Mark this structure as destroyed and remove its recon timer?"
                    ),
                    "saving": _("Saving…"),
                    "removed": _("Recon removed."),
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
        timers_qs = qs.visible_to_user(self.request.user)
        timers_qs = timers_qs.filter_by_tab(
            tab_name=self.kwargs.get("tab_name"), max_hours_passed=MAX_HOURS_PASSED
        )
        timers_qs = timers_qs.select_related(
            "eve_solar_system",
            "eve_solar_system__eve_constellation__eve_region",
            "structure_type",
            "structure_type__eve_group",
            "eve_character",
            "assigned_to__profile__main_character",
            "eve_corporation",
            "eve_alliance",
            "owner_corporation__alliance",
        )
        return timers_qs

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
                    "objective_name": timer.get_objective_display(),
                    "system_name": timer.eve_solar_system.name,
                    "region_name": timer.eve_solar_system.eve_constellation.eve_region.name,
                    "structure_type_name": (
                        timer.structure_type.name
                        if timer.structure_type
                        else _("(unknown)")
                    ),
                    "owner_name": owner_name,
                    "assignment": self._calc_assignment(timer),
                    "assigned_character_name": timer.assigned_character_name,
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
        if self.kwargs.get("tab_name") not in ("current", "past") and timer.assigned_to_id:
            name = format_html(
                '{}<br><span class="text-muted">{}: {}</span>',
                name, _("Assigned to"), timer.assigned_character_name,
            )
        return owner_name, name

    def _calc_assignment(self, timer):
        if self.kwargs.get("tab_name") == "current" and timer.user_can_edit(self.request.user):
            return render_to_string(
                "structuretimers/partials/timer_assignment.html", {"timer": timer}
            )
        return format_html("{}", timer.assigned_character_name or _("Unassigned"))

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
                    timer.get_objective_display(), timer.label_type_for_objective()
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
        if (
            timer.details_image_url
            or timer.details_notes
            or timer.assigned_to_id
            or (
                (timer.database_entry_id or timer.structures_structure_id)
                and self.request.user.has_perm("structuretimers.recon_member")
            )
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
        if self.request.user.has_perm("structuretimers.create_timer"):
            actions += "&nbsp;" + timer_action_button_html(
                reverse("structuretimers:copy", args=(timer.pk,)),
                "far fa-copy",
                "success",
                "Copy this timer",
            )
        return format_html(
            '<div class="st-timer-actions d-flex flex-wrap justify-content-center gap-1">{}</div>',
            mark_safe(actions.replace("&nbsp;", "")),
        )


class ManageReconDataView(TimerListDataView):
    """All preliminary timers visible to this user, with recon actions."""

    permission_required = (
        "structuretimers.basic_access",
        "structuretimers.recon_member",
    )

    def get_queryset(self):
        self.kwargs["tab_name"] = "preliminary"
        return super().get_queryset()

    def get_data(self, context):
        data = super().get_data(context)
        counts = dict(
            Timer.objects.visible_to_user(self.request.user)
            .filter(database_entry__in=[row["id"] for row in data])
            .values("database_entry")
            .annotate(count=Count("id"))
            .values_list("database_entry", "count")
        )
        for row in data:
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

    def _get_data_actions(self, timer):
        return render_to_string(
            "structuretimers/partials/recon_actions.html",
            {
                "timer": timer,
                "can_edit": timer.user_can_edit(self.request.user),
                "can_copy": self.request.user.has_perm("structuretimers.create_timer"),
            },
        )


class ReconActionView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """POST-only recon mutations; enforce visibility and edit permission."""

    permission_required = (
        "structuretimers.basic_access",
        "structuretimers.recon_member",
    )

    def post(self, request, pk, action):
        timer = get_object_or_404(
            Timer.objects.visible_to_user(request.user).filter(
                timer_type=Timer.Type.PRELIMINARY
            ),
            pk=pk,
        )
        if not timer.user_can_edit(request.user):
            raise PermissionDenied()
        if action == "refresh":
            refreshed_at = now()
            # Update only freshness, without rescheduling notifications or distances.
            Timer.objects.filter(pk=timer.pk, timer_type=Timer.Type.PRELIMINARY).update(
                last_updated_at=refreshed_at
            )
            return JsonResponse({"last_updated_at": refreshed_at.isoformat()})
        if action == "destroy":
            timer.delete()
            return JsonResponse({"deleted": True})
        return JsonResponse({"error": "Unknown action"}, status=400)


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
        user = self.request.user
        if user.has_perm("structuretimers.recon_member"):
            visible = Timer.objects.visible_to_user(user)
            if self.object.timer_type == Timer.Type.PRELIMINARY:
                context["linked_timers"] = visible.filter(
                    database_entry=self.object
                ).order_by("-date")
            elif self.object.database_entry_id:
                context["database_entry"] = visible.filter(
                    pk=self.object.database_entry_id
                ).first()
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

    def get_form_class(self):
        if (
            self.object.timer_type == Timer.Type.PRELIMINARY
            and not self.object.structure_type_id
        ):
            return ReconForm
        return super().get_form_class()

    def form_valid(self, form):
        result = super().form_valid(form)
        self.send_success_message(_("Updated"))
        return result


class AssignTimerView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Update only the assignee, enforcing the usual timer edit permissions."""

    permission_required = "structuretimers.basic_access"

    def get(self, request, pk):
        timer = get_object_or_404(Timer.objects.visible_to_user(request.user), pk=pk)
        if not timer.user_can_edit(request.user):
            raise PermissionDenied()
        query = request.GET.get("term", "").strip()
        try:
            page = max(1, int(request.GET.get("page", 1)))
        except ValueError:
            page = 1
        users = TimerForm.base_fields["assigned_to"].queryset.filter(
            profile__main_character__character_name__icontains=query
        )
        offset = (page - 1) * 20
        matches = list(users[offset:offset + 21]) if query else []
        return JsonResponse({
            "results": [
                {"id": user.pk, "text": user.profile.main_character.character_name}
                for user in matches[:20]
            ],
            "pagination": {"more": len(matches) > 20},
        })

    def post(self, request, pk):
        timer = get_object_or_404(Timer.objects.visible_to_user(request.user), pk=pk)
        if not timer.user_can_edit(request.user):
            raise PermissionDenied()
        if "assigned_to" not in request.POST:
            return JsonResponse({"error": _("Choose an assignee or Unassigned.")}, status=400)
        try:
            assignee = TimerForm.base_fields["assigned_to"].clean(request.POST["assigned_to"])
        except ValidationError as ex:
            return JsonResponse({"error": " ".join(ex.messages)}, status=400)
        timer.assigned_to = assignee
        timer.save(update_fields=["assigned_to", "last_updated_at"])
        return JsonResponse({"assigned_to": timer.assigned_to_id,
                             "assigned_character_name": timer.assigned_character_name})


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


class Select2SolarSystemsView(JSONResponseMixin, ListView):
    """Dynamically generated list of solar systems for select2 widget."""

    model = EveSolarSystem

    def get_queryset(self):
        qs = super().get_queryset()
        term = self.request.GET.get("term")
        if not term:
            return qs.none()
        return qs.filter(name__istartswith=term)

    def get_context_data(self, **kwargs):
        if self.object_list:
            solar_systems = self.object_list.values("id", "name")
            results = [{"id": row["id"], "text": row["name"]} for row in solar_systems]
            results = sorted(results, key=lambda d: d["text"])
        else:
            results = None
        return {"results": results}

    def render_to_response(self, context, **response_kwargs):
        return self.render_to_json_response(context, **response_kwargs)


class StandingSetView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Recon coordinators override the standing of a corporation or alliance."""

    permission_required = (
        "structuretimers.basic_access",
        "structuretimers.recon_coordinator",
    )

    def post(self, request, pk):
        organization = get_object_or_404(Organization, pk=pk)
        value = request.POST.get("standing", "")
        if value == "":
            organization.standing_override = None
        elif value.lstrip("-").isdigit() and int(value) in standings.STANDING_VALUES:
            organization.standing_override = int(value)
        else:
            return JsonResponse({"error": "Unknown standing"}, status=400)
        organization.save(update_fields=["standing_override", "updated_at"])
        return redirect(reverse("structuretimers:timer_list") + "?tab=standings")


class StandingsSyncView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Refresh automatic standings now instead of waiting for the schedule."""

    permission_required = (
        "structuretimers.basic_access",
        "structuretimers.recon_coordinator",
    )

    def post(self, request):
        standings.sync_all()
        messages.info(request, "Standings refreshed from alliance contacts.")
        return redirect(reverse("structuretimers:timer_list") + "?tab=standings")


@login_required
@permission_required(
    ("structuretimers.basic_access", "structuretimers.recon_coordinator"),
    raise_exception=True,
)
@token_required(scopes=[standings.CONTACTS_SCOPE])
def add_standings_source(request, token):
    """Use a character's alliance contacts as the automatic standings."""
    character = requests.get(
        f"{owners.ESI_URL}/characters/{token.character_id}/",
        headers={"User-Agent": owners.USER_AGENT},
        timeout=(5, 30),
    ).json()
    alliance_id = character.get("alliance_id")
    if not alliance_id:
        messages.error(request, f"{token.character_name} is not in an alliance.")
        return redirect(reverse("structuretimers:timer_list") + "?tab=standings")
    StandingsSource.objects.all().delete()
    source = StandingsSource.objects.create(
        token=token,
        alliance_id=alliance_id,
        alliance_name=EveEntity.objects.resolve_name(alliance_id),
        added_by=request.user,
    )
    try:
        count = standings.sync_source(source)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        messages.error(request, f"Could not read alliance contacts: {ex}")
    else:
        messages.info(
            request, f"Loaded {count} standings for {source.alliance_name}."
        )
    return redirect(reverse("structuretimers:timer_list") + "?tab=standings")


class Select2OwnersView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Known owner corporations for the Owner field, plus an ESI lookup."""

    permission_required = "structuretimers.basic_access"

    def get(self, request):
        return JsonResponse({"results": owners.autocomplete(request.GET.get("term"))})


class Select2DatabaseEntriesView(
    LoginRequiredMixin, PermissionRequiredMixin, JSONResponseMixin, ListView
):
    """Structures in the Database for the timer form's select2 picker."""

    permission_required = (
        "structuretimers.basic_access",
        "structuretimers.recon_member",
    )

    def get_queryset(self):
        term = (self.request.GET.get("term") or "").strip()
        if len(term) < 2:
            return Timer.objects.none()
        return (
            Timer.objects.visible_to_user(self.request.user)
            .filter(timer_type=Timer.Type.PRELIMINARY)
            .filter(
                Q(structure_name__icontains=term)
                | Q(eve_solar_system__name__istartswith=term)
                | Q(owner_name__icontains=term)
            )
            .select_related(
                "eve_solar_system", "structure_type", "owner_corporation__alliance"
            )
            .order_by("eve_solar_system__name", "structure_name")[:30]
        )

    def get_context_data(self, **kwargs):
        term = (self.request.GET.get("term") or "").strip()
        structures = (
            structures_bridge.picker_results(self.request.user, term)
            if len(term) >= 2
            else []
        )
        return {
            "results": structures
            + [
                {
                    "id": entry.pk,
                    "text": entry.structure_display_name,
                    "solar_system": {
                        "id": entry.eve_solar_system_id,
                        "text": entry.eve_solar_system.name,
                    },
                    "structure_type": (
                        {
                            "id": entry.structure_type_id,
                            "text": entry.structure_type.name,
                        }
                        if entry.structure_type
                        else None
                    ),
                    "structure_name": entry.structure_name,
                    "owner_name": entry.owner_name or "",
                    "owner": (
                        {
                            "id": str(entry.owner_corporation_id),
                            "text": entry.owner_corporation.display_name,
                        }
                        if entry.owner_corporation_id
                        else (
                            {
                                "id": owners.LOOKUP_PREFIX + entry.owner_name,
                                "text": entry.owner_name,
                            }
                            if entry.owner_name
                            else None
                        )
                    ),
                    "location_details": entry.location_details,
                    "objective": entry.objective,
                    "reinforcement_time": (
                        entry.reinforcement_time.strftime("%H:%M")
                        if entry.reinforcement_time
                        else ""
                    ),
                }
                for entry in self.object_list
            ]
        }

    def render_to_response(self, context, **response_kwargs):
        return self.render_to_json_response(context, **response_kwargs)


class Select2StructureTypesView(JSONResponseMixin, ListView):
    """Dynamically generated list of types for select2 widget."""

    def get_queryset(self):
        term = self.request.GET.get("term")
        if not term:
            return EveType.objects.none()

        qs = supported_eve_types()
        return qs.filter(name__icontains=term)

    def get_context_data(self, **kwargs):
        if self.object_list:
            results = [
                {"id": row["id"], "text": row["name"]}
                for row in self.object_list.values("id", "name").order_by("name")
            ]
            results = sorted(results, key=lambda d: d["text"])
        else:
            results = None
        return {"results": results}

    def render_to_response(self, context, **response_kwargs):
        return self.render_to_json_response(context, **response_kwargs)
