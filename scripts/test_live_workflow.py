"""End-to-end integration test against real Firestore and Firebase Auth."""

import html
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Setup paths and Django environment
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lablink.settings")
os.environ["DJANGO_DEBUG"] = "true"
os.environ["DJANGO_SECRET_KEY"] = "integration-test-secret-key-at-least-50-characters-long"
os.environ["FIREBASE_PROJECT_ID"] = "lablink-6b78a"
os.environ["FIREBASE_WEB_API_KEY"] = "AIzaSyAXyNagCGUpDkC7lqNYkEFowu64aDYFOsE"
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/tmp/lablink-sa-key.json"

import django
django.setup()

import requests
from django.conf import settings
from django.test import Client
from django.urls import reverse
from portal.store import get_store


def get_id_token(email, password):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={settings.FIREBASE_WEB_API_KEY}"
    resp = requests.post(url, json={"email": email, "password": password, "returnSecureToken": True}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data["idToken"], data.get("localId")


def run():
    print("==================================================")
    print("STARTING LABLINK END-TO-END WORKFLOW VERIFICATION")
    print("==================================================")

    store = get_store()

    # 1. Test Equipment Catalog
    catalog = store.catalog()
    assert len(catalog) >= 5, f"Expected at least 5 equipment units, found {len(catalog)}"
    print(f"✔ 1. Catalog verified: {len(catalog)} active instruments found.")

    # 2. Test Student Auth and Session Creation
    student_token, student_uid = get_id_token("student@lablink.edu", "LabLinkPass2026!")
    assert student_token, "Failed to get student ID token"
    print("✔ 2. Student Firebase Authentication successful (real token obtained).")

    client = Client()
    session_resp = client.post(reverse("create-session"), {"id_token": student_token})
    assert session_resp.status_code == 200, f"Session creation failed: {session_resp.content}"
    assert session_resp.json().get("role") == "student"
    assert settings.AUTH_COOKIE_NAME in client.cookies
    print("✔ 3. Student session cookie created and validated by backend.")

    # 3. Test Student Access to Catalog and Equipment Details
    eq = catalog[0]
    detail_resp = client.get(reverse("resource-detail", kwargs={"slug": eq["id"]}))
    assert detail_resp.status_code == 200
    assert html.escape(eq["name"]).encode() in detail_resp.content
    print(f"✔ 4. Equipment detail page accessible for '{eq['name']}'.")

    # 4. Test Student Booking Submission
    from zoneinfo import ZoneInfo
    from portal.scheduling import available_windows
    from django.utils import timezone

    now = timezone.now()
    reservations = store.upcoming_reservations(eq["id"], now=now)
    windows = [w for w in available_windows(eq, reservations, now=now) if w[0] > now + timedelta(days=1) and (w[1] - w[0]) >= timedelta(minutes=75)]
    assert windows, "No available window found for testing"
    w_start, _ = windows[0]
    tz = ZoneInfo(eq["time_zone"])
    w_start_local = w_start.astimezone(tz)
    minute = (w_start_local.minute + 14) // 15 * 15
    if minute == 60:
        target_start_local = w_start_local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        target_start_local = w_start_local.replace(minute=minute, second=0, microsecond=0)
    deadline_local = target_start_local + timedelta(hours=8)

    test_title = f"Autonomous Drone Sensor Testing {int(now.timestamp())}"

    booking_payload = {
        "project_title": test_title,
        "purpose": "Calibrate vibration sensors on 3D printed prototype chassis.",
        "starts_at": target_start_local.strftime("%Y-%m-%dT%H:%M"),
        "duration_minutes": 60,
        "deadline": deadline_local.strftime("%Y-%m-%dT%H:%M"),
    }
    submit_resp = client.post(reverse("resource-detail", kwargs={"slug": eq["id"]}), booking_payload)
    assert submit_resp.status_code == 302, f"Booking submit failed: {submit_resp.content}"
    print("✔ 5. Student booking request successfully submitted to Firestore.")

    # 5. Verify Request in Student History
    history_resp = client.get(reverse("student-history"))
    assert history_resp.status_code == 200
    assert test_title.encode() in history_resp.content
    print("✔ 6. Student history displays newly created pending request.")

    # 6. Verify Security: Student CANNOT access Faculty Dashboard
    unauth_resp = client.get(reverse("faculty"))
    assert unauth_resp.status_code == 403
    print("✔ 7. Security check: Student blocked from Faculty Dashboard (HTTP 403).")

    # 7. Faculty Authentication and Queue Inspection
    faculty_token, faculty_uid = get_id_token("faculty@lablink.edu", "LabLinkPass2026!")
    faculty_client = Client()
    faculty_session = faculty_client.post(reverse("create-session"), {"id_token": faculty_token})
    assert faculty_session.status_code == 200
    assert faculty_session.json().get("role") == "faculty"
    print("✔ 8. Faculty Firebase Authentication and session creation successful.")

    faculty_dash = faculty_client.get(reverse("faculty"))
    assert faculty_dash.status_code == 200, f"Faculty dash failed with {faculty_dash.status_code} -> {faculty_dash.headers.get('Location')}: {faculty_dash.content[:200]}"
    assert test_title.encode() in faculty_dash.content
    print("✔ 9. Faculty dashboard lists pending request in review queue.")

    # Find the request ID from history
    student_history = store.history(student_uid)
    pending_req = next((r for r in student_history if r.get("project_title") == test_title), None)
    assert pending_req, "Could not find submitted request in database"
    req_id = pending_req["id"]

    # 8. Faculty Approval
    approve_resp = faculty_client.post(
        reverse("faculty-decision", kwargs={"request_id": req_id}),
        {"decision": "approved", "reason": "Approved for capstone testing.", "slot": ""},
    )
    assert approve_resp.status_code == 302, f"Approval failed: {approve_resp.content}"
    print(f"✔ 10. Faculty approved request {req_id} (transactional reservation committed).")

    # 9. Verify Confirmed Reservation in Firestore
    updated_req = store.collection("booking_requests").document(req_id).get().to_dict()
    assert updated_req["status"] == "approved"
    assert updated_req.get("reservation_id")
    print(f"✔ 11. Request status is 'approved' with reservation ID {updated_req['reservation_id']}.")

    # 10. Verify Conflict Prevention: Overlapping booking on same unit must be rejected
    conflict_payload = {
        "project_title": "Conflicting Second Student Request",
        "purpose": "Attempting to book exact same slot.",
        "starts_at": target_start_local.strftime("%Y-%m-%dT%H:%M"),
        "duration_minutes": 60,
        "deadline": deadline_local.strftime("%Y-%m-%dT%H:%M"),
    }
    conflict_resp = client.post(reverse("resource-detail", kwargs={"slug": eq["id"]}), conflict_payload)
    assert conflict_resp.status_code == 409, f"Expected HTTP 409 conflict, got {conflict_resp.status_code}"
    print("✔ 12. Conflict prevention verified: Overlapping request rejected with HTTP 409 Conflict.")

    # 11. Test Sign Out
    signout_resp = client.post(reverse("sign-out"))
    assert signout_resp.status_code == 302
    # Verify cookie is invalidated
    unauth_history = client.get(reverse("student-history"))
    assert unauth_history.status_code == 302
    print("✔ 13. Sign out verified: Session terminated and protected routes redirect to sign-in.")

    print("==================================================")
    print("ALL 13 WORKFLOW CHECKS PASSED AGAINST REAL BACKEND!")
    print("==================================================")


if __name__ == "__main__":
    run()
