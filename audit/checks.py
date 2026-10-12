"""
Audit analytics: automated tests over the books.  Every check returns a
CheckResult; `run_all()` returns them worst-first.  Each is deterministic and
read-only so an auditor can re-run it at any time.
"""
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.apps import apps
from django.db.models import Count, F, Q, Sum
from django.urls import reverse
from django.utils import timezone

from myapp.models import (
    Deposit, Expense, MonthlyFinancialSummary, SiteSetting, SocietyMember,
)
from myapp.services import monthly_ledger

from .models import AuditLog

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
MAX_ITEMS = 60


@dataclass
class CheckResult:
    code: str
    title: str
    severity: str
    description: str
    items: list = field(default_factory=list)
    count: int = 0

    @property
    def passed(self):
        return self.count == 0


def _item(ref, detail, url=""):
    return {"ref": ref, "detail": detail, "url": url}


def _result(code, title, severity, description, items):
    return CheckResult(code, title, severity, description, items[:MAX_ITEMS], len(items))


def _money(v):
    return f"Tk {Decimal(v):,.2f}"


def check_duplicate_deposits(start, end):
    rows = (Deposit.objects.valid().filter(date__range=(start, end))
            .values("member", "member__name", "date", "amount").annotate(n=Count("id")).filter(n__gt=1))
    items = [_item(f"{r['member__name']} - {r['date']:%d-%m-%Y}", f"{r['n']} deposits of {_money(r['amount'])} on the same day") for r in rows]
    return _result("DUP_DEPOSIT", "Possible duplicate deposits", "medium",
                   "Same member, date and amount recorded more than once.", items)


def check_duplicate_references(start, end):
    rows = (Deposit.objects.valid().filter(date__range=(start, end)).exclude(reference="")
            .values("method", "reference").annotate(n=Count("id")).filter(n__gt=1))
    items = [_item(f"{r['method']}: {r['reference']}", f"Transaction reference used on {r['n']} deposits") for r in rows]
    return _result("DUP_REF", "Reused transaction references", "high",
                   "The same bKash/bank/cheque reference backs more than one deposit.", items)


def check_undocumented_expenses(start, end):
    qs = Expense.objects.valid().filter(date__range=(start, end), voucher_no="", attachment="").filter(Q(remark="") | Q(remark__iexact="nil"))
    items = [_item(f"{e.date:%d-%m-%Y} {e.description}", f"{_money(e.amount)} - no voucher number, attachment or remark",
                   reverse("expense_detail", args=[e.pk])) for e in qs]
    return _result("UNDOCUMENTED", "Expenses without supporting evidence", "medium",
                   "Approved expenses with no voucher number, attached bill or remark.", items)


def check_large_expenses(start, end):
    limit = SiteSetting.get().large_expense_threshold
    qs = Expense.objects.valid().filter(date__range=(start, end), amount__gte=limit)
    items = [_item(f"{e.date:%d-%m-%Y} {e.description}", f"{_money(e.amount)} (threshold {_money(limit)})",
                   reverse("expense_detail", args=[e.pk])) for e in qs]
    return _result("LARGE_EXPENSE", "Large expenses", "low",
                   "Expenses at or above the configured threshold - confirm authorisation and evidence.", items)


def check_self_approved(start, end):
    qs = Expense.objects.valid().filter(date__range=(start, end), recorded_by__isnull=False, recorded_by=F("approved_by"))
    items = [_item(f"{e.date:%d-%m-%Y} {e.description}", f"{_money(e.amount)} recorded and approved by {e.recorded_by}",
                   reverse("expense_detail", args=[e.pk])) for e in qs.select_related("recorded_by")]
    return _result("SELF_APPROVED", "Segregation of duties", "low",
                   "Expenses recorded and approved by the same person (acceptable only if the society has a single administrator).", items)


def check_backdated(start, end, days=30):
    items = []
    for label, Model, url in (("Deposit", Deposit, "deposit_detail"), ("Expense", Expense, "expense_detail")):
        for o in Model.objects.valid().filter(date__range=(start, end), created_at__isnull=False):
            lag = (o.created_at.date() - o.date).days
            if lag > days:
                items.append(_item(f"{label} {o.pk} dated {o.date:%d-%m-%Y}", f"entered {lag} days later ({o.created_at:%d-%m-%Y}) - {_money(o.amount)}", reverse(url, args=[o.pk])))
    return _result("BACKDATED", "Late-entered (back-dated) records", "medium",
                   f"Records entered more than {days} days after their transaction date.", items)


def check_voided(start, end):
    items = []
    for label, Model, url in (("Deposit", Deposit, "deposit_detail"), ("Expense", Expense, "expense_detail")):
        for o in Model.objects.filter(date__range=(start, end), is_void=True).select_related("voided_by"):
            items.append(_item(f"{label} {o.pk} dated {o.date:%d-%m-%Y}", f"{_money(o.amount)} voided by {o.voided_by or '?'}: {o.void_reason}", reverse(url, args=[o.pk])))
    return _result("VOIDED", "Voided records", "low", "Every voided record - review reasons.", items)


def check_material_edits(start, end):
    items = []
    logs = AuditLog.objects.filter(action="update", model__in=["myapp.Deposit", "myapp.Expense"])
    for log in logs:
        keys = set(log.changes) & {"amount", "date", "member", "fund_head", "expense_head"}
        if not keys:
            continue
        Model = apps.get_model(log.model)
        obj_date = Model.objects.filter(pk=log.object_id).values_list("date", flat=True).first()
        if obj_date and start <= obj_date <= end:
            detail = "; ".join(f"{k}: {log.changes[k][0]} -> {log.changes[k][1]}" for k in sorted(keys))
            items.append(_item(f"{log.model.split('.')[1]} {log.object_id} edited {log.timestamp:%d-%m-%Y}", f"by {log.username or 'system'} - {detail}", reverse("audit_log_detail", args=[log.pk])))
    return _result("MATERIAL_EDIT", "Amounts or dates changed after posting", "high",
                   "Edits to amount, date, member or head, taken from the audit trail.", items)


def check_summary_mismatch(start, end):
    ledger = {r["month"]: r for r in monthly_ledger() if start.replace(day=1) <= r["month"] <= end}
    snap = {s.month: s for s in MonthlyFinancialSummary.objects.filter(month__range=(start.replace(day=1), end))}
    items = []
    for m in sorted(set(ledger) | set(snap)):
        l, s = ledger.get(m), snap.get(m)
        ld, le = (l["deposits"], l["expenses"]) if l else (Decimal(0), Decimal(0))
        sd, se = (s.total_deposits, s.total_expenses) if s else (Decimal(0), Decimal(0))
        if ld != sd or le != se:
            items.append(_item(f"{m:%B %Y}", f"Ledger: deposits {_money(ld)}, expenses {_money(le)} | Stored summary: deposits {_money(sd)}, expenses {_money(se)}"))
    return _result("SUMMARY_MISMATCH", "Stored monthly summary disagrees with the ledger", "high",
                   "The legacy monthly-summary table does not equal the sum of recorded deposits/expenses. "
                   "Investigate, then use 'Rebuild summaries' on the Audit overview page.", items)


def check_negative_funds(start, end):
    dep = {r["fund_head__name"]: r["s"] for r in Deposit.objects.valid().filter(date__lte=end).values("fund_head__name").annotate(s=Sum("amount"))}
    exp = {r["expense_head__name"]: r["s"] for r in Expense.objects.valid().filter(date__lte=end).values("expense_head__name").annotate(s=Sum("amount"))}
    items = []
    for name in sorted(set(dep) | set(exp)):
        bal = (dep.get(name) or Decimal(0)) - (exp.get(name) or Decimal(0))
        if bal < 0:
            items.append(_item(name, f"Spent {_money(exp.get(name) or 0)} but collected only {_money(dep.get(name) or 0)} (balance {_money(bal)})"))
    return _result("NEGATIVE_FUND", "Heads spent beyond what was collected", "medium",
                   "Fund/expense heads whose expenses exceed their deposits to date. Often a head-mapping problem rather than overspend.", items)


def check_receipt_gaps(start, end):
    by_year = defaultdict(list)
    for r in Deposit.objects.exclude(receipt_no__isnull=True).values_list("receipt_no", flat=True):
        m = re.match(r"^(.*)-(\d{4})-(\d+)$", r)
        if m:
            by_year[(m.group(1), m.group(2))].append(int(m.group(3)))
    items = []
    for (prefix, year), nums in sorted(by_year.items()):
        missing = sorted(set(range(1, max(nums) + 1)) - set(nums))
        if missing:
            items.append(_item(f"{prefix}-{year}", f"Missing receipt number(s): {', '.join(str(n) for n in missing[:15])}"))
    return _result("RECEIPT_GAPS", "Gaps in receipt numbering", "high",
                   "Receipts are numbered consecutively and records are voided, never deleted - a gap suggests a deleted record.", items)


def check_pending(start, end):
    now = timezone.now()
    items = []
    for e in Expense.objects.filter(status="pending", is_void=False, created_at__lt=now - timedelta(days=7)):
        items.append(_item(f"Expense {e.pk}: {e.description}", f"{_money(e.amount)} awaiting approval since {e.created_at:%d-%m-%Y}", reverse("expense_detail", args=[e.pk])))
    Payment = apps.get_model("payments", "OnlinePayment")
    for p in Payment.objects.filter(status="pending", created_at__lt=now - timedelta(days=3)):
        items.append(_item(f"Payment {p.reference}", f"{_money(p.amount)} from {p.member} awaiting verification since {p.created_at:%d-%m-%Y}", reverse("payment_detail", args=[p.pk])))
    return _result("STALE_PENDING", "Items waiting too long for a decision", "medium",
                   "Expenses pending > 7 days or member payments unverified > 3 days.", items)


def check_payments_without_deposit(start, end):
    Payment = apps.get_model("payments", "OnlinePayment")
    qs = Payment.objects.filter(status="success", deposit__isnull=True)
    items = [_item(f"Payment {p.reference}", f"{_money(p.amount)} marked successful but no deposit exists", reverse("payment_detail", args=[p.pk])) for p in qs]
    return _result("PAYMENT_ORPHAN", "Successful payments missing a deposit", "critical",
                   "Money confirmed by a gateway/treasurer but not in the books.", items)


def check_failed_sms(start, end):
    SMS = apps.get_model("messaging", "SMSMessage")
    qs = SMS.objects.filter(status="failed", created_at__date__range=(start, end))
    items = [_item(f"SMS to {s.phone or s.member}", f"{s.trigger}: {s.error[:120]}", reverse("sms_detail", args=[s.pk])) for s in qs[:200]]
    return _result("SMS_FAILED", "Undelivered SMS notifications", "low",
                   "Members who were not informed of a payment/notice because delivery failed.", items)


def check_bad_phones(start, end):
    items = [_item(m.name, f"Phone '{m.phone_number}' is not a valid Bangladesh mobile number", reverse("member_edit", args=[m.pk]))
             for m in SocietyMember.objects.filter(is_active=True) if not m.has_valid_phone]
    return _result("BAD_PHONE", "Members without a valid mobile number", "medium",
                   "SMS receipts and reminders cannot be delivered to these members.", items)


def check_log_chain(start, end):
    ok, checked, bad = AuditLog.verify_chain()
    items = [] if ok else [_item(f"Audit entry #{bad}", f"Hash chain broken after {checked} valid entries - the log has been altered or entries removed.")]
    return _result("LOG_CHAIN", "Audit trail integrity", "critical",
                   "Every audit entry is hash-chained to the previous one; any tampering breaks the chain.", items)


ALL_CHECKS = [
    check_log_chain, check_payments_without_deposit, check_material_edits, check_summary_mismatch,
    check_receipt_gaps, check_duplicate_references, check_duplicate_deposits, check_backdated,
    check_undocumented_expenses, check_negative_funds, check_pending, check_bad_phones,
    check_large_expenses, check_self_approved, check_voided, check_failed_sms,
]


def run_all(start, end):
    results = [c(start, end) for c in ALL_CHECKS]
    results.sort(key=lambda r: (r.passed, SEV_ORDER[r.severity]))
    return results


def summarize(results):
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "exceptions": sum(r.count for r in results),
        "by_severity": {s: sum(1 for r in results if not r.passed and r.severity == s) for s in ("critical", "high", "medium", "low")},
    }
