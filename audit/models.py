import hashlib
import json
from decimal import Decimal

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from .context import get_context


class ImmutableError(Exception):
    pass


class AuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ImmutableError("Audit log entries cannot be modified.")

    def delete(self):
        raise ImmutableError("Audit log entries cannot be deleted.")


class AuditLog(models.Model):
    """Append-only, hash-chained record of everything that happens in the system."""

    ACTIONS = [
        ("create", "Created"), ("update", "Updated"), ("delete", "Deleted"), ("void", "Voided"),
        ("approve", "Approved"), ("reject", "Rejected"), ("login", "Login"), ("logout", "Logout"),
        ("login_failed", "Failed login"), ("payment", "Payment"), ("sms", "SMS"),
        ("lock", "Period locked"), ("unlock", "Period re-opened"), ("export", "Export"),
        ("role", "Role / access change"), ("system", "System"),
    ]

    timestamp = models.DateTimeField(db_index=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    username = models.CharField(max_length=150, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    action = models.CharField(max_length=15, choices=ACTIONS, db_index=True)
    model = models.CharField(max_length=80, blank=True, db_index=True)
    object_id = models.CharField(max_length=40, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    message = models.CharField(max_length=500, blank=True)
    prev_hash = models.CharField(max_length=64, blank=True, editable=False)
    hash = models.CharField(max_length=64, blank=True, editable=False, db_index=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.username or 'system'} {self.action} {self.model} {self.object_repr}"

    # -- hashing ---------------------------------------------------------
    def _payload(self):
        return json.dumps({
            "ts": self.timestamp.isoformat(), "user": self.username, "ip": self.ip_address or "",
            "action": self.action, "model": self.model, "oid": self.object_id,
            "repr": self.object_repr, "changes": self.changes, "msg": self.message,
        }, sort_keys=True, separators=(",", ":"), default=str)

    def compute_hash(self):
        return hashlib.sha256((self.prev_hash + self._payload()).encode("utf-8")).hexdigest()

    def save(self, *args, **kwargs):
        if self.pk:
            raise ImmutableError("Audit log entries cannot be modified.")
        with transaction.atomic():
            last = AuditLog.objects.order_by("-id").values_list("hash", flat=True).first()
            self.prev_hash = last or ""
            self.timestamp = self.timestamp or timezone.now()
            self.hash = self.compute_hash()
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableError("Audit log entries cannot be deleted.")

    # -- helpers ---------------------------------------------------------
    @classmethod
    def record(cls, action, *, model="", obj=None, object_id="", object_repr="", changes=None, message="", user=None):
        ctx = get_context()
        actor = user
        if actor is None and ctx["user"] is not None:
            actor = ctx["user"].resolve() if hasattr(ctx["user"], "resolve") else ctx["user"]
        if obj is not None:
            model = model or obj._meta.label
            object_id = object_id or str(obj.pk)
            object_repr = object_repr or str(obj)[:255]
        return cls.objects.create(
            user=actor, username=getattr(actor, "username", "") or "",
            ip_address=ctx["ip"], user_agent=ctx["ua"], action=action, model=model,
            object_id=str(object_id), object_repr=object_repr[:255], changes=changes or {},
            message=message[:500],
        )

    @classmethod
    def verify_chain(cls):
        """Return (ok, checked, first_bad_id). Detects edits, deletions and re-ordering."""
        prev, checked = "", 0
        for row in cls.objects.order_by("id").iterator():
            if row.prev_hash != prev or row.compute_hash() != row.hash:
                return False, checked, row.id
            prev = row.hash
            checked += 1
        return True, checked, None


class AuditEngagement(models.Model):
    STATUS = [("planning", "Planning"), ("fieldwork", "In progress"), ("closed", "Closed (locked)")]
    OPINIONS = [
        ("", "- not issued -"),
        ("unqualified", "Unqualified - records fairly stated"),
        ("qualified", "Qualified - minor exceptions"),
        ("adverse", "Adverse - material exceptions"),
        ("disclaimer", "Disclaimer - unable to form an opinion"),
    ]

    title = models.CharField(max_length=160)
    period_start = models.DateField()
    period_end = models.DateField()
    scope = models.TextField(blank=True, help_text="What is covered: funds, accounts, procedures.")
    lead_auditor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=10, choices=STATUS, default="planning")
    locked = models.BooleanField(default=False, editable=False)

    opinion = models.CharField(max_length=12, choices=OPINIONS, blank=True)
    conclusion = models.TextField(blank=True)
    closing_snapshot = models.JSONField(default=dict, blank=True, editable=False)
    last_check_run = models.DateTimeField(null=True, blank=True, editable=False)
    last_check_summary = models.JSONField(default=dict, blank=True, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True, editable=False)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)

    class Meta:
        ordering = ["-period_end", "-id"]

    def __str__(self):
        return self.title

    @property
    def open_findings(self):
        return self.findings.exclude(status__in=["resolved", "accepted"]).count()


class AuditFinding(models.Model):
    SEVERITY = [("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")]
    CATEGORY = [
        ("control", "Internal control"), ("data", "Data quality"), ("compliance", "Compliance"),
        ("fraud", "Fraud risk"), ("reconciliation", "Reconciliation"), ("other", "Other"),
    ]
    STATUS = [("open", "Open"), ("acknowledged", "Acknowledged"), ("resolved", "Resolved"), ("accepted", "Risk accepted")]

    engagement = models.ForeignKey(AuditEngagement, on_delete=models.CASCADE, related_name="findings")
    title = models.CharField(max_length=200)
    severity = models.CharField(max_length=10, choices=SEVERITY, default="medium")
    category = models.CharField(max_length=15, choices=CATEGORY, default="control")
    description = models.TextField()
    recommendation = models.TextField(blank=True)
    reference = models.CharField(max_length=120, blank=True, help_text="Record this relates to, e.g. 'Deposit BND-2023-00012'.")
    status = models.CharField(max_length=14, choices=STATUS, default="open")
    management_response = models.TextField(blank=True)
    check_code = models.CharField(max_length=40, blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.title

    SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


class ReconciliationEntry(models.Model):
    """Physical verification: cash in hand, bank balance, mobile-wallet balance."""
    KIND = [("cash", "Cash in hand"), ("bank", "Bank balance"), ("mfs", "bKash / Nagad / Rocket balance"), ("other", "Other")]
    engagement = models.ForeignKey(AuditEngagement, on_delete=models.CASCADE, related_name="reconciliations")
    kind = models.CharField(max_length=6, choices=KIND, default="cash")
    label = models.CharField(max_length=120, help_text="e.g. 'Cash with treasurer', 'Islami Bank A/C ...1234'.")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    counted_on = models.DateField()
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["counted_on", "id"]

    def __str__(self):
        return f"{self.label}: {self.amount}"
