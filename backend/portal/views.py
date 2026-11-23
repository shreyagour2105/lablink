import logging
import time
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import DefaultCredentialsError

from . import firebase_auth
from .availability import available_windows
from .demo_scheduling import landing_preview
from .faculty_dashboard import faculty_snapshot
from .forms import BookingForm, DecisionForm
from .gemini import explain
from .reporting import REPORT_PERIODS
from .scheduling import eligible
from .store import BookingConflict, RequestNotFound, get_store
from .student_dashboard import booking_history

logger = logging.getLogger(__name__)


def error_page(request, status, title, message):
    return render(request, "error.html", {"title": title, "message": message}, status=status)


def require_account(view):
    @wraps(view)
    def protected(request, *args, **kwargs):
        cookie = request.COOKIES.get(settings.AUTH_COOKIE_NAME)
        if not cookie:
            return redirect("sign-in")
        try:
            identity = firebase_auth.verify_cookie(cookie)
        except firebase_auth.InvalidCredential:
            response = redirect("sign-in")
            response.delete_cookie(settings.AUTH_COOKIE_NAME, samesite="Lax")
            return response
        except firebase_auth.AuthUnavailable:
            return error_page(request, 503, "Sign-in unavailable", "Try again shortly.")

        try:
            account = get_store().account(identity["uid"])
            if not account or account.get("role") not in {"student", "faculty"}:
                return error_page(request, 403, "Access required", "Ask your campus administrator to provision LabLink access.")
            return view(request, account, *args, **kwargs)
        except (GoogleAPICallError, DefaultCredentialsError):
            return error_page(request, 503, "LabLink is temporarily unavailable", "Return to your workspace to try again.")

    return never_cache(protected)


@require_GET
def home(request):
    return render(request, "home.html", {"conflict_preview": landing_preview()})


@never_cache
@require_GET
def sign_in(request):
    firebase_config = {
        "apiKey": settings.FIREBASE_WEB_API_KEY,
        "projectId": settings.FIREBASE_PROJECT_ID,
        "emulatorHost": settings.FIREBASE_AUTH_EMULATOR_HOST,
    }
    ready = bool(firebase_config["apiKey"] and firebase_config["projectId"])
    return render(request, "sign_in.html", {"firebase_config": firebase_config, "ready": ready})


@never_cache
@require_POST
def create_session(request):
    token = request.POST.get("id_token", "")
    if not token or len(token) > 8192:
        return JsonResponse({"error": "A valid sign-in token is required."}, status=400)

    stage = "verify_id_token"
    try:
        identity = firebase_auth.verify_id_token(token)
        auth_age = time.time() - identity.get("auth_time", 0)
        if not -30 <= auth_age <= 300:
            return JsonResponse({"error": "Sign in again to continue."}, status=401)
        stage = "firestore_account_lookup"
        account = get_store().account(identity["uid"])
        if not account or account.get("role") not in {"student", "faculty"}:
            return JsonResponse({"error": "Your account has not been provisioned for LabLink."}, status=403)
        stage = "create_session_cookie"
        cookie = firebase_auth.create_cookie(token, timedelta(seconds=settings.AUTH_COOKIE_AGE))
    except firebase_auth.InvalidCredential:
        return JsonResponse({"error": "Sign-in could not be verified."}, status=401)
    except (firebase_auth.AuthUnavailable, GoogleAPICallError, DefaultCredentialsError) as error:
        cause = error.__cause__ if isinstance(error, firebase_auth.AuthUnavailable) else error
        logger.warning(
            "LabLink sign-in dependency unavailable stage=%s error_type=%s",
            stage,
            type(cause or error).__name__,
        )
        return JsonResponse({"error": "Sign-in is temporarily unavailable."}, status=503)

    response = JsonResponse({"role": account["role"]})
    response.set_cookie(
        settings.AUTH_COOKIE_NAME,
        cookie,
        max_age=settings.AUTH_COOKIE_AGE,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite="Lax",
    )
    return response


@never_cache
@require_POST
def sign_out(request):
    response = redirect("home")
    response.delete_cookie(settings.AUTH_COOKIE_NAME, samesite="Lax")
    return response


@require_GET
@require_account
def dashboard(request, account):
    return redirect("faculty" if account["role"] == "faculty" else "catalog")


@require_GET
@require_account
def catalog(request, account):
    query = request.GET.get("q", "").strip()[:100]
    department = request.GET.get("department", "").strip()[:120]
    all_units = get_store().catalog()
    departments = sorted({unit["department"] for unit in all_units})
    units = [
        unit for unit in all_units
        if (not department or unit["department"] == department)
        and (not query or query.casefold() in " ".join([unit["name"], unit["capability"], unit["lab_name"]]).casefold())
    ]
    return render(request, "catalog.html", {
        "account": account, "units": units, "departments": departments, "query": query, "department": department,
    })


@require_http_methods(["GET", "POST"])
@require_account
def resource_detail(request, account, slug):
    store = get_store()
    resource = store.resource(slug)
    if not resource:
        raise Http404
    is_student = account["role"] == "student"
    can_request = eligible(resource, account)
    status = 200
    form = BookingForm(request.POST if request.method == "POST" else None, time_zone=resource["time_zone"])

    if request.method == "POST":
        if not is_student:
            return error_page(request, 403, "Student access required", "Only students can submit project requests.")
        if not can_request:
            return error_page(request, 403, "Access required", "This equipment requires lab access.")
        status = 400
        if form.is_valid():
            try:
                store.submit(account["id"], slug, form.cleaned_data)
            except RequestNotFound as error:
                raise Http404 from error
            except BookingConflict as error:
                form.add_error("starts_at", str(error))
                status = 409
            else:
                return redirect("student-history")

    now = timezone.now()
    reservations = store.upcoming_reservations(slug, now=now)
    return render(request, "resource.html", {
        "account": account,
        "resource": resource,
        "availability": list(available_windows(resource, reservations, now=now)),
        "form": form if can_request else None,
        "restricted": is_student and not can_request,
    }, status=status)


@require_GET
@require_account
def student_history(request, account):
    if account["role"] != "student":
        return error_page(request, 403, "Student access required", "Student histories are private to each student.")
    history = booking_history(get_store(), account, now=timezone.now())
    return render(request, "history.html", {"account": account, **history})


def render_faculty(request, account, *, status=200, decision_error=None, explanation_request_id=None):
    now = timezone.now()
    context = faculty_snapshot(get_store(), account.get("lab_ids", []), request.GET.get("days"), now)
    if explanation_request_id is not None:
        entry = next((row for row in context["queue"] if row["item"]["id"] == explanation_request_id), None)
        if entry is None:
            raise Http404
        entry["explanation"] = explain(entry, now=now)
    return render(request, "faculty.html", {
        **context, "account": account, "decision_error": decision_error,
        "period_choices": REPORT_PERIODS,
    }, status=status)


@require_GET
@require_account
def faculty(request, account):
    if account["role"] != "faculty":
        return error_page(request, 403, "Faculty access required", "This dashboard is available to provisioned faculty.")
    return render_faculty(request, account)


@require_POST
@require_account
def faculty_explanation(request, account, request_id):
    if account["role"] != "faculty":
        return error_page(request, 403, "Faculty access required", "Only assigned faculty can view this explanation.")
    return render_faculty(request, account, explanation_request_id=request_id)


@require_POST
@require_account
def faculty_decision(request, account, request_id):
    if account["role"] != "faculty":
        return error_page(request, 403, "Faculty access required", "Only assigned faculty can decide a request.")
    form = DecisionForm(request.POST)
    if not form.is_valid():
        message = next(iter(form.errors.values()))[0]
        return render_faculty(request, account, status=400, decision_error=message)
    try:
        get_store().decide(account["id"], request_id, **form.cleaned_data)
    except RequestNotFound as error:
        raise Http404 from error
    except BookingConflict as error:
        return render_faculty(request, account, status=409, decision_error=str(error))
    outcome = {
        "approved": "Request approved and reservation created.",
        "rejected": "Request rejected and reason recorded.",
        "reschedule requested": "Rescheduling requested and reason recorded.",
    }[form.cleaned_data["decision"]]
    messages.success(request, outcome)
    location = reverse("faculty")
    return redirect(f"{location}?days=30" if request.GET.get("days") == "30" else location)
