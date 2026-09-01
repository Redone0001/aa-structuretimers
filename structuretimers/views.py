"""Views."""

# pylint: disable=too-many-ancestors,missing-function-docstring, missing-class-docstring

import math
from copy import deepcopy
from typing import Iterable

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.timezone import now
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)
from eveuniverse.models import EveSolarSystem, EveType

from allianceauth.eveonline.evelinks import dotlan
from allianceauth.services.hooks import get_extension_logger
from app_utils.views import (
    JSONResponseMixin,
    fontawesome_link_button_html,
    link_html,
    yesno_str,
)

from structuretimers import __title__
from structuretimers.app_settings import (
    STRUCTURETIMERS_DEFAULT_PAGE_LENGTH,
    STRUCTURETIMERS_PAGING_ENABLED,
)
from structuretimers.forms import FastTimerForm, TimerForm
from structuretimers.models import DistancesFromStaging, StagingSystem, Timer
from structuretimers.selectors import supported_eve_types

logger = get_extension_logger(__name__)
DATETIME_FORMAT = "%Y-%m-%d %H:%M"
MAX_HOURS_PASSED = 2


def bootstrap5_label_html(text: str, label: str = "secondary") -> str:
    """Return HTML for a Bootstrap 5 label."""
    return format_html('<span class="badge text-bg-{}">{}</span>', label, text)


class TimerListView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """View for showing a list of timer."""

    template_name = "structuretimers/timer_list.html"
    permission_required = "structuretimers.basic_access"

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
                "tab": self.request.GET.get("tab", "current"),
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
            "eve_corporation",
            "eve_alliance",
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
                    "structure_type_name": timer.structure_type.name,
                    "owner_name": owner_name,
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
        if timer.owner_name:
            owner_name = timer.owner_name
            owner = owner_name
        else:
            owner = "-"
            owner_name = ""

        structure_name = timer.structure_name if timer.structure_name else "-"
        name = format_html("{}<br>{}", structure_name, owner)
        return owner_name, name

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
            structure_type_name = "(unknown)"

        context = {
            "type_icon_url": structure_type_icon_url,
            "type_name": structure_type_name,
            "timer_name": timer.get_timer_type_display(),
            "timer_style": timer.label_type_for_timer_type(),
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
            distance_text = format_html("{}<br>{}", light_years_text, jumps_text)
        return distances, distance_text

    def _get_data_actions(self, timer: Timer):
        actions = ""
        if timer.details_image_url or timer.details_notes:
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
                '<button type="button" id="timerboardBtnDetails" '
                'class="btn btn-{}" title="{}"'
                "{}"
                'data-timerpk="{}"{}>'
                '<i class="fas fa-search-plus"></i>'
                "</button>",
                button_type,
                title,
                mark_safe(data_toggle),
                timer.pk,
                mark_safe(disabled_html),
            )
            + "&nbsp;"
        )
        if timer.user_can_edit(self.request.user):
            actions += (
                fontawesome_link_button_html(
                    reverse("structuretimers:delete", args=(timer.pk,)),
                    "far fa-trash-alt",
                    "danger",
                    "Delete this timer",
                )
                + "&nbsp;"
                + fontawesome_link_button_html(
                    reverse("structuretimers:edit", args=(timer.pk,)),
                    "far fa-edit",
                    "warning",
                    "Edit this timer",
                )
            )
        if self.request.user.has_perm("structuretimers.create_timer"):
            actions += "&nbsp;" + fontawesome_link_button_html(
                reverse("structuretimers:copy", args=(timer.pk,)),
                "far fa-copy",
                "success",
                "Copy this timer",
            )
        return actions


class TimerDetailDataView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """View for showing details of a timer."""

    permission_required = "structuretimers.basic_access"
    model = Timer

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.visible_to_user(self.request.user).select_related(
            "structure_type", "eve_solar_system"
        )


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


# FIXME: User without permission can still post to edit timers directly


class EditTimerMixin:
    permission_required = "structuretimers.basic_access"

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        if response.status_code == 200:
            can_view = (
                Timer.objects.filter(pk=self.object.pk)
                .visible_to_user(self.request.user)
                .exists()
            )
            can_edit = self.object.user_can_edit(self.request.user)
            if not can_view or not can_edit:
                raise PermissionDenied()

        return response


class EditTimerView(EditTimerMixin, TimerManagementView, AddUpdateMixin, UpdateView):
    template_name_suffix = "_update_form"

    def form_valid(self, form):
        result = super().form_valid(form)
        self.send_success_message(_("Updated"))
        return result


class CopyTimerView(CreateTimerView):
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        old_obj = get_object_or_404(Timer, pk=self.kwargs["pk"])
        new_obj = deepcopy(old_obj)
        new_obj.pk = None
        new_obj.date = None
        kwargs["instance"] = deepcopy(new_obj)
        return kwargs


# FIXME: User without permission can still post to delete timers directly


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
