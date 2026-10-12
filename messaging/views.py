from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from audit.models import AuditLog
from myapp.models import SocietyMember
from myapp.permissions import finance_required, office_required

from . import services
from .forms import ComposeForm, TemplateForm
from .models import Campaign, MessageTemplate, SMSMessage


@office_required
def home(request):
    now = timezone.now()
    week = now - timedelta(days=7)
    stats = dict(SMSMessage.objects.filter(created_at__gte=week).values_list("status").annotate(n=Count("id")))
    from myapp.models import SiteSetting
    site = SiteSetting.get()
    from django.conf import settings
    ctx = {
        "page_title": "Message center", "stats": {k: stats.get(k, 0) for k in ("sent", "queued", "failed", "skipped")},
        "recent": SMSMessage.objects.select_related("member")[:8], "campaigns": Campaign.objects.all()[:5],
        "templates": MessageTemplate.objects.filter(is_system=True, language=site.sms_language),
        "site": site, "http_ready": bool(settings.SMS_HTTP_URL and settings.SMS_HTTP_PARAMS),
        "no_phone": sum(1 for m in SocietyMember.objects.filter(is_active=True) if not m.has_valid_phone),
    }
    return render(request, "messaging/home.html", ctx)


@office_required
def outbox(request):
    qs = SMSMessage.objects.select_related("member")
    status, trigger, q = request.GET.get("status", ""), request.GET.get("trigger", ""), request.GET.get("q", "").strip()
    if status:
        qs = qs.filter(status=status)
    if trigger:
        qs = qs.filter(trigger=trigger)
    if q:
        qs = qs.filter(Q(phone__icontains=q) | Q(body__icontains=q) | Q(member__name__icontains=q))
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    triggers = SMSMessage.objects.values_list("trigger", flat=True).distinct().order_by("trigger")
    return render(request, "messaging/outbox.html", {"page_title": "SMS outbox", "page": page, "status": status, "trigger": trigger, "q": q,
                                                      "triggers": triggers, "statuses": SMSMessage.STATUS, "params": request.GET})


@office_required
def sms_detail(request, pk):
    sms = get_object_or_404(SMSMessage.objects.select_related("member", "campaign", "created_by"), pk=pk)
    return render(request, "messaging/sms_detail.html", {"page_title": "SMS detail", "sms": sms})


@finance_required
@require_POST
def sms_retry(request, pk):
    sms = get_object_or_404(SMSMessage, pk=pk)
    if sms.status in ("failed", "queued"):
        services.dispatch(sms)
        (messages.success if sms.status == "sent" else messages.error)(request, f"SMS {sms.status}." + (f" {sms.error}" if sms.error else ""))
    return redirect("sms_detail", pk=pk)


@finance_required
@require_POST
def process_queue(request):
    sent, failed = services.dispatch_queued(limit=200, include_failed=request.POST.get("retry_failed") == "1")
    AuditLog.record("sms", message=f"Outbox processed manually: {sent} sent, {failed} failed")
    messages.success(request, f"Outbox processed: {sent} sent, {failed} failed.")
    return redirect("sms_outbox")


@finance_required
def compose(request):
    initial = {"audience": request.GET.get("audience", "selected")}
    if request.GET.get("member"):
        initial["members"] = [request.GET["member"]]
    if request.GET.get("template"):
        t = MessageTemplate.objects.filter(pk=request.GET["template"]).first()
        if t:
            initial.update(template=t.pk, body=t.body, title=t.title)
    if initial["audience"] == "dues" and "body" not in initial:
        t = services._template_for("due_reminder")
        if t:
            initial.update(template=t.pk, body=t.body, title="Dues reminder")
    form = ComposeForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        audience = services.campaign_audience(d["audience"], [m.pk for m in d["members"]])
        campaign = Campaign.objects.create(title=d["title"] or d["body"][:60], body=d["body"], audience=d["audience"], created_by=request.user, total=len(audience))
        for member in audience:
            sms = services.send_custom(member, d["body"], created_by=request.user, campaign=campaign, trigger="campaign")
        c = campaign.counts
        AuditLog.record("sms", obj=campaign, message=f"Campaign '{campaign.title}' to {len(audience)} member(s): {c['sent']} sent, {c['failed']} failed, {c['skipped']} skipped")
        (messages.success if not c["failed"] else messages.warning)(
            request, f"Message processed for {len(audience)} member(s): {c['sent']} sent, {c['failed']} failed, {c['skipped']} skipped.")
        return redirect("campaign_detail", pk=campaign.pk)
    sample = SocietyMember.objects.filter(is_active=True).first()
    return render(request, "messaging/compose.html", {"page_title": "Compose message", "form": form, "placeholders": services.PLACEHOLDERS,
                                                      "templates_json": {str(t.pk): t.body for t in MessageTemplate.objects.filter(is_active=True)},
                                                      "sample": sample, "test_mode": __import__("myapp.models", fromlist=["SiteSetting"]).SiteSetting.get().sms_backend == "console"})


@office_required
@require_GET
def preview(request):
    body = request.GET.get("body", "")
    member = SocietyMember.objects.filter(pk=request.GET.get("member")).first() or SocietyMember.objects.filter(is_active=True).first()
    ctx = services.member_context(member, amount="5,000", date=timezone.localdate().strftime("%d-%m-%Y"), receipt_no="BND-2026-00001",
                                  method="bKash", reference="8N7A6D5E", reason="amount mismatch", username="01712345678",
                                  temp_password="••••••••", login_url="https://example.org") if member else {}
    text = services.render(body, ctx)
    enc, length, segments = services.analyze(text)
    return JsonResponse({"text": text, "encoding": enc, "length": length, "segments": segments})


@office_required
def campaign_list(request):
    page = Paginator(Campaign.objects.select_related("created_by"), 20).get_page(request.GET.get("page"))
    return render(request, "messaging/campaigns.html", {"page_title": "Sent messages", "page": page})


@office_required
def campaign_detail(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    return render(request, "messaging/campaign_detail.html", {"page_title": campaign.title, "campaign": campaign, "counts": campaign.counts,
                                                              "messages_list": campaign.messages.select_related("member")})


@office_required
def template_list(request):
    return render(request, "messaging/templates.html", {"page_title": "Message templates", "templates": MessageTemplate.objects.all(),
                                                        "placeholders": services.PLACEHOLDERS})


@finance_required
def template_form(request, pk=None):
    template = get_object_or_404(MessageTemplate, pk=pk) if pk else None
    form = TemplateForm(request.POST or None, instance=template)
    if request.method == "POST" and form.is_valid():
        t = form.save()
        messages.success(request, "Template saved. New messages use it immediately.")
        return redirect("template_list")
    return render(request, "messaging/template_form.html", {"page_title": "Edit template" if template else "New template", "form": form,
                                                            "placeholders": services.PLACEHOLDERS, "template": template})
