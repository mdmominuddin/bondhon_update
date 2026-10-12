from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone


# ---------------------------------------------------------------------------
# Site configuration (single row)
# ---------------------------------------------------------------------------
class SiteSetting(models.Model):
    """Editable, non-secret configuration. API keys live in environment variables."""

    LANG_CHOICES = [("en", "English"), ("bn", "Bangla")]
    SMS_BACKENDS = [
        ("console", "Console / test mode (nothing leaves the server)"),
        ("http", "HTTP SMS provider (configured in environment)"),
    ]
    GATEWAYS = [
        ("manual", "Manual (bKash / Nagad / Rocket / bank - verified by treasurer)"),
        ("sslcommerz", "SSLCommerz (cards & MFS, automatic)"),
        ("sandbox", "Sandbox (fake gateway for testing only)"),
    ]

    society_name = models.CharField(max_length=120, default="Bondhon Society")
    society_name_bn = models.CharField("Society name (Bangla)", max_length=120, blank=True, default="বন্ধন সোসাইটি")
    address = models.CharField(max_length=255, blank=True)
    contact_phone = models.CharField(max_length=30, blank=True)
    receipt_prefix = models.CharField(max_length=10, default="BND")

    default_fund_head = models.ForeignKey(
        "ExpenseHead", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Fund head credited when a member pays online.",
    )
    members_see_expenses = models.BooleanField(
        default=True, help_text="Let members view the society's expense list (read-only) in their portal.",
    )
    large_expense_threshold = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("50000"),
        help_text="Expenses at or above this amount are flagged by the audit analytics.",
    )

    # Messaging
    sms_enabled = models.BooleanField(default=True)
    sms_backend = models.CharField(max_length=10, choices=SMS_BACKENDS, default="console")
    sms_sender_id = models.CharField(max_length=20, blank=True, help_text="Approved sender ID / mask, if your provider requires one.")
    sms_language = models.CharField(max_length=2, choices=LANG_CHOICES, default="en")

    # Payments
    online_gateway = models.CharField(max_length=12, choices=GATEWAYS, default="manual")
    min_online_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("100"))
    max_online_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("500000"))
    bkash_number = models.CharField("bKash number", max_length=20, blank=True)
    nagad_number = models.CharField("Nagad number", max_length=20, blank=True)
    rocket_number = models.CharField("Rocket number", max_length=20, blank=True)
    bank_details = models.TextField(blank=True, help_text="Bank name, account name/number, branch, routing number.")
    payment_instructions = models.TextField(blank=True, help_text="Extra note shown to members on the Deposit Money page.")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site setting"

    def __str__(self):
        return "Site settings"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------
class SocietyMember(models.Model):
    MEMBER_DESIGNATION_CHOICES = [
        ("General Member", "General Member"),
        ("Treasurer", "Treasurer"),
        ("Secretary", "Secretary"),
        ("President", "President"),
    ]
    name = models.CharField(max_length=255)
    society_designation = models.CharField(
        max_length=50, choices=MEMBER_DESIGNATION_CHOICES, default="General Member"
    )
    phone_number = models.CharField(max_length=15)
    address = models.TextField(null=True, blank=True)
    shares = models.PositiveIntegerField(default=1)
    joining_date = models.DateField(default=date.today)

    # --- added in the Management & Auditing upgrade -----------------------
    user = models.OneToOneField(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="member_profile",
        help_text="Login account of this member (enables the member portal).",
    )
    is_active = models.BooleanField(default=True, help_text="Inactive members are hidden from dropdowns and SMS campaigns.")
    sms_opt_in = models.BooleanField("SMS notifications", default=True)
    must_change_password = models.BooleanField(default=False)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.name

    @property
    def code(self):
        return f"M-{self.pk:03d}" if self.pk else "M-new"

    @property
    def sms_number(self):
        from messaging.phone import normalize_bd_phone
        return normalize_bd_phone(self.phone_number)

    @property
    def has_valid_phone(self):
        return bool(self.sms_number)

    def calculate_due(self, contribution_plan):
        """Kept for backwards compatibility."""
        return contribution_plan.amount * self.shares


# ---------------------------------------------------------------------------
# Heads
# ---------------------------------------------------------------------------
class ExpenseHead(models.Model):
    """Used both as fund head (deposits) and expense head (expenses)."""
    name = models.CharField(max_length=255)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class ExpenseCategory(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        verbose_name_plural = "expense categories"

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Plans & projects
# ---------------------------------------------------------------------------
class ContributionPlan(models.Model):
    description = models.CharField(max_length=100)
    amount = models.PositiveBigIntegerField(help_text="Amount payable per share.")
    start_date = models.DateField()
    end_date = models.DateField(help_text="Due date of this contribution.")

    class Meta:
        ordering = ["start_date", "id"]

    def __str__(self):
        return self.description


class BuildingProject(models.Model):
    name = models.CharField(max_length=255)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    budget = models.DecimalField(max_digits=15, decimal_places=2)
    description = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Ledger entries
# ---------------------------------------------------------------------------
class PeriodLockedError(ValidationError):
    pass


def assert_period_open(entry_date):
    """Raise if `entry_date` falls inside a closed (locked) audit period."""
    if not entry_date:
        return
    from django.apps import apps
    Engagement = apps.get_model("audit", "AuditEngagement")
    locked = Engagement.objects.filter(
        locked=True, period_start__lte=entry_date, period_end__gte=entry_date
    ).first()
    if locked:
        raise PeriodLockedError(
            f"{entry_date:%d-%m-%Y} falls in the audited period '{locked.title}' "
            f"({locked.period_start:%d-%m-%Y} to {locked.period_end:%d-%m-%Y}) which is locked. "
            "An administrator must re-open the audit before this record can change."
        )


class DepositQuerySet(models.QuerySet):
    def valid(self):
        """Deposits that count in the books (not voided)."""
        return self.filter(is_void=False)


class ExpenseQuerySet(models.QuerySet):
    def valid(self):
        """Expenses that count in the books (approved and not voided)."""
        return self.filter(is_void=False, status="approved")


class Deposit(models.Model):
    METHODS = [
        ("cash", "Cash"),
        ("bank", "Bank transfer / cheque"),
        ("bkash", "bKash"),
        ("nagad", "Nagad"),
        ("rocket", "Rocket"),
        ("card", "Card / online gateway"),
        ("other", "Other"),
    ]
    SOURCES = [("admin", "Entered by office"), ("online", "Paid online by member")]

    date = models.DateField()
    member = models.ForeignKey(SocietyMember, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    fund_head = models.ForeignKey(ExpenseHead, on_delete=models.CASCADE)
    remark = models.CharField(max_length=200, default="Nil")

    method = models.CharField(max_length=10, choices=METHODS, default="cash")
    reference = models.CharField("Transaction / cheque ref", max_length=80, blank=True)
    receipt_no = models.CharField(max_length=30, unique=True, null=True, blank=True, editable=False)
    source = models.CharField(max_length=10, choices=SOURCES, default="admin")
    recorded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)

    is_void = models.BooleanField(default=False, editable=False)
    void_reason = models.CharField(max_length=255, blank=True, editable=False)
    voided_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)

    created_at = models.DateTimeField(null=True, blank=True, editable=False)
    updated_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects = DepositQuerySet.as_manager()

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.member.name} - {self.date.strftime('%d-%m-%Y')}"

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": "Amount must be greater than zero."})

    def save(self, *args, **kwargs):
        from django.db import IntegrityError
        from myapp.services import next_receipt_no

        now = timezone.now()
        is_new = self._state.adding
        if is_new and not self.created_at:
            self.created_at = now
        self.updated_at = now
        assert_period_open(self.date)
        if not is_new:
            # a record may not be moved out of a locked period either
            old = Deposit.objects.filter(pk=self.pk).values_list("date", flat=True).first()
            if old and old != self.date:
                assert_period_open(old)
        if is_new and not self.receipt_no:
            for attempt in range(5):
                self.receipt_no = next_receipt_no(self.date)
                try:
                    with transaction.atomic():
                        return super().save(*args, **kwargs)
                except IntegrityError:
                    if attempt == 4:
                        raise
                    self.receipt_no = None
                    self._state.adding = True
        return super().save(*args, **kwargs)

    def void(self, user, reason):
        assert_period_open(self.date)
        self.is_void = True
        self.void_reason = reason
        self.voided_by = user
        self.voided_at = timezone.now()
        self.save()


class Expense(models.Model):
    STATUS = [("pending", "Pending approval"), ("approved", "Approved"), ("rejected", "Rejected")]

    date = models.DateField()
    description = models.CharField(max_length=255)
    expense_head = models.ForeignKey(ExpenseHead, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_by = models.ForeignKey(SocietyMember, on_delete=models.CASCADE, verbose_name="Paid / handled by")
    remark = models.CharField(max_length=200, default="Nil")

    project = models.ForeignKey(BuildingProject, null=True, blank=True, on_delete=models.SET_NULL, related_name="expenses")
    voucher_no = models.CharField(max_length=40, blank=True)
    attachment = models.FileField("Voucher / bill (photo or PDF)", upload_to="expense_vouchers/%Y/%m/", blank=True)
    recorded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)

    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)
    reject_reason = models.CharField(max_length=255, blank=True, editable=False)

    is_void = models.BooleanField(default=False, editable=False)
    void_reason = models.CharField(max_length=255, blank=True, editable=False)
    voided_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)

    created_at = models.DateTimeField(null=True, blank=True, editable=False)
    updated_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects = ExpenseQuerySet.as_manager()

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.expense_head.name} - {self.date.strftime('%d-%m-%Y')}"

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": "Amount must be greater than zero."})

    def save(self, *args, **kwargs):
        now = timezone.now()
        if self._state.adding and not self.created_at:
            self.created_at = now
        self.updated_at = now
        assert_period_open(self.date)
        if not self._state.adding:
            old = Expense.objects.filter(pk=self.pk).values_list("date", flat=True).first()
            if old and old != self.date:
                assert_period_open(old)
        super().save(*args, **kwargs)

    @property
    def counts(self):
        """Does this expense count in the books?"""
        return self.status == "approved" and not self.is_void

    def approve(self, user):
        self.status = "approved"
        self.approved_by = user
        self.approved_at = timezone.now()
        self.reject_reason = ""
        self.save()

    def reject(self, user, reason):
        self.status = "rejected"
        self.approved_by = user
        self.approved_at = timezone.now()
        self.reject_reason = reason
        self.save()

    def void(self, user, reason):
        assert_period_open(self.date)
        self.is_void = True
        self.void_reason = reason
        self.voided_by = user
        self.voided_at = timezone.now()
        self.save()


# ---------------------------------------------------------------------------
# Derived / legacy
# ---------------------------------------------------------------------------
class MonthlyFinancialSummary(models.Model):
    """
    Snapshot table kept for backwards compatibility.  It is rebuilt from the
    ledger (never incremented), and the audit analytics reconcile it against
    the ledger.
    """
    month = models.DateField(default=date.today, unique=True)
    total_deposits = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_expenses = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ["month"]

    def calculate_balance(self):
        self.balance = self.total_deposits - self.total_expenses

    def save(self, *args, **kwargs):
        self.calculate_balance()
        super().save(*args, **kwargs)

    def __str__(self):
        return str(self.month.strftime("%B %Y"))


class DueDeposit(models.Model):
    member = models.ForeignKey(SocietyMember, on_delete=models.CASCADE)
    date = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(
        max_length=10, choices=[("Pending", "Pending"), ("Paid", "Paid")], default="Pending",
    )

    @classmethod
    def get_total_due_amount(cls):
        return cls.objects.filter(status="Pending").aggregate(models.Sum("amount"))["amount__sum"] or 0

    def __str__(self):
        return f"{self.member.name} - {self.date.strftime('%d-%m-%Y')}"


class Notification(models.Model):
    """In-app notification shown in the member portal."""
    member = models.ForeignKey(SocietyMember, on_delete=models.CASCADE)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    title = models.CharField(max_length=120, blank=True)
    kind = models.CharField(max_length=30, blank=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Notification for {self.member.name}"
