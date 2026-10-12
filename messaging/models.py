from django.conf import settings
from django.db import models
from django.db.models import Q


class MessageTemplate(models.Model):
    """Centrally managed message text.  System templates (is_system) fire automatically."""

    EVENTS = [
        ("payment_received", "Payment received (auto - sent for every deposit)"),
        ("payment_pending", "Online payment submitted (auto)"),
        ("payment_rejected", "Online payment rejected (auto)"),
        ("due_reminder", "Dues reminder"),
        ("account_created", "Portal login created"),
        ("custom", "Custom / broadcast"),
    ]
    LANGS = [("en", "English"), ("bn", "Bangla")]

    key = models.SlugField(max_length=40, help_text="Event key, e.g. payment_received.")
    language = models.CharField(max_length=2, choices=LANGS, default="en")
    title = models.CharField(max_length=120)
    event = models.CharField(max_length=20, choices=EVENTS, default="custom")
    body = models.TextField(help_text="Use placeholders like {member_name}. See the list beside the editor.")
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["event", "key", "language"]
        constraints = [models.UniqueConstraint(fields=["key", "language"], name="uniq_template_key_lang")]

    def __str__(self):
        return f"{self.title} [{self.language}]"


class Campaign(models.Model):
    AUDIENCES = [
        ("all", "All active members"),
        ("dues", "Members with dues"),
        ("selected", "Selected members"),
    ]
    title = models.CharField(max_length=160)
    body = models.TextField()
    audience = models.CharField(max_length=10, choices=AUDIENCES, default="all")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    total = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.title

    @property
    def counts(self):
        rows = dict(self.messages.values_list("status").annotate(n=models.Count("id")))
        return {k: rows.get(k, 0) for k in ("queued", "sent", "failed", "skipped")}


class SMSMessage(models.Model):
    STATUS = [("queued", "Queued"), ("sent", "Sent"), ("failed", "Failed"), ("skipped", "Skipped")]

    member = models.ForeignKey("myapp.SocietyMember", null=True, blank=True, on_delete=models.SET_NULL, related_name="sms_messages")
    phone = models.CharField(max_length=20, blank=True)
    body = models.TextField()
    encoding = models.CharField(max_length=8, default="gsm")
    segments = models.PositiveSmallIntegerField(default=1)

    status = models.CharField(max_length=8, choices=STATUS, default="queued", db_index=True)
    trigger = models.CharField(max_length=30, db_index=True)
    related_type = models.CharField(max_length=30, blank=True)
    related_id = models.CharField(max_length=30, blank=True)
    campaign = models.ForeignKey(Campaign, null=True, blank=True, on_delete=models.SET_NULL, related_name="messages")

    provider = models.CharField(max_length=20, blank=True)
    provider_response = models.TextField(blank=True)
    error = models.CharField(max_length=500, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            # one automatic message per (event, record, number): the guarantee that
            # a payment is announced exactly once however it was entered.
            models.UniqueConstraint(
                fields=["trigger", "related_type", "related_id", "phone"],
                condition=~Q(related_type=""),
                name="uniq_auto_sms_per_record",
            ),
        ]

    def __str__(self):
        return f"{self.trigger} -> {self.phone or self.member} ({self.status})"
