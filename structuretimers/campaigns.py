"""Recon campaign coordination and reservation-scoped recon editing."""

from collections import defaultdict

from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.db.models.functions import Lower
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.views import View
from eveuniverse.models import EveRegion, EveSolarSystem, EveStargate

from .map_layouts import get_region_layout
from .forms import ReconForm
from .models import ReconCampaign, ReconCampaignSystem, Timer


class CampaignForm(forms.Form):
    name = forms.CharField(max_length=200, label=_("Campaign name"))
    systems = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        label=_("Solar systems"),
        help_text=_("Enter system names separated by commas or new lines."),
    )
    regions = forms.ModelMultipleChoiceField(
        queryset=EveRegion.objects.order_by("name"),
        required=False,
        label=_("Regions"),
        help_text=_(
            "All systems in selected regions will be imported. Hold Ctrl or Command to select multiple regions."
        ),
    )

    def clean(self):
        data = super().clean()
        names = {
            name.strip().lower()
            for name in data.get("systems", "").replace(",", "\n").splitlines()
            if name.strip()
        }
        found = {
            system.name.lower(): system
            for system in EveSolarSystem.objects.annotate(
                lower_name=Lower("name")
            ).filter(lower_name__in=names)
        }
        systems = {system.pk: system for system in found.values()}
        for name in sorted(names - found.keys()):
            self.add_error(
                "systems", _("Unknown solar system: %(name)s") % {"name": name}
            )
        data["resolved_systems"] = systems
        if not systems and not data.get("regions"):
            raise forms.ValidationError(_("Add at least one system or region."))
        return data


class CampaignAccess(LoginRequiredMixin, PermissionRequiredMixin):
    permission_required = "structuretimers.basic_access"


class CampaignListView(CampaignAccess, View):
    def get(self, request):
        return redirect(reverse("structuretimers:timer_list") + "?tab=recon-campaigns")


class CampaignCreateView(CampaignAccess, View):
    permission_required = (
        "structuretimers.basic_access",
        "structuretimers.recon_coordinator",
    )

    def get(self, request):
        return self.render_form(request, CampaignForm())

    def render_form(self, request, form):
        return render(
            request,
            "structuretimers/campaign_form.html",
            {"form": form, "title": _("Create recon campaign")},
        )

    def post(self, request):
        form = CampaignForm(request.POST)
        if form.is_valid():
            systems = form.cleaned_data["resolved_systems"]
            region_ids = list(form.cleaned_data["regions"].values_list("pk", flat=True))
            with transaction.atomic():
                campaign = ReconCampaign.objects.create(
                    name=form.cleaned_data["name"],
                    created_by=request.user,
                    region_ids=region_ids,
                    import_status="pending" if region_ids else "ready",
                    gates_status="pending",
                )
                ReconCampaignSystem.objects.bulk_create(
                    [
                        ReconCampaignSystem(campaign=campaign, solar_system=system)
                        for system in systems.values()
                    ]
                )
                from .campaign_jobs import enqueue_campaign_job

                transaction.on_commit(
                    lambda: enqueue_campaign_job(
                        campaign.pk, "systems" if region_ids else "gates"
                    )
                )
            return redirect(campaign)
        return self.render_form(request, form)


def can_work(user, entry):
    return (
        entry.campaign.import_status == "ready"
        and not entry.completed_at
        and (
            entry.reserved_by_id == user.pk
            or user.has_perm("structuretimers.recon_coordinator")
        )
    )


def campaign_map_data(entries):
    """Use the same visibility-filtered recon as the list, without timer details."""
    regions = {}
    system_regions = {}
    for entry in entries:
        system = entry.solar_system
        region = system.eve_constellation.eve_region
        group = regions.setdefault(
            region.pk,
            {"id": region.pk, "name": region.name, "systems": [], "links": []},
        )
        system_regions[system.pk] = region.pk
        group["systems"].append(
            {
                "id": system.pk,
                "entryId": entry.pk,
                "name": system.name,
                "x": system.position_x,
                "z": system.position_z,
                "count": (
                    entry.timer_count
                    if hasattr(entry, "timer_count")
                    else len(entry.timers)
                ),
                "status": (
                    "completed"
                    if entry.completed_at
                    else "reserved" if entry.reserved_by_id else "available"
                ),
            }
        )
    links = set()
    for source, target in EveStargate.objects.filter(
        eve_solar_system_id__in=system_regions,
        destination_eve_solar_system_id__in=system_regions,
    ).values_list("eve_solar_system_id", "destination_eve_solar_system_id"):
        if source != target and system_regions[source] == system_regions[target]:
            links.add(tuple(sorted((source, target))))
    for source, target in sorted(links):
        regions[system_regions[source]]["links"].append([source, target])
    for region in regions.values():
        region["layout"] = get_region_layout(region["name"])
    return sorted(regions.values(), key=lambda region: region["name"])


class CampaignDetailView(CampaignAccess, View):
    def get(self, request, pk):
        campaign = get_object_or_404(ReconCampaign, pk=pk)
        entries = list(
            campaign.systems.select_related(
                "solar_system__eve_constellation__eve_region",
                "reserved_by",
                "completed_by",
            ).order_by(
                "solar_system__eve_constellation__name",
                "solar_system__eve_constellation_id",
                "solar_system__name",
            )
        )
        timers = defaultdict(list)
        for timer in (
            Timer.objects.visible_to_user(request.user)
            .filter(
                timer_type=Timer.Type.PRELIMINARY,
                eve_solar_system_id__in=[e.solar_system_id for e in entries],
            )
            .select_related("structure_type")
        ):
            timers[timer.eve_solar_system_id].append(timer)
        for entry in entries:
            entry.campaign = campaign
            entry.timers = timers[entry.solar_system_id]
            entry.can_work = can_work(request.user, entry)
        return render(
            request,
            "structuretimers/campaign_detail.html",
            {
                "campaign": campaign,
                "entries": entries,
                "completed": sum(bool(e.completed_at) for e in entries),
                "title": campaign.name,
            },
        )

    def post(self, request, pk):
        action = request.POST.get("action")
        if action in {"load_gates", "retry_import"}:
            if not request.user.has_perm("structuretimers.recon_coordinator"):
                raise PermissionDenied()
            from .campaign_jobs import enqueue_campaign_job

            with transaction.atomic():
                campaign = get_object_or_404(
                    ReconCampaign.objects.select_for_update(), pk=pk
                )
                if action == "retry_import" and campaign.import_status == "failed":
                    campaign.import_status = "pending"
                    campaign.gates_status = "pending"
                    campaign.save(update_fields=["import_status", "gates_status"])
                    transaction.on_commit(lambda: enqueue_campaign_job(pk, "systems"))
                elif (
                    action == "load_gates"
                    and campaign.import_status == "ready"
                    and campaign.gates_status != "pending"
                ):
                    campaign.gates_status = "pending"
                    campaign.save(update_fields=["gates_status"])
                    transaction.on_commit(
                        lambda: enqueue_campaign_job(pk, "gates", refresh=True)
                    )
            return redirect(campaign)
        if action not in {"reserve", "release", "complete", "reopen"}:
            return HttpResponseBadRequest("Unknown action")
        with transaction.atomic():
            campaign = get_object_or_404(
                ReconCampaign.objects.select_for_update(), pk=pk
            )
            if campaign.import_status != "ready":
                return HttpResponseBadRequest(
                    "Campaign systems are still being imported"
                )
            ids = request.POST.getlist("systems")
            entries = (
                list(campaign.systems.select_for_update().filter(pk__in=ids))
                if all(i.isdigit() for i in ids)
                else []
            )
            if not entries or len(entries) != len(set(ids)):
                return HttpResponseBadRequest("Select systems from this campaign")
            coordinator = request.user.has_perm("structuretimers.recon_coordinator")
            for entry in entries:
                entry.campaign = campaign
                if action == "reserve":
                    if entry.completed_at or entry.reserved_by_id not in {
                        None,
                        request.user.pk,
                    }:
                        messages.error(
                            request,
                            _(
                                "One of these systems is already reserved or complete. Refresh and try again."
                            ),
                        )
                        return redirect(campaign)
                elif action == "reopen":
                    if not coordinator:
                        raise PermissionDenied()
                elif not can_work(request.user, entry):
                    raise PermissionDenied()
            for entry in entries:
                entry.campaign = campaign
                if action == "reserve":
                    entry.reserved_by = request.user
                elif action == "release":
                    entry.reserved_by = None
                elif action == "complete":
                    entry.completed_at, entry.completed_by = now(), request.user
                else:
                    entry.completed_at, entry.completed_by = None, None
                entry.save()
            campaign.finished_at = (
                None
                if campaign.systems.filter(completed_at__isnull=True).exists()
                else (campaign.finished_at or now())
            )
            campaign.save(update_fields=["finished_at"])
        return redirect(campaign)


class CampaignStatusView(CampaignAccess, View):
    def get(self, request, pk):
        campaign = get_object_or_404(ReconCampaign, pk=pk)
        response = JsonResponse(
            {
                "import_status": campaign.import_status,
                "gates_status": campaign.gates_status,
            }
        )
        response["Cache-Control"] = "private, no-store"
        return response


class CampaignMapDataView(CampaignAccess, View):
    def get(self, request, pk):
        campaign = get_object_or_404(ReconCampaign, pk=pk)
        entries = list(
            campaign.systems.select_related(
                "solar_system__eve_constellation__eve_region"
            )
        )
        counts = dict(
            Timer.objects.visible_to_user(request.user)
            .filter(
                timer_type=Timer.Type.PRELIMINARY,
                eve_solar_system_id__in=[entry.solar_system_id for entry in entries],
            )
            .order_by()
            .values("eve_solar_system_id")
            .annotate(count=Count("pk"))
            .values_list("eve_solar_system_id", "count")
        )
        for entry in entries:
            entry.timer_count = counts.get(entry.solar_system_id, 0)
        response = JsonResponse(campaign_map_data(entries), safe=False)
        response["Cache-Control"] = "private, no-store"
        return response


class CampaignReconView(CampaignAccess, View):
    def handle(self, request, pk, entry_pk, timer_pk=None):
        entry = get_object_or_404(
            ReconCampaignSystem.objects.select_related("campaign", "solar_system"),
            pk=entry_pk,
            campaign_id=pk,
        )
        if not can_work(request.user, entry):
            raise PermissionDenied()
        timer = (
            get_object_or_404(
                Timer.objects.visible_to_user(request.user),
                pk=timer_pk,
                eve_solar_system_id=entry.solar_system_id,
                timer_type=Timer.Type.PRELIMINARY,
            )
            if timer_pk
            else None
        )
        if request.method == "POST" and request.POST.get("action") in {
            "refresh",
            "destroy",
        }:
            if timer is None:
                return HttpResponseBadRequest("Timer required")
            if request.POST["action"] == "destroy":
                timer.delete()
            else:
                Timer.objects.filter(pk=timer.pk).update(last_updated_at=now())
            return redirect(entry.campaign)
        form = ReconForm(
            request.POST if request.method == "POST" else None,
            instance=timer,
            user=request.user,
        )
        form.fields["eve_solar_system_2"].disabled = True
        form.initial["eve_solar_system_2"] = str(entry.solar_system_id)
        form.fields["eve_solar_system_2"].widget.choices = [
            (str(entry.solar_system_id), entry.solar_system.name)
        ]
        if request.method == "POST" and form.is_valid():
            form.save()
            return redirect(entry.campaign)
        return render(
            request,
            "structuretimers/campaign_recon_form.html",
            {
                "form": form,
                "title": _("Campaign recon"),
                "cancel_url": entry.campaign.get_absolute_url(),
            },
        )

    def get(self, request, **kwargs):
        return self.handle(request, **kwargs)

    def post(self, request, pk, **kwargs):
        with transaction.atomic():
            get_object_or_404(ReconCampaign.objects.select_for_update(), pk=pk)
            return self.handle(request, pk=pk, **kwargs)
