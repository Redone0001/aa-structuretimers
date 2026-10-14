"""Owner standings and form autocomplete endpoints."""

# Django generic views provide the inherited HTTP dispatch and form behavior.
# pylint: disable=too-many-ancestors

import requests

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import ListView
from esi.decorators import token_required
from eveuniverse.models import EveEntity, EveSolarSystem, EveType

from app_utils.views import JSONResponseMixin

from . import information, owners, standings, structures_bridge
from .forms import TimerForm
from .models import Organization, StandingsSource, Structure, Timer
from .selectors import supported_eve_types


class Select2SolarSystemsView(JSONResponseMixin, ListView):
    """Dynamically generated list of solar systems for select2 widget."""

    model = EveSolarSystem

    def get_queryset(self):
        """Return records matching the current autocomplete term."""
        qs = super().get_queryset()
        term = self.request.GET.get("term")
        if not term:
            return qs.none()
        return qs.filter(name__istartswith=term)

    def get_context_data(self, **kwargs):
        """Serialize matching records for the select2 widget."""
        if self.object_list:
            solar_systems = self.object_list.values("id", "name")
            results = [{"id": row["id"], "text": row["name"]} for row in solar_systems]
            results = sorted(results, key=lambda d: d["text"])
        else:
            results = None
        return {"results": results}

    def render_to_response(self, context, **response_kwargs):
        """Return the select2 context as JSON."""
        return self.render_to_json_response(context, **response_kwargs)


class StandingSetView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Recon coordinators override the standing of a corporation or alliance."""

    permission_required = (
        "structuretimers.basic_access",
        "structuretimers.recon_coordinator",
    )

    def post(self, request, pk):
        """Apply the requested standings update and return to the standings tab."""
        organization = get_object_or_404(Organization, pk=pk)
        value = request.POST.get("standing", "")
        if value == "":
            organization.standing_override = None
        elif value.lstrip("-").isdigit() and int(value) in standings.STANDING_VALUES:
            organization.standing_override = int(value)
        else:
            return JsonResponse({"error": "Unknown standing"}, status=400)
        organization.save(update_fields=["standing_override", "updated_at"])
        standings.refresh_objectives()
        return redirect(reverse("structuretimers:timer_list") + "?tab=standings")


class StandingsSyncView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Refresh automatic standings now instead of waiting for the schedule."""

    permission_required = (
        "structuretimers.basic_access",
        "structuretimers.recon_coordinator",
    )

    def post(self, request):
        """Apply the requested standings update and return to the standings tab."""
        standings.sync_all()
        if StandingsSource.objects.exists():
            messages.info(request, "Standings refreshed from alliance contacts.")
        else:
            messages.warning(
                request,
                "No character in Auth has alliance-contacts access yet. "
                "Add one to get automatic standings.",
            )
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
        standings.refresh_objectives()
    except Exception as ex:  # pylint: disable=broad-exception-caught
        messages.error(request, f"Could not read alliance contacts: {ex}")
    else:
        messages.info(request, f"Loaded {count} standings for {source.alliance_name}.")
    return redirect(reverse("structuretimers:timer_list") + "?tab=standings")


class Select2OwnersView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Known owner corporations for the Owner field, plus an ESI lookup."""

    permission_required = "structuretimers.basic_access"

    def get(self, request):
        """Return matching owner corporations and lookup options."""
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
        """Return records matching the current autocomplete term."""
        term = (self.request.GET.get("term") or "").strip()
        if len(term) < 2:
            return Timer.objects.none()
        return (
            Structure.objects.visible_to_user(self.request.user)
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
        """Serialize matching records for the select2 widget."""
        term = (self.request.GET.get("term") or "").strip()
        structures = (
            structures_bridge.picker_results(self.request.user, term)
            if len(term) >= 2
            else []
        )
        return {
            "results": structures
            + [information.picker_entry(entry) for entry in self.object_list]
        }

    def render_to_response(self, context, **response_kwargs):
        """Return the select2 context as JSON."""
        return self.render_to_json_response(context, **response_kwargs)


class Select2StructureTypesView(JSONResponseMixin, ListView):
    """Dynamically generated list of types for select2 widget."""

    def get_queryset(self):
        """Return records matching the current autocomplete term."""
        term = self.request.GET.get("term")
        if not term:
            return EveType.objects.none()

        qs = supported_eve_types()
        return qs.filter(name__icontains=term)

    def get_context_data(self, **kwargs):
        """Serialize matching records for the select2 widget."""
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
        """Return the select2 context as JSON."""
        return self.render_to_json_response(context, **response_kwargs)


class AssignTimerView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Update only the assignee, enforcing the usual timer edit permissions."""

    permission_required = "structuretimers.basic_access"

    def get(self, request, pk):
        """Return matching main characters for an editable timer."""
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
        matches = list(users[offset : offset + 21]) if query else []
        return JsonResponse(
            {
                "results": [
                    {"id": user.pk, "text": user.profile.main_character.character_name}
                    for user in matches[:20]
                ],
                "pagination": {"more": len(matches) > 20},
            }
        )

    def post(self, request, pk):
        """Validate and save the assignee without modifying other timer fields."""
        timer = get_object_or_404(Timer.objects.visible_to_user(request.user), pk=pk)
        if not timer.user_can_edit(request.user):
            raise PermissionDenied()
        if "assigned_to" not in request.POST:
            return JsonResponse(
                {"error": _("Choose an assignee or Unassigned.")}, status=400
            )
        try:
            assignee = TimerForm.base_fields["assigned_to"].clean(
                request.POST["assigned_to"]
            )
        except ValidationError as ex:
            return JsonResponse({"error": " ".join(ex.messages)}, status=400)
        timer.assigned_to = assignee
        timer.save(update_fields=["assigned_to", "last_updated_at"])
        return JsonResponse(
            {
                "assigned_to": timer.assigned_to_id,
                "assigned_character_name": timer.assigned_character_name,
            }
        )
