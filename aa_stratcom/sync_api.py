"""HTTPS bearer-only API; browser sessions never authorize replication."""

import json
import logging

from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import RequestDataTooBig, ValidationError
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_POST

from .sync_protocol import SyncConflict, receive
from .sync_security import authenticate, within_rate_limit

logger = logging.getLogger(__name__)


@login_not_required
@csrf_exempt
@never_cache
@require_POST
@sensitive_variables()
def timer_sync(request):  # pylint: disable=too-many-return-statements
    """One bounded event per request, authenticated separately from AA sessions."""
    if not request.is_secure():
        return JsonResponse({"error": "HTTPS is required."}, status=400)
    try:
        if not within_rate_limit("ip:" + request.META.get("REMOTE_ADDR", ""), 300):
            return JsonResponse(
                {"error": "Rate limit exceeded."},
                status=429,
                headers={"Retry-After": "60"},
            )
        key = authenticate(request.headers.get("Authorization", ""))
        if key is None:
            return JsonResponse(
                {"error": "Invalid or expired API key."},
                status=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not within_rate_limit("key:" + str(key.pk), 120):
            return JsonResponse(
                {"error": "Rate limit exceeded."},
                status=429,
                headers={"Retry-After": "60"},
            )
    except Exception:  # pylint: disable=broad-exception-caught
        # Cache/database outage must never bypass authentication or rate limits.
        logger.error("Timer sync authentication service unavailable")
        return JsonResponse(
            {"error": "Authentication service unavailable."}, status=503
        )
    if request.content_type != "application/json":
        return JsonResponse({"error": "Use application/json."}, status=415)
    try:
        if len(request.body) > 32768:
            raise RequestDataTooBig()
        payload = json.loads(request.body)
        return JsonResponse(receive(payload, key))
    except (RequestDataTooBig, UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse(
            {"error": "Invalid or oversized JSON request (maximum 32 KiB)."}, status=400
        )
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=422)
    except SyncConflict as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    except Exception:  # pylint: disable=broad-exception-caught
        # Do not expose traceback, upstream responses, headers or secrets.
        logger.error("Timer sync event failed; no event changes committed")
        return JsonResponse(
            {"error": "Timer sync temporarily unavailable; retry later."}, status=503
        )
