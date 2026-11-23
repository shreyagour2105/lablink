"""Isolated, browser-scoped demonstration. This module uses no database."""

import json

from django.conf import settings
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

COOKIE = "lablink_demo"
SALT = "lablink.demo.v1"
MAX_AGE = 8 * 60 * 60
MAX_REQUESTS = 8
ROLES = ("student", "faculty")
UNITS = {
    "printer": ("FDM 3D Printer", "Mechanical Engineering", "Prototype fabrication"),
    "scope": ("Digital Oscilloscope", "Electrical Engineering", "Signal analysis"),
    "station": ("GPU Workstation", "Computer Science", "GPU computing"),
}
SLOTS = ("09:00–10:00", "10:00–11:00", "11:00–12:00")
PROJECTS = ("Prototype enclosure", "Sensor calibration", "Image analysis")


def initial_state():
    return {"role": "", "requests": [
        {"unit": "printer", "slot": SLOTS[0], "project": PROJECTS[0], "status": "pending"},
    ]}


def read_state(request):
    raw = request.get_signed_cookie(COOKIE, default=None, salt=SALT, max_age=MAX_AGE)
    return json.loads(raw) if raw is not None else initial_state()


def save_state(response, state):
    response.set_signed_cookie(
        COOKIE, json.dumps(state, separators=(",", ":")), salt=SALT, max_age=MAX_AGE,
        httponly=True, secure=settings.AUTH_COOKIE_SECURE, samesite="Lax", path="/demo/",
    )
    return response


def slot_reserved(rows, unit, slot):
    return any(
        row["status"] == "approved" and row["unit"] == unit and row["slot"] == slot
        for row in rows
    )


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
    return save_state(redirect(f"demo-{role}"), state)


def render_workspace(request, state, *, error=None, status=200):
    role = state["role"]
    rows = state["requests"]
    query = request.GET.get("q", "").strip()[:100]
    units = [
        {"id": key, "name": name, "department": department, "capability": capability,
         "slots": [slot for slot in SLOTS if not slot_reserved(rows, key, slot)]}
        for key, (name, department, capability) in UNITS.items()
        if query.casefold() in f"{name} {department} {capability}".casefold()
    ]
    requests = [{"number": i, "equipment": UNITS[row["unit"]][0], **row} for i, row in enumerate(rows)]
    return render(request, "demo_workspace.html", {
        "demo_role": role, "units": units, "query": query, "projects": PROJECTS,
        "requests": requests, "error": error, "at_limit": len(rows) >= MAX_REQUESTS,
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
    unit, slot, project = (request.POST.get(field) for field in ("unit", "slot", "project"))
    rows = state["requests"]
    if unit not in UNITS or slot not in SLOTS or project not in PROJECTS:
        return render_workspace(request, state, error="Select a listed demo project and time.", status=400)
    if len(rows) >= MAX_REQUESTS or slot_reserved(rows, unit, slot):
        return render_workspace(request, state, error="This slot or demo request quota is unavailable.", status=409)
    rows.append({"unit": unit, "slot": slot, "project": project, "status": "pending"})
    return save_state(redirect("demo-student"), state)


@never_cache
@require_POST
def decide(request, number):
    state = read_state(request)
    if state["role"] != "faculty":
        return HttpResponseForbidden("A faculty demo session is required.")
    rows = state["requests"]
    decision = request.POST.get("decision")
    if decision not in ("approved", "rejected") or number >= len(rows):
        return render_workspace(request, state, error="Select an existing request and decision.", status=400)
    row = rows[number]
    if row["status"] != "pending":
        return render_workspace(request, state, error="This request has already been decided.", status=409)
    if decision == "approved" and slot_reserved(rows, row["unit"], row["slot"]):
        return render_workspace(request, state, error="A simulated reservation already occupies this slot.", status=409)
    row["status"] = decision
    return save_state(redirect("demo-faculty"), state)
