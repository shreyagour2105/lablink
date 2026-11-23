"""Browser-scoped demonstration with simulated records and no database."""

from django import forms
from django.conf import settings
from django.core import signing
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .demo_scheduling import (
    UNITS,
    can_approve,
    feasible,
    initial_rows,
    new_row,
    scenario,
)
from .demo_student_dashboard import record_activity, student_overview

COOKIE = "lablink_demo"
SALT = "lablink.demo.v2"
MAX_AGE = 8 * 60 * 60
MAX_REQUESTS = 8
ROLES = ("student", "faculty")


class DemoRequestForm(forms.Form):
    title = forms.CharField(max_length=60, label="Project title", initial="Lab bracket prototype")
    purpose = forms.CharField(max_length=100, initial="Print a test part.")
    start = forms.TimeField(label="Preferred start", widget=forms.TimeInput(attrs={"type": "time"}), initial="09:00")
    end = forms.TimeField(label="Latest acceptable end", widget=forms.TimeInput(attrs={"type": "time"}), initial="12:00")
    deadline = forms.TimeField(label="Project deadline", widget=forms.TimeInput(attrs={"type": "time"}), initial="12:00")
    urgency_note = forms.CharField(
        label="Urgency justification (optional)", max_length=120, required=False,
        help_text="Use fictional details. This note does not add priority points.",
    )
    capability = forms.ChoiceField(
        label="Required capability",
        choices=[(unit[2], unit[2]) for unit in UNITS.values()],
    )
    minutes = forms.TypedChoiceField(label="Required minutes", choices=[(n, n) for n in (30, 45, 60, 90, 120)], coerce=int, initial=60)

    def clean(self):
        data = super().clean()
        for key in ("start", "end", "deadline"):
            if clock := data.get(key):
                if clock.second or clock.microsecond or clock.minute % 15:
                    self.add_error(key, "Use a 15-minute boundary.")
                else:
                    data[key] = clock.hour * 60 + clock.minute
        return data


def read_state(request):
    try:
        return signing.loads(request.COOKIES.get(COOKIE, ""), salt=SALT, max_age=MAX_AGE)
    except signing.BadSignature:
        return {"role": "", "requests": initial_rows()}


def save_state(state, role):
    value = signing.dumps(state, salt=SALT, compress=True)
    if len(value) > 3600:
        return HttpResponseBadRequest("Demo storage is full. Start with a fresh browser profile.")
    response = redirect(f"demo-{role}")
    response.set_cookie(
        COOKIE, value, max_age=MAX_AGE, httponly=True,
        secure=settings.AUTH_COOKIE_SECURE, samesite="Lax", path="/demo/",
    )
    return response


@never_cache
@require_GET
def entry(request):
    return render(request, "demo.html")


@never_cache
@require_POST
def enter(request):
    role = request.POST.get("role")
    if role not in ROLES:
        return HttpResponseBadRequest("Choose Student or Faculty.")
    state = read_state(request)
    state["role"] = role
    return save_state(state, role)


def render_workspace(request, state, *, error=None, status=200, bound_form=None, bound_unit=None):
    query = request.GET.get("q", "").strip()[:100]
    department = request.GET.get("department", "")
    units, requests = scenario(state["requests"])
    filtered = [
        {**item, "form": bound_form if item["id"] == bound_unit else DemoRequestForm(auto_id=f'{item["id"]}_%s', initial={"capability": item["capability"]})}
        for item in units
        if query.casefold() in f'{item["name"]} {item["department"]} {item["capability"]}'.casefold()
        and (not department or item["department"] == department)
    ]
    at_limit = len(state["requests"]) >= MAX_REQUESTS
    request_unit = None
    dashboard = None
    if state["role"] == "student":
        dashboard = student_overview(units, requests)
        if request.GET.get("request") == "1" and not at_limit and bound_form is None:
            request_unit = next((unit["id"] for unit in filtered if unit["slots"]), None)
    return render(request, "demo_workspace.html", {
        "demo_role": state["role"], "units": filtered, "query": query,
        "departments": sorted({item["department"] for item in units}), "department": department,
        "requests": requests, "schedule": units, "error": error,
        "at_limit": at_limit, "dashboard": dashboard, "request_unit": request_unit,
    }, status=status)


@never_cache
@require_GET
def workspace(request, role):
    state = read_state(request)
    if not state["role"]:
        return redirect("demo")
    if state["role"] != role:
        return HttpResponseForbidden("Switch demo roles to open this workspace.")
    return render_workspace(request, state)


@never_cache
@require_POST
def submit(request):
    state = read_state(request)
    if state["role"] != "student":
        return HttpResponseForbidden("A student demo session is required.")
    unit = request.POST.get("unit")
    if unit not in UNITS:
        return HttpResponseBadRequest("Choose a fictional equipment unit.")
    form = DemoRequestForm(request.POST, auto_id=f"{unit}_%s")
    if not form.is_valid():
        return render_workspace(request, state, status=400, bound_form=form, bound_unit=unit)
    rows = state["requests"]
    row = new_row(unit, **form.cleaned_data)
    if len(rows) >= MAX_REQUESTS or not feasible(row, rows):
        return render_workspace(request, state,
                                error="No feasible slot fits those requirements, or the demo request limit was reached.",
                                status=409, bound_form=form, bound_unit=unit)
    record_activity(row, rows)
    rows.append(row)
    return save_state(state, "student")


@never_cache
@require_POST
def decide(request, number):
    state = read_state(request)
    if state["role"] != "faculty":
        return HttpResponseForbidden("A faculty demo session is required.")
    rows = state["requests"]
    decision = request.POST.get("decision")
    if decision not in ("approved", "rejected", "reschedule requested") or number >= len(rows):
        return HttpResponseBadRequest("Choose an existing request and decision.")
    row = rows[number]
    if row["status"] != "pending":
        return render_workspace(request, state, error="This request has already been decided.", status=409)
    if decision == "approved":
        raw = request.POST.get("start", "")
        if not raw.isascii() or not raw.isdecimal() or len(raw) > 4 or not can_approve(row, rows, int(raw)):
            return render_workspace(request, state, error="This allocation conflicts with the booking constraints.", status=409)
        row["allocated"] = int(raw)
    row["status"] = decision
    record_activity(row, rows)
    return save_state(state, "faculty")
