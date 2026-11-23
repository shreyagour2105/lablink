from datetime import timedelta
from functools import lru_cache

from django.conf import settings
from django.utils import timezone
from google.api_core.exceptions import Aborted, AlreadyExists, FailedPrecondition
from google.auth.credentials import AnonymousCredentials
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from .scheduling import approved_intervals, candidates, equipment_capabilities, slot_is_feasible


class BookingConflict(Exception):
    pass


class RequestNotFound(Exception):
    pass


@lru_cache(maxsize=2)
def firestore_client(project_id, emulator_host):
    # The Firestore SDK uses FIRESTORE_EMULATOR_HOST to choose the endpoint.
    credentials = AnonymousCredentials() if emulator_host else None
    return firestore.Client(project=project_id or None, credentials=credentials)


def get_store():
    return Store(firestore_client(settings.FIREBASE_PROJECT_ID, settings.FIRESTORE_EMULATOR_HOST))


def record(snapshot):
    return {**snapshot.to_dict(), "id": snapshot.id} if snapshot.exists else None


class Store:
    def __init__(self, client, *, prefix=""):
        self.client = client
        self.prefix = prefix

    def collection(self, name):
        return self.client.collection(f"{self.prefix}{name}")

    def account(self, uid):
        if not uid or "/" in uid or uid in {".", ".."}:
            return None
        return record(self.collection("accounts").document(uid).get())

    def provision(self, uid, role, lab_ids, eligible_lab_ids=()):
        labs = self.collection("labs")
        if any(not labs.document(lab_id).get().exists for lab_id in {*lab_ids, *eligible_lab_ids}):
            raise ValueError("Every assigned or eligible lab must exist.")
        self.collection("accounts").document(uid).set({"role": role, "lab_ids": lab_ids, "eligible_lab_ids": list(eligible_lab_ids)}, merge=True)

    def catalog(self):
        query = self.collection("equipment").where(filter=FieldFilter("active", "==", True))
        return sorted((record(item) for item in query.stream()), key=lambda item: (item["name"].casefold(), item["id"]))

    def resource(self, slug):
        item = record(self.collection("equipment").document(slug).get())
        return item if item and item.get("active") else None

    def upcoming_reservations(self, slug, *, now, transaction=None):
        query = (
            self.collection("equipment").document(slug).collection("reservations")
            .where(filter=FieldFilter("ends_at", ">", now))
        )
        return [record(item) for item in query.stream(transaction=transaction)]

    def history(self, uid, *, transaction=None):
        query = self.collection("booking_requests").where(filter=FieldFilter("student_uid", "==", uid))
        return sorted((record(item) for item in query.stream(transaction=transaction)), key=lambda item: item["submitted_at"], reverse=True)

    def labs(self, lab_ids):
        labs = self.collection("labs")
        return [item for lab_id in lab_ids if (item := record(labs.document(lab_id).get()))]

    def lab_records(self, collection_name, lab_ids):
        collection = self.collection(collection_name)
        return [
            record(snapshot)
            for lab_id in sorted(set(lab_ids))
            for snapshot in collection.where(filter=FieldFilter("lab_id", "==", lab_id)).stream()
        ]

    def submit(self, uid, slug, data):
        resource_ref = self.collection("equipment").document(slug)
        request_ref = self.collection("booking_requests").document()
        account_ref = self.collection("accounts").document(uid)
        start = data["starts_at"]

        @firestore.transactional
        def save_request(transaction):
            account = record(account_ref.get(transaction=transaction))
            resource = record(resource_ref.get(transaction=transaction))
            if not resource or not account or account.get("role") != "student":
                raise RequestNotFound
            now = timezone.now()
            explicit = set(data.get("required_capabilities", []))
            capabilities = equipment_capabilities(resource)
            if not explicit <= capabilities:
                raise BookingConflict("This equipment does not meet all of the required capabilities.")
            item = {
                **data,
                "required_capabilities": sorted(capabilities | explicit),
                "student_uid": uid,
                "resource_id": slug,
                "resource_name": resource["name"],
                "lab_id": resource["lab_id"],
                "time_zone": resource["time_zone"],
            }
            reservations = self.upcoming_reservations(slug, now=now, transaction=transaction)
            personal = approved_intervals(self.history(uid, transaction=transaction))
            if not candidates(item, [resource], account, {slug: reservations}, personal, now):
                raise BookingConflict("That slot is unavailable or you are ineligible. Check the hours, deadline, and your reservations.")
            transaction.create(request_ref, {
                **item,
                "ends_at": start + timedelta(minutes=data["duration_minutes"]),
                "status": "pending",
                "submitted_at": firestore.SERVER_TIMESTAMP,
            })

        save_request(self.client.transaction())
        return request_ref.id

    def decide(self, uid, request_id, decision, reason, slot=None):
        if decision not in {"approved", "rejected", "reschedule requested"}:
            raise ValueError("Unsupported booking decision.")
        if decision in {"rejected", "reschedule requested"} and not reason.strip():
            raise ValueError("A rejection or reschedule request requires a reason.")
        request_ref = self.collection("booking_requests").document(request_id)
        account_ref = self.collection("accounts").document(uid)

        @firestore.transactional
        def save_decision(transaction):
            # Keep the faculty permission read transactional. The mutable booking records
            # use write preconditions to avoid two readers deadlocking on a lock upgrade.
            account = record(account_ref.get(transaction=transaction))
            request_snapshot = request_ref.get()
            item = record(request_snapshot)
            if not account or account.get("role") != "faculty" or not item or item["lab_id"] not in account.get("lab_ids", []):
                raise RequestNotFound
            if item["status"] != "pending":
                raise BookingConflict("This request was already decided.")

            changes = {
                "status": decision, "decision_reason": reason,
                "decided_by": uid, "decided_at": firestore.SERVER_TIMESTAMP,
            }
            if decision == "approved":
                changes.update(self._reserve(transaction, request_ref.id, item, slot))
            transaction.update(request_ref, changes, option=self._version(request_snapshot))

        for _ in range(4):
            try:
                save_decision(self.client.transaction())
                return
            except (Aborted, AlreadyExists, FailedPrecondition):
                continue
            except ValueError as error:
                if not isinstance(error.__cause__, Aborted):
                    raise
        raise BookingConflict("The records changed during approval. Refresh and try again.")

    def _version(self, snapshot):
        return self.client.write_option(last_update_time=snapshot.update_time)

    def _reserve(self, transaction, request_id, item, slot):
        slug, start = slot or (item["resource_id"], item["starts_at"])
        resource_ref = self.collection("equipment").document(slug)
        resource_snapshot = resource_ref.get()
        student_ref = self.collection("accounts").document(item["student_uid"])
        student_snapshot = student_ref.get()
        resource, student = record(resource_snapshot), record(student_snapshot)
        now = timezone.now()
        if resource and student:
            reservations = self.upcoming_reservations(slug, now=now)
            personal = approved_intervals(self.history(item["student_uid"]))
            if slot_is_feasible(item, resource, student, start, reservations, personal, now):
                end = start + timedelta(minutes=item["duration_minutes"])
                reservation_ref = resource_ref.collection("reservations").document(request_id)
                transaction.create(reservation_ref, {
                    "request_id": request_id, "student_uid": item["student_uid"],
                    "lab_id": item["lab_id"], "starts_at": start, "ends_at": end,
                })
                # Any concurrent booking must change the resource and student versions.
                transaction.update(resource_ref, {"booking_revision": firestore.Increment(1)}, option=self._version(resource_snapshot))
                transaction.update(student_ref, {"booking_revision": firestore.Increment(1)}, option=self._version(student_snapshot))
                return {
                    "reservation_id": reservation_ref.id,
                    "reserved_resource_id": slug,
                    "reserved_resource_name": resource["name"],
                    "reserved_time_zone": resource["time_zone"],
                    "reserved_start": start,
                    "reserved_end": end,
                }
        raise BookingConflict("That slot no longer meets the booking constraints. The request is still pending.")
