"""Role-checked operation links, timer membership edits and roster lookups."""

# Generic Django views supply the inherited HTTP dispatch behavior.
# pylint: disable=too-many-ancestors

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.db.models import F, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils.decorators import method_decorator
from django.utils.timezone import now
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from eveuniverse.models import EveSolarSystem

from .models import Operation, Timer
from .operation_roster import user_label


def operation_prefetch(user):
    """Prefetch only operation names this user may open."""
    return Prefetch(
        "operations", queryset=Operation.visible_to(user), to_attr="assigned_operations"
    )


def assignment_html(timer, user, editable=False):
    """Hide private operations and replace the legacy user assignment display."""
    if not isinstance(timer, Timer):
        return ""
    operations = getattr(timer, "assigned_operations", None)
    if operations is None:
        operations = list(Operation.visible_to(user).filter(timers=timer))
    return render_to_string(
        "structuretimers/partials/timer_assignment.html",
        {
            "timer": timer,
            "assigned_operations": operations,
            "can_assign_operation": editable
            and user.has_perm("structuretimers.op_coordinator"),
        },
    )


@method_decorator(never_cache, name="dispatch")
class AssignTimerView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Only coordinators can edit a visible timer's operation memberships."""

    permission_required = (
        "structuretimers.basic_access",
        "structuretimers.op_coordinator",
    )

    def get(self, request, pk):
        """Search operation names for the timer's multiple-operation picker."""
        get_object_or_404(Timer.objects.visible_to_user(request.user), pk=pk)
        query = request.GET.get("term", "").strip()[:200]
        return JsonResponse(
            {
                "results": [
                    {"id": op.pk, "text": op.name}
                    for op in Operation.visible_to(request.user).filter(
                        name__icontains=query
                    )[:50]
                ]
            }
        )

    @transaction.atomic
    def post(self, request, pk):
        """Change operation membership without changing timer or legacy user data."""
        get_object_or_404(Timer.objects.visible_to_user(request.user), pk=pk)
        if "operation_ids" not in request.POST:
            return JsonResponse(
                {"error": "Choose operations or clear the selection."}, status=400
            )
        try:
            ids = {
                int(value) for value in request.POST.getlist("operation_ids") if value
            }
        except (TypeError, ValueError):
            return JsonResponse({"error": "Choose valid operations."}, status=400)
        if len(ids) > 100 or any(pk <= 0 for pk in ids):
            return JsonResponse(
                {"error": "Choose at most 100 valid operations."}, status=400
            )
        if ids - set(
            Operation.visible_to(request.user)
            .filter(pk__in=ids)
            .values_list("pk", flat=True)
        ):
            return JsonResponse({"error": "An operation is unavailable."}, status=400)
        # Operation editors lock the operation first too. Lock in stable order.
        old = set(Operation.objects.filter(timers=pk).values_list("pk", flat=True))
        list(
            Operation.objects.select_for_update()
            .filter(pk__in=ids | old)
            .order_by("pk")
        )
        timer = get_object_or_404(
            Timer.objects.visible_to_user(request.user).select_for_update(), pk=pk
        )
        old = set(timer.operations.values_list("pk", flat=True))
        timer.operations.set(ids)
        Operation.objects.filter(pk__in=old ^ ids).update(
            revision=F("revision") + 1, updated_at=now(), updated_by=request.user
        )
        return JsonResponse(
            {
                "operation_ids": sorted(ids),
                "assignment": assignment_html(timer, request.user, editable=True),
            }
        )


@login_required
@permission_required(
    ("structuretimers.basic_access", "structuretimers.op_coordinator"),
    raise_exception=True,
)
@require_GET
@never_cache
def roster_lookup(request, kind):
    """Search active AA accounts and known systems; never create global records."""
    query = request.GET.get("term", "").strip()[:200]
    if kind == "users":
        objects = (
            get_user_model()
            .objects.filter(is_active=True)
            .filter(
                Q(username__icontains=query)
                | Q(profile__main_character__character_name__icontains=query)
            )
            .select_related("profile__main_character")
            .order_by("username", "pk")
        )
        results = [{"id": user.pk, "text": user_label(user)} for user in objects[:50]]
    elif kind == "systems":
        results = [
            {"id": system.name, "text": system.name}
            for system in EveSolarSystem.objects.filter(name__icontains=query).order_by(
                "name"
            )[:50]
        ]
    else:
        return JsonResponse({"error": "Unknown lookup."}, status=404)
    return JsonResponse({"results": results})
