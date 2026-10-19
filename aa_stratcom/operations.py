"""Operation selection, role-restricted boards, and lightweight live observations."""

# Generic Django views supply the inherited dispatch and template behavior.
# pylint: disable=too-many-ancestors

import hashlib
import json

from django import forms
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.utils.decorators import method_decorator
from django.utils.timezone import now
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .battle_map import observation_payload
from .models import Operation, Timer
from .operation_access import (
    current_timers,
    get_operation,
    operation_timers,
    require_operation_access,
)
from .operation_assignments import assignment_html
from .operation_roster import (
    clean_roster,
    roster_payload,
    save_roster,
    template_payload,
)
from .views import TimerListDataView, TimerListView, fit_buttons_html


class OperationForm(forms.ModelForm):
    """Only visible Current timers can be newly selected for an operation."""

    timers = forms.ModelMultipleChoiceField(
        queryset=Timer.objects.none(), widget=forms.MultipleHiddenInput
    )
    roster = forms.JSONField(required=False, widget=forms.HiddenInput)
    version = forms.IntegerField(widget=forms.HiddenInput, min_value=0)

    class Meta:
        model = Operation
        fields = ("name", "description", "visibility", "timers")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = current_timers(user)
        if self.instance.pk:
            allowed = (
                Timer.objects.visible_to_user(user)
                .filter(Q(pk__in=allowed.values("pk")) | Q(operations=self.instance))
                .distinct()
            )
        self.fields["timers"].queryset = allowed
        if self.instance.pk:
            self.initial["timers"] = list(
                operation_timers(self.instance, user).values_list("pk", flat=True)
            )
        self.initial["roster"] = roster_payload(
            self.instance if self.instance.pk else None
        )
        self.initial["version"] = self.instance.revision if self.instance.pk else 0

    def clean_roster(self):
        """Validate only operation-local planning labels and existing AA accounts."""
        return clean_roster(self.cleaned_data.get("roster"), self.instance)


def board_context(context, user, operation=None, form=None):
    """Render operation timers through the existing Current board and map."""
    editor = form is not None
    selected = []
    if editor:
        selected = (
            form.data.getlist("timers")
            if form.is_bound
            else (
                list(operation_timers(operation, user).values_list("pk", flat=True))
                if operation
                else []
            )
        )
    context.update(
        {
            "operation_page": True,
            "operation_roster": (
                (form.cleaned_data.get("roster") or roster_payload(operation))
                if editor and form.is_bound
                else (form.initial["roster"] if editor else roster_payload(operation))
            ),
            "aa_stratcom_operation_role_templates": (
                template_payload() if editor else []
            ),
            "operation": operation,
            "operation_form": form,
            "operation_editor": editor,
            "tab": "ops",
            "can_view_ops": True,
            "operation_selected_ids": [int(pk) for pk in selected if str(pk).isdigit()],
            "operation_data_url": reverse(
                (
                    "aa_stratcom:operation_picker"
                    if editor
                    else "aa_stratcom:operation_data"
                ),
                kwargs={"pk": operation.pk} if operation else {},
            ),
        }
    )
    return context


@method_decorator(never_cache, name="dispatch")
class OperationDetailView(TimerListView):
    """The Current layout, restricted to the operation's visible selected timers."""

    def get_context_data(self, **kwargs):
        """Add role-checked operation metadata to the shared timerboard layout."""
        operation = get_operation(self.request.user, self.kwargs["pk"])
        context = super().get_context_data(**kwargs)
        context["title"] = operation.name
        return board_context(context, self.request.user, operation)


@method_decorator(never_cache, name="dispatch")
class OperationEditorView(TimerListView):
    """Coordinators create and edit selections without changing the timers."""

    permission_required = (
        "aa_stratcom.basic_access",
        "aa_stratcom.op_coordinator",
    )

    def get_context_data(self, **kwargs):
        operation = (
            get_operation(self.request.user, self.kwargs["pk"])
            if self.kwargs.get("pk")
            else None
        )
        form = kwargs.pop("operation_form", None)
        if form is None:
            form = OperationForm(user=self.request.user, instance=operation)
        context = super().get_context_data(**kwargs)
        context["title"] = "Edit operation" if operation else "Create operation"
        return board_context(context, self.request.user, operation, form)

    @transaction.atomic
    def post(self, request, **kwargs):
        """Lock membership edits and reject changes from stale editor pages."""
        operation = (
            get_operation(request.user, kwargs["pk"]) if kwargs.get("pk") else None
        )
        if operation:
            operation = Operation.objects.select_for_update().get(pk=operation.pk)
        version = operation.revision if operation else 0
        form = OperationForm(request.POST, user=request.user, instance=operation)
        valid = form.is_valid()
        if valid and form.cleaned_data["version"] != version:
            form.add_error(
                None,
                "This operation changed while you were editing it. Reload before saving.",
            )
            return self.render_to_response(
                self.get_context_data(operation_form=form), status=409
            )
        if valid:
            hidden = (
                set(
                    operation.timers.exclude(
                        pk__in=Timer.objects.visible_to_user(request.user)
                    ).values_list("pk", flat=True)
                )
                if operation
                else set()
            )
            saved = form.save(commit=False)
            if not saved.pk:
                saved.created_by = request.user
            saved.updated_by = request.user
            saved.revision = version + 1
            saved.save()
            saved.timers.set(
                hidden | {timer.pk for timer in form.cleaned_data["timers"]}
            )
            save_roster(saved, form.cleaned_data.get("roster"))
            return redirect(saved)
        return self.render_to_response(self.get_context_data(operation_form=form))


@method_decorator(never_cache, name="dispatch")
class OperationTimerDataView(TimerListDataView):
    """Share Current's row presentation and filters without sharing other timers."""

    picker = False
    operation = None

    def filter_timers(self, qs):
        """Apply operation membership, or the coordinator's eligible candidates."""
        require_operation_access(self.request.user, coordinator=self.picker)
        self.operation = (
            get_operation(self.request.user, self.kwargs["pk"])
            if self.kwargs.get("pk")
            else None
        )
        if not self.picker:
            return operation_timers(self.operation, self.request.user)
        current = current_timers(self.request.user)
        if self.operation:
            return (
                qs.visible_to_user(self.request.user)
                .filter(Q(pk__in=current.values("pk")) | Q(operations=self.operation))
                .distinct()
            )
        return current

    def get_data(self, context):
        """Attach the shared repair clock to selected timer rows."""
        rows = super().get_data(context)
        if not self.picker:
            timers = {timer.pk: timer for timer in self.object_list}
            for row in rows:
                row["battle"] = observation_payload(
                    timers[row["id"]],
                    self.request.user,
                    can_edit=self.request.user.has_perm("aa_stratcom.op_coordinator"),
                )
        return rows

    def _calc_assignment(self, timer):
        """Operation viewing does not grant assignment-edit permissions."""
        return assignment_html(timer, self.request.user)

    def _get_data_actions(self, timer):
        """Keep timer details available without granting global edit rights."""
        return render_to_string(
            "aa_stratcom/partials/operation_timer_actions.html",
            {
                "timer": timer,
                "fit_buttons": fit_buttons_html(
                    getattr(timer.structure, "fitting", "")
                ),
            },
        )


class OperationPickerDataView(OperationTimerDataView):
    """The same Current candidates and filters for new and existing operations."""

    picker = True


@login_required
@require_GET
@never_cache
def operation_live(request, pk):
    """Poll only membership metadata and observations, with conditional responses."""
    operation = get_operation(request.user, pk)
    can_edit = request.user.has_perm("aa_stratcom.op_coordinator")
    timers = (
        operation_timers(operation, request.user)
        .select_related("map_state", "structure", "structure_type", "eve_solar_system")
        .order_by("pk")
    )
    payload = {
        "operation": {
            "id": operation.pk,
            "revision": operation.revision,
            "name": operation.name,
            "description": operation.description,
            "visibility": operation.get_visibility_display(),
            "can_edit": can_edit,
            "roster": roster_payload(operation),
        },
        "timers": [
            {
                **observation_payload(timer, request.user, can_edit=can_edit),
                "updated_at": timer.last_updated_at.isoformat(),
                "destroyed_at": (
                    timer.structure.destroyed_at.isoformat()
                    if timer.structure_id and timer.structure.destroyed_at
                    else None
                ),
            }
            for timer in timers
        ],
    }
    return conditional_response(request, payload)


def conditional_response(request, payload):
    """Return unchanged live data without resending the response body."""
    signature = (
        '"'
        + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        + '"'
    )
    response = (
        HttpResponse(status=304)
        if request.headers.get("If-None-Match") == signature
        else JsonResponse({**payload, "server_time": now().isoformat()})
    )
    response["ETag"] = signature
    patch_vary_headers(response, ("Cookie",))
    return response


@login_required
@require_GET
@never_cache
def operation_index_live(request):
    """Keep the Ops list current when another coordinator creates or edits an op."""
    require_operation_access(request.user)
    can_edit = request.user.has_perm("aa_stratcom.op_coordinator")
    return conditional_response(
        request,
        {
            "can_edit": can_edit,
            "operations": [
                {
                    "name": operation.name,
                    "url": operation.get_absolute_url(),
                    "visibility": operation.get_visibility_display(),
                    "updated_at": operation.updated_at.isoformat(),
                    "edit_url": (
                        reverse("aa_stratcom:operation_edit", args=[operation.pk])
                        if can_edit
                        else None
                    ),
                }
                for operation in Operation.visible_to(request.user)
            ],
        },
    )
