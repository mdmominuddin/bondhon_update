import secrets

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


def new_reference():
    return f"PAY-{timezone.localdate():%y%m%d}-{secrets.token_hex(3).upper()}"


class OnlinePayment(models.Model):
    """A member's attempt to deposit money.  Becomes a Deposit once confirmed."""

    METHODS = [
        ("bkash", "bKash"), ("nagad", "Nagad"), ("rocket", "Rocket"),
        ("bank", "Bank transfer"), ("card", "Card / online gateway"),
    ]
    STATUS = [
        ("initiated", "Awaiting gateway"),
        ("pending", "Pending verification"),
        ("success", "Confirmed"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
        ("rejected", "Rejected"),
    ]
    OPEN_STATUSES = ("initiated", "pending")

    reference = models.CharField(max_length=24, unique=True, default=new_reference, editable=False)
    member = models.ForeignKey("myapp.SocietyMember", on_delete=models.PROTECT, related_name="online_payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=10, choices=METHODS)
    gateway = models.CharField(max_length=12, default="manual")
    status = models.CharField(max_length=10, choices=STATUS, default="pending", db_index=True)

    trx_id = models.CharField("Transaction ID", max_length=60, blank=True)
    sender_number = models.CharField("Paid from (number / account)", max_length=40, blank=True)
    note = models.CharField(max_length=255, blank=True)

    gateway_ref = models.CharField(max_length=80, blank=True)
    raw_response = models.JSONField(default=dict, blank=True, editable=False)

    fund_head = models.ForeignKey("myapp.ExpenseHead", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    deposit = models.OneToOneField("myapp.Deposit", null=True, blank=True, on_delete=models.SET_NULL, related_name="online_payment")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            # one transaction ID can back only one live payment per method
            models.UniqueConstraint(
                fields=["method", "trx_id"],
                condition=~Q(trx_id="") & ~Q(status__in=["rejected", "failed", "cancelled"]),
                name="uniq_live_trx_per_method",
            ),
        ]

    def __str__(self):
        return f"{self.reference} - {self.member} - {self.amount}"

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES
