"""
Ledger calculations - the single source of truth for every total shown in the
application (dashboards, reports, portal, SMS placeholders, audit checks).
"""
from collections import OrderedDict, defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth

from .models import (
    ContributionPlan, Deposit, Expense, ExpenseHead, MonthlyFinancialSummary, SiteSetting, SocietyMember,
)

ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------
def next_receipt_no(on_date):
    prefix = f"{SiteSetting.get().receipt_prefix}-{on_date.year}-"
    last = (
        Deposit.objects.filter(receipt_no__startswith=prefix)
        .order_by("-receipt_no").values_list("receipt_no", flat=True).first()
    )
    seq = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{seq:05d}"


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
         "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_hundred(n):
    return _ONES[n] if n < 20 else (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else ""))


def _below_thousand(n):
    out = []
    if n >= 100:
        out.append(_ONES[n // 100] + " Hundred")
        n %= 100
    if n:
        out.append(_below_hundred(n))
    return " ".join(out)


def amount_in_words(amount):
    """Taka in words using the South-Asian (crore / lakh) system."""
    amount = Decimal(amount).quantize(Decimal("0.01"))
    taka, paisa = int(amount), int((amount - int(amount)) * 100)
    if taka == 0 and paisa == 0:
        return "Zero Taka only"
    parts = []
    for unit, label in ((10_000_000, "Crore"), (100_000, "Lakh"), (1000, "Thousand")):
        if taka >= unit:
            parts.append(_below_hundred(taka // unit) + " " + label if taka // unit < 100 else _below_thousand(taka // unit) + " " + label)
            taka %= unit
    if taka:
        parts.append(_below_thousand(taka))
    words = " ".join(parts) + " Taka" if parts else ""
    if paisa:
        words += (" and " if words else "") + _below_hundred(paisa) + " Paisa"
    return words + " only"


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------
def total_shares():
    return SocietyMember.objects.filter(is_active=True).aggregate(s=Sum("shares"))["s"] or 0


def plan_total_per_share():
    return Decimal(ContributionPlan.objects.aggregate(s=Sum("amount"))["s"] or 0)


def society_totals():
    deposits = Deposit.objects.valid().aggregate(s=Sum("amount"))["s"] or ZERO
    expenses = Expense.objects.valid().aggregate(s=Sum("amount"))["s"] or ZERO
    per_share = plan_total_per_share()
    shares = total_shares()
    expected = per_share * shares
    return {
        "per_share": per_share,
        "shares": shares,
        "expected": expected,
        "deposits": deposits,
        "expenses": expenses,
        "balance": deposits - expenses,
        "due": max(expected - deposits, ZERO),
        "collection_pct": (deposits / expected * 100) if expected else ZERO,
    }


def member_positions(members=None):
    """Return a list of dicts: member, expected, paid, due, advance, pct."""
    members = list(members if members is not None else SocietyMember.objects.filter(is_active=True))
    per_share = plan_total_per_share()
    paid_map = {
        r["member"]: r["s"]
        for r in Deposit.objects.valid().values("member").annotate(s=Sum("amount"))
    }
    out = []
    for m in members:
        expected = per_share * m.shares
        paid = paid_map.get(m.id, ZERO)
        out.append({
            "member": m,
            "expected": expected,
            "paid": paid,
            "due": max(expected - paid, ZERO),
            "advance": max(paid - expected, ZERO),
            "pct": (paid / expected * 100) if expected else ZERO,
        })
    return out


def member_position(member):
    return member_positions([member])[0]


def member_plan_breakdown(member):
    """Allocate the member's payments to plans oldest-first (FIFO)."""
    remaining = Deposit.objects.valid().filter(member=member).aggregate(s=Sum("amount"))["s"] or ZERO
    today = date.today()
    rows = []
    for plan in ContributionPlan.objects.all():
        expected = Decimal(plan.amount) * member.shares
        paid = min(remaining, expected)
        remaining -= paid
        due = expected - paid
        rows.append({
            "plan": plan, "expected": expected, "paid": paid, "due": due,
            "overdue": due > 0 and plan.end_date < today,
            "status": "Paid" if due == 0 else ("Partly paid" if paid > 0 else "Unpaid"),
        })
    return rows, remaining  # remaining = advance


# ---------------------------------------------------------------------------
# Monthly ledger (with running balance)
# ---------------------------------------------------------------------------
def monthly_ledger():
    dep = {r["m"].date() if hasattr(r["m"], "date") else r["m"]: r["s"] for r in
           Deposit.objects.valid().annotate(m=TruncMonth("date")).values("m").annotate(s=Sum("amount"))}
    exp = {r["m"].date() if hasattr(r["m"], "date") else r["m"]: r["s"] for r in
           Expense.objects.valid().annotate(m=TruncMonth("date")).values("m").annotate(s=Sum("amount"))}
    months = sorted(set(dep) | set(exp))
    rows, opening = [], ZERO
    for m in months:
        d, e = dep.get(m, ZERO), exp.get(m, ZERO)
        closing = opening + d - e
        rows.append({"month": m, "opening": opening, "deposits": d, "expenses": e, "closing": closing})
        opening = closing
    return rows


def ledger_between(start, end):
    """Opening balance / movements / closing balance for an inclusive date range."""
    dq, eq = Deposit.objects.valid(), Expense.objects.valid()
    opening = (dq.filter(date__lt=start).aggregate(s=Sum("amount"))["s"] or ZERO) - \
              (eq.filter(date__lt=start).aggregate(s=Sum("amount"))["s"] or ZERO)
    d = dq.filter(date__range=(start, end)).aggregate(s=Sum("amount"), n=Count("id"))
    e = eq.filter(date__range=(start, end)).aggregate(s=Sum("amount"), n=Count("id"))
    dep, exp = d["s"] or ZERO, e["s"] or ZERO
    return {
        "opening": opening, "deposits": dep, "expenses": exp, "closing": opening + dep - exp,
        "deposit_count": d["n"], "expense_count": e["n"],
    }


def fund_summary():
    dep = {r["fund_head"]: r["s"] for r in Deposit.objects.valid().values("fund_head").annotate(s=Sum("amount"))}
    exp = {r["expense_head"]: r["s"] for r in Expense.objects.valid().values("expense_head").annotate(s=Sum("amount"))}
    rows = []
    for h in ExpenseHead.objects.all():
        d, e = dep.get(h.id, ZERO), exp.get(h.id, ZERO)
        rows.append({"head": h, "deposits": d, "expenses": e, "balance": d - e})
    return rows


# ---------------------------------------------------------------------------
# Monthly summary snapshot table (legacy) - rebuilt from the ledger
# ---------------------------------------------------------------------------
def rebuild_monthly_summary(month=None):
    """Rebuild one month (a date on day 1) or, when None, every month."""
    ledger = {r["month"]: r for r in monthly_ledger()}
    targets = [month] if month else list(ledger)
    for m in targets:
        row = ledger.get(m)
        if row:
            MonthlyFinancialSummary.objects.update_or_create(
                month=m, defaults={"total_deposits": row["deposits"], "total_expenses": row["expenses"]},
            )
        else:
            MonthlyFinancialSummary.objects.filter(month=m).delete()
    if month is None:
        MonthlyFinancialSummary.objects.exclude(month__in=ledger.keys()).delete()
