"""
Centralised messaging.  Everything that sends an SMS - automatic payment
receipts, reminders, one-off custom messages, campaigns - goes through here.
"""
import logging
import re
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from myapp.models import Notification, SiteSetting, SocietyMember
from myapp.services import member_position, society_totals

from .backends import SMSBackendError, get_backend
from .models import MessageTemplate, SMSMessage
from .phone import normalize_bd_phone

log = logging.getLogger("bondhon.sms")

# --------------------------------------------------------------------------
# Placeholders
# --------------------------------------------------------------------------
PLACEHOLDERS = {
    "member_name": "Member's name",
    "member_code": "Member code, e.g. M-007",
    "society_name": "Society name",
    "amount": "Amount of this payment (e.g. 5,000)",
    "date": "Payment date",
    "receipt_no": "Receipt number",
    "method": "Payment method",
    "total_paid": "Member's total paid to date",
    "due": "Member's outstanding due",
    "advance": "Advance paid beyond the due",
    "expected": "Member's total expected contribution",
    "balance": "Society balance",
    "reference": "Payment reference / TrxID",
    "reason": "Reason (rejections)",
    "username": "Portal username",
    "temp_password": "Temporary password",
    "login_url": "Portal address",
}

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def render(body, context):
    """Safe {placeholder} substitution - unknown keys are left blank, no attribute access."""
    return _PLACEHOLDER_RE.sub(lambda m: str(context.get(m.group(1), "")), body)


def _fmt(v):
    v = Decimal(v)
    return f"{v:,.0f}" if v == v.to_integral() else f"{v:,.2f}"


def member_context(member, **extra):
    site = SiteSetting.get()
    pos = member_position(member)
    ctx = {
        "member_name": member.name, "member_code": member.code,
        "society_name": site.society_name_bn if site.sms_language == "bn" and site.society_name_bn else site.society_name,
        "total_paid": _fmt(pos["paid"]), "due": _fmt(pos["due"]), "advance": _fmt(pos["advance"]),
        "expected": _fmt(pos["expected"]), "balance": _fmt(society_totals()["balance"]),
    }
    ctx.update(extra)
    return ctx


# --------------------------------------------------------------------------
# Length / encoding
# --------------------------------------------------------------------------
_GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
_GSM7_EXT = set("^{}\\[~]|€")


def analyze(body):
    """Return (encoding, length, segments) following GSM-7 / UCS-2 rules."""
    if all(c in _GSM7 or c in _GSM7_EXT for c in body):
        length = sum(2 if c in _GSM7_EXT else 1 for c in body)
        single, multi, enc = 160, 153, "gsm"
    else:
        length = len(body)
        single, multi, enc = 70, 67, "unicode"
    if length == 0:
        return enc, 0, 0
    segments = 1 if length <= single else -(-length // multi)
    return enc, length, segments


# --------------------------------------------------------------------------
# Queue + dispatch
# --------------------------------------------------------------------------
def queue_sms(*, body, member=None, phone="", trigger, related=None, campaign=None, created_by=None):
    """
    Create an outbox row.  Returns the SMSMessage, or None when this exact
    automatic message was already queued for this record (idempotency).
    Rows that cannot be delivered are stored as 'skipped' with the reason.
    """
    site = SiteSetting.get()
    number = normalize_bd_phone(phone or (member.phone_number if member else ""))
    enc, _, segments = analyze(body)
    status, error = "queued", ""
    if not site.sms_enabled:
        status, error = "skipped", "SMS is switched off in Settings."
    elif member is not None and not member.sms_opt_in:
        status, error = "skipped", "Member has opted out of SMS."
    elif not number:
        status, error = "skipped", f"No valid mobile number ('{phone or (member.phone_number if member else '')}')."
    rel_type, rel_id = related if related else ("", "")
    try:
        with transaction.atomic():
            return SMSMessage.objects.create(
                member=member, phone=number or (phone or "")[:20], body=body, encoding=enc, segments=segments,
                status=status, error=error, trigger=trigger, related_type=rel_type, related_id=str(rel_id),
                campaign=campaign, created_by=created_by,
            )
    except IntegrityError:
        return None


def dispatch(sms):
    """Attempt delivery of one queued/failed message and record the outcome."""
    if sms.status not in ("queued", "failed"):
        return sms
    site = SiteSetting.get()
    backend = get_backend(site.sms_backend)
    sms.attempts += 1
    sms.provider = backend.name
    try:
        ok, provider_id, raw = backend.send(sms.phone, sms.body, site.sms_sender_id)
        sms.provider_response = raw
        if ok:
            sms.status, sms.error, sms.sent_at = "sent", "", timezone.now()
        else:
            sms.status, sms.error = "failed", "Provider did not confirm acceptance."
    except SMSBackendError as exc:
        sms.status, sms.error = "failed", str(exc)[:500]
    except Exception as exc:  # never let SMS break a financial transaction
        log.exception("Unexpected SMS error")
        sms.status, sms.error = "failed", f"Unexpected error: {exc}"[:500]
    sms.save()
    return sms


def dispatch_queued(limit=100, include_failed=False):
    statuses = ["queued", "failed"] if include_failed else ["queued"]
    sent = failed = 0
    for sms in SMSMessage.objects.filter(status__in=statuses).order_by("id")[:limit]:
        dispatch(sms)
        sent += sms.status == "sent"
        failed += sms.status == "failed"
    return sent, failed


def _template_for(key):
    lang = SiteSetting.get().sms_language
    qs = MessageTemplate.objects.filter(key=key, is_active=True)
    return qs.filter(language=lang).first() or qs.first()


def send_event(key, member, *, related=None, created_by=None, in_app=True, **extra):
    """
    Send the active template for `key` to `member`.  Safe to call from signal
    handlers: it never raises.  Returns the SMSMessage (or None).
    """
    try:
        template = _template_for(key)
        if template is None:
            return None
        ctx = member_context(member, **extra)
        body = render(template.body, ctx)
        sms = queue_sms(body=body, member=member, trigger=key, related=related, created_by=created_by)
        if sms is None:
            return None
        if in_app and member.user_id:
            Notification.objects.create(member=member, title=template.title, message=body, kind=key)
        if sms.status == "queued":
            dispatch(sms)
        return sms
    except Exception:
        log.exception("send_event(%s) failed for member %s", key, getattr(member, "pk", None))
        return None


def send_custom(member, body, *, created_by=None, campaign=None, trigger="custom", dispatch_now=True, **extra):
    ctx = member_context(member, **extra)
    sms = queue_sms(body=render(body, ctx), member=member, trigger=trigger, campaign=campaign, created_by=created_by)
    if sms and sms.status == "queued" and dispatch_now:
        dispatch(sms)
    return sms


def campaign_audience(audience, member_ids=None):
    from myapp.services import member_positions
    members = SocietyMember.objects.filter(is_active=True)
    if audience == "selected":
        return list(members.filter(pk__in=member_ids or []))
    if audience == "dues":
        return [p["member"] for p in member_positions(members) if p["due"] > 0]
    return list(members)
