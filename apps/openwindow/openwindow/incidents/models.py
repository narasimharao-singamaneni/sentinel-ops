from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Incident(models.Model):
    """Represents an incident detected by the AIOps Sentinel."""

    class Status(models.TextChoices):
        NEW = "NEW", "New"
        APPROVED = "APPROVED", "Approved"
        EXECUTED = "EXECUTED", "Executed"
        DUPLICATE_IGNORED = "DUPLICATE_IGNORED", "Duplicate Ignored"

    id = models.BigAutoField(primary_key=True)
    root_cause = models.TextField(help_text="Root cause description from Sentinel RCA")
    confidence = models.FloatField(
        help_text="Confidence score (0.0-1.0) from the RCA engine",
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
    )
    citations = models.JSONField(
        default=list,
        blank=True,
        help_text="List of evidence citations (trace_id, span_id, snippet)",
    )
    dry_run_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Dry-run remediation payload from Sentinel",
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.NEW,
        blank=True,
    )
    target_resource = models.CharField(
        max_length=255,
        help_text="Target resource identifier (e.g., 'person-api-db-pool')",
    )
    raw_log = models.TextField(
        null=True,
        blank=True,
        help_text="Raw log/evidence text reported with the incident (nullable)",
    )
    roi = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Per-incident ROI object: runbook_class, manual_estimate_minutes, "
            "tool_time_minutes, time_saved_minutes, breakdown (nullable)"
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="idx_inc_status_created"),
            models.Index(fields=["root_cause", "target_resource"], name="idx_inc_dedup"),
        ]

    def __str__(self) -> str:
        return f"[{self.status}] {self.root_cause} @ {self.target_resource}"
