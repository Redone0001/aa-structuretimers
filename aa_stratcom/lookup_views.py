"""Owner standings and form autocomplete endpoints."""

# Django generic views provide the inherited HTTP dispatch and form behavior.
# pylint: disable=too-many-ancestors

import requests

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import ListView
from esi.decorators import token_required
from eveuniverse.models import EveEntity, EveSolarSystem, EveType

from app_utils.views import JSONResponseMixin

from . import destruction, information, owners, standings, structures_bridge
from .models import Organization, StandingsSource, Structure, Timer
from .selectors import supported_eve_types


class ReconActionView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """POST-only recon mutations; enforce visibility and edit permission."""

    permission_required = (
        "aa_stratcom.basic_access",
        "aa_stratcom.recon_member",
    )

    @transaction.atomic
    def post(self, request, pk, action):
        """Archive or restore a permitted structure without deleting its history."""
        structure = get_object_or_404(
            Structure.objects.filter(
                pk__in=Structure.objects.visible_to_user(request.user).values("pk")
            ).select_for_update(),
            pk=pk,
        )
        if not structure.user_can_edit(request.user):
            raise PermissionDenied()
        if action == "refresh":
            destruction.restore_structure(structure, request.user)
            return JsonResponse(
                {"last_updated_at": structure.last_updated_at.isoformat()}
            )
        if action == "destroy":
            destruction.mark_destroyed(structure, request.user)
            return JsonResponse({"destroyed": True, "deleted": False})
        return JsonResponse({"error": "Unknown action"}, status=400)


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
        "aa_stratcom.basic_access",
        "aa_stratcom.recon_coordinator",
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
        return redirect(reverse("aa_stratcom:timer_list") + "?tab=standings")


class StandingsSyncView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Refresh automatic standings now instead of waiting for the schedule."""

    permission_required = (
        "aa_stratcom.basic_access",
        "aa_stratcom.recon_coordinator",
    )

    def post(self, request):
        """Apply the requested standings update and return to the standings tab."""
        standings.sync_all()
        errors = list(
            StandingsSource.objects.exclude(last_error="").values_list(
                "last_error", flat=True
            )
        )
        if errors:
            messages.error(request, "Could not refresh standings: " + "; ".join(errors))
        elif StandingsSource.objects.exists():
            messages.info(request, "Standings refreshed from alliance contacts.")
        else:
            messages.warning(
                request,
                "No standings source configured. "
                "Add a character explicitly to enable alliance standings.",
            )
        return redirect(reverse("aa_stratcom:timer_list") + "?tab=standings")


@login_required
@permission_required(
    ("aa_stratcom.basic_access", "aa_stratcom.recon_coordinator"),
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
        return redirect(reverse("aa_stratcom:timer_list") + "?tab=standings")
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
        source.last_error = str(ex)[:254]
        source.save(update_fields=["last_error"])
        messages.error(request, f"Could not read alliance contacts: {ex}")
    else:
        messages.info(request, f"Loaded {count} standings for {source.alliance_name}.")
    return redirect(reverse("aa_stratcom:timer_list") + "?tab=standings")


class Select2OwnersView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Known owner corporations for the Owner field, plus an ESI lookup."""

    permission_required = "aa_stratcom.basic_access"

    def get(self, request):
        """Return matching owner corporations and lookup options."""
        return JsonResponse({"results": owners.autocomplete(request.GET.get("term"))})


class Select2DatabaseEntriesView(
    LoginRequiredMixin, PermissionRequiredMixin, JSONResponseMixin, ListView
):
    """Structures in the Database for the timer form's select2 picker."""

    permission_required = (
        "aa_stratcom.basic_access",
        "aa_stratcom.recon_member",
    )

    def get_queryset(self):
        """Return records matching the current autocomplete term."""
        term = (self.request.GET.get("term") or "").strip()
        if len(term) < 2:
            return Timer.objects.none()
        return (
            Structure.objects.visible_to_user(self.request.user)
            .filter(destroyed_at__isnull=True)
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
