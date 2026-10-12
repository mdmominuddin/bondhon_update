"""Automatic audit trail for every tracked model, plus authentication events."""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import AuditLog

User = get_user_model()

TRACKED = {
    "myapp.SocietyMember", "myapp.Deposit", "myapp.Expense", "myapp.ExpenseHead",
    "myapp.ContributionPlan", "myapp.BuildingProject", "myapp.SiteSetting",
    "auth.User", "payments.OnlinePayment", "messaging.MessageTemplate", "messaging.Campaign",
    "audit.AuditEngagement", "audit.AuditFinding", "audit.ReconciliationEntry",
}
SKIP_FIELDS = {"last_login", "updated_at", "last_check_run", "last_check_summary", "raw_response"}
SECRET_FIELDS = {"password"}


def _ser(value):
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return str(value)


def _snapshot(obj):
    data = {}
    for f in obj._meta.concrete_fields:
        if f.name in SKIP_FIELDS:
            continue
        value = getattr(obj, f.attname)
        if isinstance(value, Decimal) and getattr(f, "decimal_places", None) is not None:
            value = f"{value:.{f.decimal_places}f}"  # same text whether it came from a form or the database
        data[f.name] = "(hidden)" if f.name in SECRET_FIELDS and value else _ser(value)
    return data


def _tracked(sender):
    return sender._meta.label in TRACKED


@receiver(pre_save)
def capture_old(sender, instance, raw=False, **kwargs):
    if raw or not _tracked(sender) or instance.pk is None:
        return
    old = sender._default_manager.filter(pk=instance.pk).first()
    instance._audit_old = _snapshot(old) if old else None
    if old is not None and "password" in {f.name for f in sender._meta.concrete_fields}:
        instance._audit_old_pw = old.password


@receiver(post_save)
def log_save(sender, instance, created, raw=False, **kwargs):
    if raw or not _tracked(sender):
        return
    new = _snapshot(instance)
    if created or getattr(instance, "_audit_old", None) is None:
        changes = {k: [None, v] for k, v in new.items() if v not in (None, "", False)}
        AuditLog.record("create", obj=instance, changes=changes)
        return
    old = instance._audit_old
    changes = {k: [old.get(k), v] for k, v in new.items() if old.get(k) != v}
    if hasattr(instance, "_audit_old_pw") and instance._audit_old_pw != instance.password:
        changes["password"] = ["(hidden)", "(changed)"]
    if not changes:
        return
    action = "update"
    if "is_void" in changes and changes["is_void"][1] is True:
        action = "void"
    elif "status" in changes and sender._meta.label == "myapp.Expense":
        action = {"approved": "approve", "rejected": "reject"}.get(changes["status"][1], "update")
    elif "locked" in changes and sender._meta.label == "audit.AuditEngagement":
        action = "lock" if changes["locked"][1] else "unlock"
    AuditLog.record(action, obj=instance, changes=changes)


@receiver(post_delete)
def log_delete(sender, instance, **kwargs):
    if not _tracked(sender):
        return
    AuditLog.record("delete", obj=instance,
                    changes={k: [v, None] for k, v in _snapshot(instance).items() if v not in (None, "", False)})


@receiver(m2m_changed, sender=User.groups.through)
def log_group_change(sender, instance, action, pk_set, reverse, **kwargs):
    if action not in ("post_add", "post_remove", "post_clear") or reverse:
        return
    names = list(Group.objects.filter(pk__in=pk_set or []).values_list("name", flat=True))
    verb = {"post_add": "added to", "post_remove": "removed from", "post_clear": "cleared from"}[action]
    AuditLog.record("role", obj=instance, message=f"{instance.username} {verb} role(s): {', '.join(names) or 'all'}")


@receiver(user_logged_in)
def log_login(sender, request, user, **kwargs):
    AuditLog.record("login", model="auth.User", object_id=user.pk, object_repr=user.username, user=user,
                    message="Signed in")


@receiver(user_logged_out)
def log_logout(sender, request, user, **kwargs):
    if user:
        AuditLog.record("logout", model="auth.User", object_id=user.pk, object_repr=user.username, user=user,
                        message="Signed out")


@receiver(user_login_failed)
def log_login_failed(sender, credentials, request=None, **kwargs):
    AuditLog.record("login_failed", model="auth.User", object_repr=str(credentials.get("username", ""))[:150],
                    message="Failed sign-in attempt")
