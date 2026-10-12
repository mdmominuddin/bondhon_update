from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from myapp import services
from django.urls import reverse

from myapp.permissions import ROLE_ADMIN, ROLE_AUDITOR, admin_required, auditor_required, finance_required, has_role, office_required
from myapp.views import csv_response

from . import checks
from .forms import (
    AdHocRangeForm, CloseForm, EngagementForm, FindingForm, LogFilterForm, ManagementResponseForm, ReconciliationForm,
)
from .models import AuditEngagement, AuditFinding, AuditLog, ReconciliationEntry


# ---------------------------------------------------------------------------
# overview + trail
# ---------------------------------------------------------------------------
@auditor_required
def overview(request):
    today = timezone.localdate()
    start = date(today.year, 1, 1)
    results = checks.run_all(start, today)
    ok, n_checked, bad = AuditLog.verify_chain()
    ctx = {
        "page_title": "Audit overview", "results": [r for r in results if not r.passed][:8], "summary": checks.summarize(results),
        "period": (start, today), "chain_ok": ok, "chain_checked": n_checked, "chain_bad": bad,
        "engagements": AuditEngagement.objects.all()[:5],
        "recent": AuditLog.objects.all()[:8],
        "open_findings": AuditFinding.objects.filter(status="open").count(),
        "log_total": AuditLog.objects.count(),
    }
    return render(request, "audit/overview.html", ctx)


@auditor_required
def log_list(request):
    form = LogFilterForm(request.GET or None)
    qs = AuditLog.objects.select_related("user")
    if form.is_valid():
        d = form.cleaned_data
        if d["q"]:
            qs = qs.filter(Q(username__icontains=d["q"]) | Q(object_repr__icontains=d["q"]) | Q(message__icontains=d["q"]) | Q(object_id=d["q"]))
        if d["action"]:
            qs = qs.filter(action=d["action"])
        if d["model"]:
            qs = qs.filter(model=d["model"])
        if d["start"]:
            qs = qs.filter(timestamp__date__gte=d["start"])
        if d["end"]:
            qs = qs.filter(timestamp__date__lte=d["end"])
    if request.GET.get("format") == "csv":
        return csv_response("audit-trail.csv", ["time", "user", "ip", "action", "record", "id", "description", "message", "hash"],
                            [[l.timestamp.strftime("%Y-%m-%d %H:%M:%S"), l.username, l.ip_address, l.action, l.model, l.object_id,
                              l.object_repr, l.message, l.hash] for l in qs[:20000]])
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    return render(request, "audit/log_list.html", {"page_title": "Audit trail", "form": form, "page": page, "params": request.GET})


@auditor_required
def log_detail(request, pk):
    entry = get_object_or_404(AuditLog, pk=pk)
    return render(request, "audit/log_detail.html", {"page_title": f"Audit entry #{entry.pk}", "entry": entry})


@auditor_required
@require_POST
def verify_chain(request):
    ok, checked, bad = AuditLog.verify_chain()
    if ok:
        messages.success(request, f"Audit trail intact: {checked} entries verified, no gaps or alterations.")
    else:
        messages.error(request, f"INTEGRITY FAILURE: entry #{bad} does not match its hash chain ({checked} entries verified before it).")
    return redirect(request.POST.get("next") or "audit_overview")


@admin_required
@require_POST
def rebuild_summaries(request):
    services.rebuild_monthly_summary()
    AuditLog.record("system", message="Monthly summary table rebuilt from the ledger")
    messages.success(request, "Stored monthly summaries rebuilt from the ledger.")
    return redirect("audit_overview")


@auditor_required
def adhoc_checks(request):
    today = timezone.localdate()
    form = AdHocRangeForm(request.GET or {"start": date(today.year, 1, 1), "end": today})
    results = []
    if form.is_valid():
        results = checks.run_all(form.cleaned_data["start"], form.cleaned_data["end"])
    return render(request, "audit/checks.html", {"page_title": "Audit analytics", "form": form, "results": results,
                                                 "summary": checks.summarize(results) if results else None})


# ---------------------------------------------------------------------------
# engagements
# ---------------------------------------------------------------------------
@office_required
def engagement_list(request):
    return render(request, "audit/engagement_list.html", {"page_title": "Audit engagements", "engagements": AuditEngagement.objects.all()})


@auditor_required
def engagement_form(request, pk=None):
    engagement = get_object_or_404(AuditEngagement, pk=pk) if pk else None
    if engagement and engagement.locked:
        messages.error(request, "A closed audit cannot be edited. An administrator can re-open it.")
        return redirect("audit_engagement", pk=pk)
    form = EngagementForm(request.POST or None, instance=engagement, initial={"lead_auditor": request.user.pk} if not engagement else None)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        if not engagement:
            obj.status = "fieldwork"
            obj.save()
        messages.success(request, "Engagement saved.")
        return redirect("audit_engagement", pk=obj.pk)
    return render(request, "audit/engagement_form.html", {"page_title": "Edit engagement" if engagement else "New audit engagement", "form": form, "engagement": engagement})


def _engagement_context(engagement):
    ledger = services.ledger_between(engagement.period_start, engagement.period_end)
    recon = list(engagement.reconciliations.all())
    counted = sum((r.amount for r in recon), 0)
    book = ledger["closing"] if not engagement.locked else __import__("decimal").Decimal(engagement.closing_snapshot.get("closing", ledger["closing"]))
    return {"engagement": engagement, "ledger": ledger, "recon": recon, "counted": counted, "book": book, "variance": counted - book}


@office_required
def engagement_detail(request, pk):
    engagement = get_object_or_404(AuditEngagement, pk=pk)
    tab = request.GET.get("tab", "overview")
    ctx = _engagement_context(engagement)
    ctx.update({
        "page_title": engagement.title, "tab": tab,
        "tabs": [("overview", "Overview"), ("checks", "Analytics"), ("findings", f"Findings ({engagement.findings.count()})"),
                 ("reconciliation", "Reconciliation"), ("close", "Sign-off")],
        "findings": engagement.findings.select_related("created_by"),
        "recon_form": ReconciliationForm(initial={"counted_on": timezone.localdate()}),
        "close_form": CloseForm(initial={"opinion": engagement.opinion or None, "conclusion": engagement.conclusion}),
        "last_summary": engagement.last_check_summary,
    })
    if tab == "checks" and has_role(request.user, ROLE_ADMIN, ROLE_AUDITOR):
        results = checks.run_all(engagement.period_start, engagement.period_end)
        ctx["results"] = results
        ctx["summary"] = checks.summarize(results)
    return render(request, "audit/engagement_detail.html", ctx)


@auditor_required
@require_POST
def engagement_run_checks(request, pk):
    engagement = get_object_or_404(AuditEngagement, pk=pk)
    results = checks.run_all(engagement.period_start, engagement.period_end)
    engagement.last_check_run = timezone.now()
    engagement.last_check_summary = checks.summarize(results)
    AuditEngagement.objects.filter(pk=pk).update(last_check_run=engagement.last_check_run, last_check_summary=engagement.last_check_summary)
    messages.success(request, f"Analytics run: {engagement.last_check_summary['exceptions']} exception(s) across {engagement.last_check_summary['total'] - engagement.last_check_summary['passed']} test(s).")
    return redirect(reverse("audit_engagement", args=[pk]) + "?tab=checks")


@auditor_required
@require_POST
def raise_finding(request, pk):
    engagement = get_object_or_404(AuditEngagement, pk=pk)
    if engagement.locked:
        messages.error(request, "This audit is closed.")
        return redirect("audit_engagement", pk=pk)
    code = request.POST.get("code", "")
    result = next((r for r in checks.run_all(engagement.period_start, engagement.period_end) if r.code == code and not r.passed), None)
    if not result:
        raise Http404("That test has no exceptions.")
    lines = "\n".join(f"- {i['ref']}: {i['detail']}" for i in result.items[:15])
    more = f"\n... and {result.count - 15} more." if result.count > 15 else ""
    AuditFinding.objects.create(
        engagement=engagement, title=result.title, severity=result.severity if result.severity != "info" else "low",
        category="data" if code in ("BAD_PHONE",) else "control", check_code=code,
        description=f"{result.description}\n\n{result.count} exception(s):\n{lines}{more}",
        recommendation="", created_by=request.user,
    )
    messages.success(request, "Finding raised from analytics. Review and add a recommendation.")
    return redirect(f"/audit/engagements/{pk}/?tab=findings")


@auditor_required
def finding_form(request, engagement_pk=None, pk=None):
    finding = get_object_or_404(AuditFinding, pk=pk) if pk else None
    engagement = finding.engagement if finding else get_object_or_404(AuditEngagement, pk=engagement_pk)
    if engagement.locked:
        messages.error(request, "This audit is closed - findings can no longer be edited.")
        return redirect("audit_engagement", pk=engagement.pk)
    form = FindingForm(request.POST or None, instance=finding)
    if request.method == "POST" and form.is_valid():
        f = form.save(commit=False)
        f.engagement = engagement
        if not f.created_by_id:
            f.created_by = request.user
        f.save()
        messages.success(request, "Finding saved.")
        return redirect(f"/audit/engagements/{engagement.pk}/?tab=findings")
    return render(request, "audit/finding_form.html", {"page_title": "Edit finding" if finding else "New finding", "form": form, "engagement": engagement, "finding": finding})


@finance_required
def finding_respond(request, pk):
    finding = get_object_or_404(AuditFinding, pk=pk)
    form = ManagementResponseForm(request.POST or None, instance=finding)
    if request.method == "POST" and form.is_valid():
        f = form.save(commit=False)
        f.resolved_at = timezone.now() if f.status in ("resolved", "accepted") else None
        f.save()
        messages.success(request, "Management response recorded.")
        return redirect(f"/audit/engagements/{finding.engagement_id}/?tab=findings")
    return render(request, "audit/finding_respond.html", {"page_title": "Respond to finding", "form": form, "finding": finding})


@auditor_required
@require_POST
def reconciliation_add(request, pk):
    engagement = get_object_or_404(AuditEngagement, pk=pk)
    if engagement.locked:
        messages.error(request, "This audit is closed.")
    else:
        form = ReconciliationForm(request.POST)
        if form.is_valid():
            r = form.save(commit=False)
            r.engagement = engagement
            r.save()
            messages.success(request, "Physical count recorded.")
        else:
            messages.error(request, "Could not save the count: " + "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
    return redirect(f"/audit/engagements/{pk}/?tab=reconciliation")


@auditor_required
@require_POST
def reconciliation_delete(request, pk):
    entry = get_object_or_404(ReconciliationEntry, pk=pk)
    if entry.engagement.locked:
        messages.error(request, "This audit is closed.")
    else:
        entry.delete()
        messages.success(request, "Count removed.")
    return redirect(f"/audit/engagements/{entry.engagement_id}/?tab=reconciliation")


@auditor_required
@require_POST
def engagement_close(request, pk):
    engagement = get_object_or_404(AuditEngagement, pk=pk)
    if engagement.locked:
        return redirect("audit_engagement", pk=pk)
    form = CloseForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please choose an opinion, write a conclusion and tick the confirmation to close the audit.")
        return redirect(f"/audit/engagements/{pk}/?tab=close")
    if engagement.period_end > timezone.localdate():
        messages.error(request, "The audit period has not ended yet - it cannot be closed.")
        return redirect(f"/audit/engagements/{pk}/?tab=close")
    results = checks.run_all(engagement.period_start, engagement.period_end)
    ledger = services.ledger_between(engagement.period_start, engagement.period_end)
    engagement.opinion = form.cleaned_data["opinion"]
    engagement.conclusion = form.cleaned_data["conclusion"]
    engagement.closing_snapshot = {k: str(v) for k, v in ledger.items()} | {"checks": checks.summarize(results)}
    engagement.last_check_run, engagement.last_check_summary = timezone.now(), checks.summarize(results)
    engagement.status, engagement.locked = "closed", True
    engagement.closed_at, engagement.closed_by = timezone.now(), request.user
    engagement.save()
    messages.success(request, "Audit closed and signed off. Records dated in this period are now locked.")
    return redirect("audit_engagement_report", pk=pk)


@admin_required
@require_POST
def engagement_reopen(request, pk):
    engagement = get_object_or_404(AuditEngagement, pk=pk)
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "A reason is required to re-open a closed audit.")
        return redirect("audit_engagement", pk=pk)
    engagement.status, engagement.locked = "fieldwork", False
    engagement.save()
    AuditLog.record("system", obj=engagement, message=f"Audit re-opened by administrator. Reason: {reason}")
    messages.warning(request, "Audit re-opened. Records in the period can be changed again until it is re-closed.")
    return redirect("audit_engagement", pk=pk)


@office_required
def engagement_report(request, pk):
    engagement = get_object_or_404(AuditEngagement, pk=pk)
    ctx = _engagement_context(engagement)
    results = checks.run_all(engagement.period_start, engagement.period_end)
    ctx.update({
        "page_title": f"Audit report - {engagement.title}", "findings": sorted(engagement.findings.all(), key=lambda f: AuditFinding.SEVERITY_ORDER[f.severity]),
        "summary": checks.summarize(results), "failed": [r for r in results if not r.passed],
        "chain": AuditLog.verify_chain(),
    })
    return render(request, "audit/report.html", ctx)
