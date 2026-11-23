import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from django import forms
from django.utils import timezone

from .availability import local_instant
from .scheduling import normalize_capabilities


class LabDateTimeField(forms.CharField):
    widget = forms.DateTimeInput(attrs={"type": "datetime-local"})

    def clean(self, value):
        value = super().clean(value)
        if not value:
            return None
        try:
            if len(value) != 16:
                raise ValueError
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M")
        except ValueError as error:
            raise forms.ValidationError("Enter a valid date and time.") from error
        try:
            return local_instant(parsed, self.zone)
        except OverflowError as error:
            raise forms.ValidationError("Enter a date within the supported time range.") from error
        except ValueError as error:
            raise forms.ValidationError(str(error)) from error


class BookingForm(forms.Form):
    project_title = forms.CharField(label="Project name", max_length=120)
    purpose = forms.CharField(label="How will you use this equipment?", max_length=1000, widget=forms.Textarea(attrs={"rows": 3}))
    starts_at = LabDateTimeField(label="Requested start", max_length=16, widget=forms.DateTimeInput(attrs={"type": "datetime-local", "step": 900}))
    duration_minutes = forms.IntegerField(
        label="Required duration (minutes)", min_value=15, max_value=480,
        widget=forms.NumberInput(attrs={"step": 15}),
    )
    deadline = LabDateTimeField(label="Project deadline", max_length=16)

    urgency_note = forms.CharField(
        label="Urgency justification (optional)", max_length=300, required=False,
        help_text="Faculty can read this note. It does not add priority points.",
    )
    required_capabilities = forms.CharField(
        label="Additional required capabilities (optional)", max_length=240, required=False,
        help_text="Separate equipment capabilities with commas.",
    )
    earliest_start = LabDateTimeField(
        label="Earliest alternative start (optional)", max_length=16, required=False,
        help_text="Set both alternative bounds to permit an equivalent unit in this lab or a different time.",
    )
    latest_end = LabDateTimeField(label="Latest alternative end (optional)", max_length=16, required=False)

    def clean_required_capabilities(self):
        return sorted(normalize_capabilities(self.cleaned_data["required_capabilities"].split(",")))

    def __init__(self, *args, time_zone, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("starts_at", "deadline", "earliest_start", "latest_end"):
            self.fields[name].zone = ZoneInfo(time_zone)

    def clean(self):
        data = super().clean()
        start, duration, deadline = (data.get(key) for key in ("starts_at", "duration_minutes", "deadline"))
        if duration is not None and duration % 15:
            self.add_error("duration_minutes", "Use a multiple of 15 minutes.")
        if start:
            local_start = start.astimezone(self.fields["starts_at"].zone)
            if local_start.minute % 15:
                self.add_error("starts_at", "Choose a start on a 15-minute boundary.")
            elif start <= timezone.now():
                self.add_error("starts_at", "Choose a start in the future.")
        if start and duration and deadline and deadline - start < timedelta(minutes=duration):
            self.add_error("deadline", "The project deadline must be at or after the requested end time.")
        earliest, latest = data.get("earliest_start"), data.get("latest_end")
        if (earliest is None) != (latest is None):
            self.add_error("latest_end", "Enter both bounds to allow alternatives.")
        if earliest and latest:
            if earliest >= latest:
                self.add_error("latest_end", "The latest end must follow the earliest start.")
            elif start and duration and (earliest > start or latest - start < timedelta(minutes=duration)):
                self.add_error("latest_end", "Include the requested interval within the alternative bounds.")
            if deadline and latest > deadline:
                self.add_error("latest_end", "The alternative window must end by the project deadline.")
        return data


class DecisionForm(forms.Form):
    decision = forms.ChoiceField(choices=[("approved", "Approve"), ("rejected", "Reject"), ("reschedule requested", "Request rescheduling")])
    reason = forms.CharField(max_length=500, required=False)
    slot = forms.CharField(max_length=150, required=False)

    def clean_slot(self):
        raw = self.cleaned_data["slot"]
        if not raw:
            return None
        try:
            slug, value = raw.split("|", 1)
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", slug):
                raise ValueError
            start = datetime.fromisoformat(value)
            if start.tzinfo is None:
                raise ValueError
            return slug, start.astimezone(UTC)
        except (ValueError, OverflowError) as error:
            raise forms.ValidationError("Select a valid recommended slot.") from error

    def clean(self):
        data = super().clean()
        if data.get("decision") == "rejected" and not data.get("reason"):
            self.add_error("reason", "Enter a reason for rejecting the request.")
        if data.get("decision") == "reschedule requested" and not data.get("reason"):
            self.add_error("reason", "Explain what the student should reschedule.")
        return data
