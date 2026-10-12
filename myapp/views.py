import csv
import io
import secrets
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from audit.models import AuditLog
from messaging.models import SMSMessage
from messaging.phone import normalize_bd_phone

from . import services
from .forms import (
    BuildingProjectForm, ContributionPlanForm, DateRangeForm, DepositForm, ExpenseForm, ExpenseHeadForm,
    LoginForm, MemberForm, MemberImportForm, ReasonForm, SiteSettingForm, TailwindMixin, UserForm,
)
from .models import (
    BuildingProject, ContributionPlan, Deposit, Expense, ExpenseHead, Notification, PeriodLockedError,
    SiteSetting, SocietyMember,
)
from .permissions import (
    ALL_ROLES, ROLE_ADMIN, ROLE_DESCRIPTIONS, ROLE_MEMBER, ROLE_TREASURER, admin_required, can_manage_finance,
    finance_required, has_role, is_admin, is_office, office_required, user_roles,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def paginate(request, qs, per_page=25):
    return Paginator(qs, per_page).get_page(request.GET.get("page"))


def csv_response(filename, header, rows):
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.write("\ufeff")  # Excel-friendly UTF-8
    w = csv.writer(resp)
    w.writerow(header)
    w.writerows(rows)
    AuditLog.record("export", message=f"CSV export: {filename}")
    return resp


def parse_range(request):
    form = DateRangeForm(request.GET or None)
    start = end = None
    if form.is_valid():
        start, end = form.cleaned_data["start"], form.cleaned_data["end"]
    return form, start, end


def own_member(user):
    return getattr(user, "member_profile", None)


def guard_period(request, fn, *args, **kwargs):
    """Run fn; turn a locked-period error into a flash message. Returns True on success."""
    try:
        fn(*args, **kwargs)
        return True
    except PeriodLockedError as exc:
        messages.error(request, exc.messages[0])
        return False


# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------
def user_login(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = LoginForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        nxt = request.POST.get("next") or request.GET.get("next")
        if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
            return redirect(nxt)
        return redirect("home")
    return render(request, "accounts/login.html", {"form": form, "next": request.GET.get("next", "")})


def user_logout(request):
    if request.method == "POST":
        logout(request)
        messages.info(request, "You have been signed out.")
    return redirect("login")


def register(request):
    messages.info(request, "Accounts are created by the society administrator. Ask the office for your login.")
    return redirect("login")


class StyledPasswordChangeForm(TailwindMixin, PasswordChangeForm):
    pass


@login_required
def password_change(request):
    form = StyledPasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        member = own_member(user)
        if member and member.must_change_password:
            member.must_change_password = False
            member.save(update_fields=["must_change_password"])
        AuditLog.record("role", model="auth.User", object_id=user.pk, object_repr=user.username, message="Password changed by user")
        messages.success(request, "Your password has been changed.")
        return redirect("home")
    forced = bool(own_member(request.user) and own_member(request.user).must_change_password)
    return render(request, "accounts/password_change.html", {"form": form, "forced": forced, "page_title": "Change password"})


# ---------------------------------------------------------------------------
# dashboards
# ---------------------------------------------------------------------------
@login_required
def home(request):
    if has_role(request.user, ROLE_ADMIN, ROLE_TREASURER, "Auditor"):
        return office_dashboard(request)
    member = own_member(request.user)
    if member:
        return member_dashboard(request, member)
    raise PermissionDenied("Your account has no role yet. Ask the administrator to assign one.")


def office_dashboard(request):
    totals = services.society_totals()
    ledger = services.monthly_ledger()
    positions = services.member_positions()
    top_dues = sorted([p for p in positions if p["due"] > 0], key=lambda p: -p["due"])[:6]
    now = timezone.now()
    attention = []
    if can_manage_finance(request.user):
        n = Expense.objects.filter(status="pending", is_void=False).count()
        if n:
            attention.append({"tone": "warning", "text": f"{n} expense(s) waiting for approval", "url": "expense_list", "query": "?status=pending"})
        from payments.models import OnlinePayment
        n = OnlinePayment.objects.filter(status="pending").count()
        if n:
            attention.append({"tone": "warning", "text": f"{n} member payment(s) waiting for verification", "url": "payment_list", "query": "?status=pending"})
        n = SMSMessage.objects.filter(status="failed", created_at__gte=now - timedelta(days=30)).count()
        if n:
            attention.append({"tone": "danger", "text": f"{n} SMS failed in the last 30 days", "url": "sms_outbox", "query": "?status=failed"})
        n = sum(1 for m in SocietyMember.objects.filter(is_active=True) if not m.has_valid_phone)
        if n:
            attention.append({"tone": "danger", "text": f"{n} member(s) have no valid mobile number - SMS cannot reach them", "url": "member_list", "query": "?phone=invalid"})
    from audit.models import AuditFinding
    n = AuditFinding.objects.filter(status="open").count()
    if n:
        attention.append({"tone": "info", "text": f"{n} open audit finding(s)", "url": "audit_engagements", "query": ""})
    ctx = {
        "page_title": "Dashboard", "totals": totals,
        "chart": {
            "labels": [r["month"].strftime("%b %Y") for r in ledger[-12:]],
            "deposits": [float(r["deposits"]) for r in ledger[-12:]],
            "expenses": [float(r["expenses"]) for r in ledger[-12:]],
            "closing": [float(r["closing"]) for r in ledger[-12:]],
        },
        "funds": services.fund_summary(),
        "top_dues": top_dues,
        "recent_deposits": Deposit.objects.valid().select_related("member", "fund_head")[:6],
        "recent_expenses": Expense.objects.valid().select_related("expense_head")[:6],
        "attention": attention,
        "member_count": len(positions),
        "paid_up": sum(1 for p in positions if p["due"] == 0),
    }
    return render(request, "dashboard/office.html", ctx)


def member_dashboard(request, member):
    position = services.member_position(member)
    plans, advance = services.member_plan_breakdown(member)
    ctx = {
        "page_title": "My account", "member": member, "position": position, "plans": plans,
        "totals": services.society_totals(),
        "recent_deposits": Deposit.objects.valid().filter(member=member)[:5],
        "notifications": Notification.objects.filter(member=member)[:5],
        "open_payments": member.online_payments.filter(status__in=["initiated", "pending"]),
    }
    return render(request, "dashboard/member.html", ctx)


@login_required
def notifications(request):
    member = own_member(request.user)
    if not member:
        raise PermissionDenied
    if request.method == "POST":
        Notification.objects.filter(member=member, is_read=False).update(is_read=True)
        return redirect("notifications")
    items = Notification.objects.filter(member=member)
    page = paginate(request, items, 20)
    return render(request, "members/notifications.html", {"page_title": "Notifications", "page": page})


# ---------------------------------------------------------------------------
# members
# ---------------------------------------------------------------------------
@office_required
def member_list(request):
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "active")
    members = SocietyMember.objects.select_related("user")
    if status == "active":
        members = members.filter(is_active=True)
    elif status == "inactive":
        members = members.filter(is_active=False)
    if q:
        members = members.filter(Q(name__icontains=q) | Q(phone_number__icontains=q))
    rows = services.member_positions(members)
    if request.GET.get("phone") == "invalid":
        rows = [r for r in rows if not r["member"].has_valid_phone]
    if request.GET.get("due") == "1":
        rows = [r for r in rows if r["due"] > 0]
    if request.GET.get("format") == "csv":
        return csv_response("members.csv", ["id", "name", "designation", "phone_number", "shares", "expected", "paid", "due"],
                            [[r["member"].id, r["member"].name, r["member"].society_designation, r["member"].phone_number,
                              r["member"].shares, r["expected"], r["paid"], r["due"]] for r in rows])
    page = paginate(request, rows, 30)
    return render(request, "members/list.html", {"page_title": "Members", "page": page, "q": q, "status": status, "total": len(rows)})


@login_required
def member_detail(request, pk):
    member = get_object_or_404(SocietyMember, pk=pk)
    mine = own_member(request.user)
    if not is_office(request.user) and not (mine and mine.pk == member.pk):
        raise PermissionDenied
    plans, advance = services.member_plan_breakdown(member)
    ctx = {
        "page_title": member.name, "member": member, "position": services.member_position(member),
        "plans": plans, "deposits": Deposit.objects.filter(member=member).select_related("fund_head"),
        "payments": member.online_payments.all()[:10],
        "sms": member.sms_messages.all()[:10] if is_office(request.user) else [],
        "is_self": bool(mine and mine.pk == member.pk),
    }
    return render(request, "members/detail.html", ctx)


@finance_required
def member_add(request):
    form = MemberForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        member = form.save()
        messages.success(request, f"{member.name} added.")
        return redirect("member_detail", pk=member.pk)
    return render(request, "members/form.html", {"page_title": "Add member", "form": form, "cancel": "member_list"})


@finance_required
def member_edit(request, pk):
    member = get_object_or_404(SocietyMember, pk=pk)
    form = MemberForm(request.POST or None, instance=member)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Member updated.")
        return redirect("member_detail", pk=member.pk)
    return render(request, "members/form.html", {"page_title": f"Edit {member.name}", "form": form, "member": member, "cancel": "member_list"})


@finance_required
def member_import(request):
    form = MemberImportForm(request.POST or None, request.FILES or None)
    report = None
    if request.method == "POST" and form.is_valid():
        text = request.FILES["file"].read().decode("utf-8-sig", "replace")
        reader = csv.DictReader(io.StringIO(text))
        report = {"created": 0, "updated": 0, "errors": []}
        with transaction.atomic():
            for n, row in enumerate(reader, start=2):
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                try:
                    phone = row.get("phone_number") or row.get("phone", "")
                    norm = normalize_bd_phone(phone) if phone else None
                    if phone and not norm:
                        raise ValueError(f"invalid mobile number '{phone}'")
                    if row.get("id"):
                        m = SocietyMember.objects.get(pk=int(row["id"]))
                    else:
                        if not row.get("name"):
                            raise ValueError("name is required")
                        m = SocietyMember(name=row["name"])
                    if row.get("name"):
                        m.name = row["name"]
                    if norm:
                        m.phone_number = "0" + norm[3:]
                    elif not m.pk:
                        raise ValueError("phone_number is required for new members")
                    if row.get("society_designation"):
                        m.society_designation = row["society_designation"]
                    if row.get("address"):
                        m.address = row["address"]
                    if row.get("shares"):
                        m.shares = max(int(row["shares"]), 1)
                    if row.get("joining_date"):
                        m.joining_date = date.fromisoformat(row["joining_date"])
                    was_new = m.pk is None
                    m.save()
                    report["created" if was_new else "updated"] += 1
                except (ValueError, SocietyMember.DoesNotExist) as exc:
                    report["errors"].append(f"Row {n}: {exc}")
        if report["created"] or report["updated"]:
            messages.success(request, f"Import finished: {report['created']} added, {report['updated']} updated.")
    return render(request, "members/import.html", {"page_title": "Import / update members", "form": form, "report": report})


@finance_required
@require_POST
def member_create_login(request, pk):
    member = get_object_or_404(SocietyMember, pk=pk)
    send_sms = request.POST.get("send_sms") == "1"
    temp = secrets.token_urlsafe(7)
    reset = member.user_id is not None
    with transaction.atomic():
        if reset:
            user = member.user
        else:
            base = ("0" + member.sms_number[3:]) if member.sms_number else f"member{member.pk}"
            username, i = base, 1
            while User.objects.filter(username=username).exists():
                i += 1
                username = f"{base}-{i}"
            user = User(username=username, first_name=member.name.split()[0][:150])
        user.set_password(temp)
        user.is_active = True
        user.save()
        user.groups.add(Group.objects.get_or_create(name=ROLE_MEMBER)[0])
        member.user = user
        member.must_change_password = True
        member.save()
    if send_sms:
        from django.conf import settings
        from messaging import services as msg
        sms = msg.send_event("account_created", member, in_app=False, created_by=request.user, username=user.username,
                             temp_password=temp, login_url=settings.SITE_BASE_URL)
        note = f" SMS status: {sms.status}." if sms else ""
    else:
        note = ""
    messages.success(request, f"Portal login {'reset' if reset else 'created'}. Username: {user.username}  Temporary password: {temp}  "
                              f"(shown once - the member must change it at first sign-in).{note}")
    return redirect("member_detail", pk=member.pk)


# ---------------------------------------------------------------------------
# deposits
# ---------------------------------------------------------------------------
def deposit_queryset(request):
    qs = Deposit.objects.select_related("member", "fund_head")
    mine = own_member(request.user)
    if not is_office(request.user):
        qs = qs.filter(member=mine) if mine else qs.none()
    form, start, end = parse_range(request)
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    if request.GET.get("member") and is_office(request.user):
        qs = qs.filter(member_id=request.GET["member"])
    if request.GET.get("fund_head"):
        qs = qs.filter(fund_head_id=request.GET["fund_head"])
    if request.GET.get("method"):
        qs = qs.filter(method=request.GET["method"])
    if request.GET.get("source"):
        qs = qs.filter(source=request.GET["source"])
    if request.GET.get("void") != "1":
        qs = qs.filter(is_void=False)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(receipt_no__icontains=q) | Q(member__name__icontains=q) | Q(reference__icontains=q) | Q(remark__icontains=q))
    return qs, form


@login_required
def deposit_list(request):
    qs, form = deposit_queryset(request)
    total = qs.filter(is_void=False).aggregate(s=Sum("amount"))["s"] or 0
    if request.GET.get("format") == "csv" and is_office(request.user):
        return csv_response("deposits.csv", ["receipt", "date", "member", "amount", "fund_head", "method", "reference", "source", "remark", "void"],
                            [[d.receipt_no, d.date, d.member.name, d.amount, d.fund_head.name, d.get_method_display(), d.reference,
                              d.get_source_display(), d.remark, d.is_void] for d in qs])
    ctx = {
        "page_title": "Deposits", "page": paginate(request, qs), "total": total, "range_form": form, "count": qs.count(),
        "members": SocietyMember.objects.all(), "heads": ExpenseHead.objects.all(), "methods": Deposit.METHODS,
        "params": request.GET,
    }
    return render(request, "finance/deposit_list.html", ctx)


@finance_required
def create_deposit(request):
    initial = {"date": timezone.localdate(), "fund_head": SiteSetting.get().default_fund_head_id}
    if request.GET.get("member"):
        initial["member"] = request.GET["member"]
    form = DepositForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        deposit = form.save(commit=False)
        deposit.recorded_by, deposit.source = request.user, "admin"
        try:
            deposit.save()
        except PeriodLockedError as exc:
            form.add_error("date", exc.messages[0])
        else:
            sms = SMSMessage.objects.filter(related_type="deposit", related_id=deposit.pk).first()
            note = {
                "sent": " Receipt SMS sent to the member.",
                "queued": " Receipt SMS queued.",
                "failed": " Receipt SMS could not be delivered - see Messages > Outbox.",
                "skipped": f" Receipt SMS skipped: {sms.error}" if sms else "",
            }.get(sms.status if sms else "", "")
            messages.success(request, f"Deposit {deposit.receipt_no} recorded.{note}")
            if "add_another" in request.POST:
                return redirect("create_deposit")
            return redirect("deposit_detail", pk=deposit.pk)
    return render(request, "finance/deposit_form.html", {"page_title": "Record a deposit", "form": form})


@login_required
def deposit_detail(request, pk):
    deposit = get_object_or_404(Deposit.objects.select_related("member", "fund_head", "recorded_by", "voided_by"), pk=pk)
    mine = own_member(request.user)
    if not is_office(request.user) and not (mine and mine.pk == deposit.member_id):
        raise PermissionDenied
    ctx = {
        "page_title": f"Receipt {deposit.receipt_no}", "deposit": deposit,
        "words": services.amount_in_words(deposit.amount),
        "position": services.member_position(deposit.member),
        "sms": SMSMessage.objects.filter(related_type="deposit", related_id=deposit.pk).first() if is_office(request.user) else None,
        "reason_form": ReasonForm(),
    }
    return render(request, "finance/deposit_detail.html", ctx)


@finance_required
def deposit_edit(request, pk):
    deposit = get_object_or_404(Deposit, pk=pk)
    if deposit.is_void:
        messages.error(request, "A voided deposit cannot be edited.")
        return redirect("deposit_detail", pk=pk)
    form = DepositForm(request.POST or None, instance=deposit)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except PeriodLockedError as exc:
            form.add_error("date", exc.messages[0])
        else:
            messages.success(request, "Deposit updated. The change is recorded in the audit trail.")
            return redirect("deposit_detail", pk=pk)
    return render(request, "finance/deposit_form.html", {"page_title": f"Edit deposit {deposit.receipt_no}", "form": form, "deposit": deposit})


@admin_required
@require_POST
def deposit_void(request, pk):
    deposit = get_object_or_404(Deposit, pk=pk)
    form = ReasonForm(request.POST)
    if form.is_valid() and not deposit.is_void:
        if guard_period(request, deposit.void, request.user, form.cleaned_data["reason"]):
            messages.success(request, f"Deposit {deposit.receipt_no} voided.")
    else:
        messages.error(request, "A reason is required to void a deposit.")
    return redirect("deposit_detail", pk=pk)


# ---------------------------------------------------------------------------
# expenses
# ---------------------------------------------------------------------------
@login_required
def expense_list(request):
    site = SiteSetting.get()
    if not is_office(request.user) and not site.members_see_expenses:
        raise PermissionDenied
    qs = Expense.objects.select_related("expense_head", "created_by", "project")
    form, start, end = parse_range(request)
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    if request.GET.get("head"):
        qs = qs.filter(expense_head_id=request.GET["head"])
    status = request.GET.get("status", "")
    if not is_office(request.user):
        qs = qs.filter(status="approved", is_void=False)
    else:
        if status:
            qs = qs.filter(status=status)
        if request.GET.get("void") != "1":
            qs = qs.filter(is_void=False)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(description__icontains=q) | Q(voucher_no__icontains=q) | Q(remark__icontains=q))
    total = qs.filter(is_void=False, status="approved").aggregate(s=Sum("amount"))["s"] or 0
    if request.GET.get("format") == "csv" and is_office(request.user):
        return csv_response("expenses.csv", ["date", "description", "head", "amount", "paid_by", "voucher", "status", "remark", "void"],
                            [[e.date, e.description, e.expense_head.name, e.amount, e.created_by.name, e.voucher_no, e.status, e.remark, e.is_void] for e in qs])
    ctx = {"page_title": "Expenses", "page": paginate(request, qs), "total": total, "range_form": form, "count": qs.count(),
           "heads": ExpenseHead.objects.all(), "params": request.GET, "statuses": Expense.STATUS}
    return render(request, "finance/expense_list.html", ctx)


@finance_required
def create_expense(request):
    form = ExpenseForm(request.POST or None, request.FILES or None, initial={"date": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        expense = form.save(commit=False)
        expense.recorded_by = request.user
        if is_admin(request.user):
            expense.status, expense.approved_by, expense.approved_at = "approved", request.user, timezone.now()
        else:
            expense.status = "pending"
        try:
            expense.save()
        except PeriodLockedError as exc:
            form.add_error("date", exc.messages[0])
        else:
            if expense.status == "pending":
                messages.success(request, "Expense recorded and sent to the administrator for approval. It will count in the books once approved.")
            else:
                messages.success(request, "Expense recorded and approved.")
            return redirect("expense_detail", pk=expense.pk)
    return render(request, "finance/expense_form.html", {"page_title": "Record an expense", "form": form})


@login_required
def expense_detail(request, pk):
    expense = get_object_or_404(Expense.objects.select_related("expense_head", "created_by", "recorded_by", "approved_by", "voided_by", "project"), pk=pk)
    if not is_office(request.user):
        if not SiteSetting.get().members_see_expenses or not expense.counts:
            raise PermissionDenied
    return render(request, "finance/expense_detail.html", {"page_title": "Expense", "expense": expense, "reason_form": ReasonForm(),
                                                           "threshold": SiteSetting.get().large_expense_threshold})


@finance_required
def expense_edit(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    if expense.is_void:
        messages.error(request, "A voided expense cannot be edited.")
        return redirect("expense_detail", pk=pk)
    form = ExpenseForm(request.POST or None, request.FILES or None, instance=expense)
    if request.method == "POST" and form.is_valid():
        e = form.save(commit=False)
        resubmitted = False
        if not is_admin(request.user) and e.status == "approved":
            e.status, e.approved_by, e.approved_at = "pending", None, None  # maker-checker: re-approval after a change
            resubmitted = True
        try:
            e.save()
        except PeriodLockedError as exc:
            form.add_error("date", exc.messages[0])
        else:
            messages.success(request, "Expense updated." + (" It needs approval again." if resubmitted else ""))
            return redirect("expense_detail", pk=pk)
    return render(request, "finance/expense_form.html", {"page_title": "Edit expense", "form": form, "expense": expense})


@admin_required
@require_POST
def expense_approve(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    if expense.status == "pending" and not expense.is_void:
        if guard_period(request, expense.approve, request.user):
            messages.success(request, "Expense approved - it now counts in the books.")
    nxt = request.POST.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect("expense_detail", pk=pk)


@admin_required
@require_POST
def expense_reject(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    form = ReasonForm(request.POST)
    if form.is_valid() and expense.status == "pending":
        if guard_period(request, expense.reject, request.user, form.cleaned_data["reason"]):
            messages.success(request, "Expense rejected.")
    else:
        messages.error(request, "A reason is required to reject an expense.")
    return redirect("expense_detail", pk=pk)


@admin_required
@require_POST
def expense_void(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    form = ReasonForm(request.POST)
    if form.is_valid() and not expense.is_void:
        if guard_period(request, expense.void, request.user, form.cleaned_data["reason"]):
            messages.success(request, "Expense voided.")
    else:
        messages.error(request, "A reason is required to void an expense.")
    return redirect("expense_detail", pk=pk)


# ---------------------------------------------------------------------------
# plans & projects
# ---------------------------------------------------------------------------
@office_required
def contribution_plan_list(request):
    plans = ContributionPlan.objects.all()
    shares = services.total_shares()
    rows = [{"plan": p, "expected": Decimal(p.amount) * shares} for p in plans]
    return render(request, "finance/plan_list.html", {"page_title": "Contribution plans", "rows": rows, "shares": shares,
                                                      "per_share": services.plan_total_per_share()})


@admin_required
def create_contribution_plan(request):
    form = ContributionPlanForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Contribution plan created. Every member's expected amount has been updated.")
        return redirect("contribution_plan_list")
    return render(request, "members/form.html", {"page_title": "New contribution plan", "form": form, "cancel": "contribution_plan_list",
                                                 "intro": "Amount is per share. Adding a plan increases every member's expected contribution."})


@admin_required
def contribution_plan_detail(request, pk):
    plan = get_object_or_404(ContributionPlan, pk=pk)
    form = ContributionPlanForm(request.POST or None, instance=plan)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Plan updated.")
        return redirect("contribution_plan_list")
    return render(request, "members/form.html", {"page_title": f"Edit plan: {plan.description}", "form": form, "cancel": "contribution_plan_list"})


@office_required
def project_list(request):
    rows = []
    for p in BuildingProject.objects.all():
        spent = p.expenses.filter(is_void=False, status="approved").aggregate(s=Sum("amount"))["s"] or 0
        rows.append({"project": p, "spent": spent, "remaining": p.budget - spent, "pct": (spent / p.budget * 100) if p.budget else 0})
    return render(request, "finance/project_list.html", {"page_title": "Projects", "rows": rows})


@finance_required
def project_form(request, pk=None):
    project = get_object_or_404(BuildingProject, pk=pk) if pk else None
    form = BuildingProjectForm(request.POST or None, instance=project)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Project saved.")
        return redirect("project_list")
    return render(request, "members/form.html", {"page_title": "Edit project" if project else "New project", "form": form, "cancel": "project_list",
                                                 "intro": "Tag expenses to a project when recording them to track budget against actual spend."})


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------
@office_required
def reports_index(request):
    return render(request, "reports/index.html", {"page_title": "Reports"})


@login_required
def monthly_summary(request, year=None, month=None):
    if not is_office(request.user) and not SiteSetting.get().members_see_expenses:
        raise PermissionDenied
    rows = services.monthly_ledger()
    totals = services.society_totals()
    if request.GET.get("format") == "csv" and is_office(request.user):
        return csv_response("monthly-ledger.csv", ["month", "opening", "deposits", "expenses", "closing"],
                            [[r["month"].strftime("%Y-%m"), r["opening"], r["deposits"], r["expenses"], r["closing"]] for r in rows])
    return render(request, "reports/monthly.html", {"page_title": "Monthly ledger", "rows": reversed(rows) if request.GET.get("order") != "asc" else rows,
                                                    "totals": totals})


@office_required
def members_with_dues(request):
    rows = sorted([p for p in services.member_positions() if p["due"] > 0], key=lambda p: -p["due"])
    if request.GET.get("format") == "csv":
        return csv_response("dues.csv", ["member", "phone", "shares", "expected", "paid", "due"],
                            [[r["member"].name, r["member"].phone_number, r["member"].shares, r["expected"], r["paid"], r["due"]] for r in rows])
    return render(request, "reports/dues.html", {"page_title": "Dues", "rows": rows, "total_due": sum(r["due"] for r in rows)})


@office_required
def fund_report(request):
    rows = services.fund_summary()
    return render(request, "reports/funds.html", {"page_title": "Fund-wise summary", "rows": rows,
                                                  "totals": {"deposits": sum(r["deposits"] for r in rows), "expenses": sum(r["expenses"] for r in rows)}})


@office_required
def collection_report(request):
    """Member-by-member collection status with plan columns."""
    plans = list(ContributionPlan.objects.all())
    rows = []
    for pos in services.member_positions():
        breakdown, _ = services.member_plan_breakdown(pos["member"])
        rows.append({**pos, "breakdown": breakdown})
    return render(request, "reports/collection.html", {"page_title": "Collection by plan", "plans": plans, "rows": rows})


# ---------------------------------------------------------------------------
# settings, heads, users
# ---------------------------------------------------------------------------
@admin_required
def site_settings(request):
    site = SiteSetting.get()
    form = SiteSettingForm(request.POST or None, instance=site)
    head_form = ExpenseHeadForm(prefix="head")
    if request.method == "POST":
        if "add_head" in request.POST:
            head_form = ExpenseHeadForm(request.POST, prefix="head")
            if head_form.is_valid():
                head_form.save()
                messages.success(request, "Head added.")
                return redirect("site_settings")
        elif form.is_valid():
            form.save()
            messages.success(request, "Settings saved.")
            return redirect("site_settings")
    from django.conf import settings as dj
    env = {
        "sms_http_ready": bool(dj.SMS_HTTP_URL and dj.SMS_HTTP_PARAMS),
        "sslcommerz_ready": bool(dj.SSLCOMMERZ_STORE_ID and dj.SSLCOMMERZ_STORE_PASSWORD),
        "sandbox_enabled": dj.PAYMENT_SANDBOX_ENABLED,
        "base_url": dj.SITE_BASE_URL,
    }
    return render(request, "settings/site.html", {"page_title": "Settings", "form": form, "head_form": head_form,
                                                  "heads": ExpenseHead.objects.annotate(n_dep=Count("deposit", distinct=True), n_exp=Count("expense", distinct=True)),
                                                  "env": env})


@admin_required
def user_list(request):
    users = User.objects.prefetch_related("groups").select_related("member_profile").order_by("username")
    return render(request, "settings/users.html", {"page_title": "Users & roles", "users": users, "role_descriptions": ROLE_DESCRIPTIONS})


@admin_required
def user_form(request, pk=None):
    user = get_object_or_404(User, pk=pk) if pk else None
    initial = {}
    if user:
        initial = {"username": user.username, "first_name": user.first_name, "email": user.email, "is_active": user.is_active,
                   "roles": [g for g in user.groups.values_list("name", flat=True) if g in ALL_ROLES],
                   "member": getattr(user, "member_profile", None)}
    form = UserForm(request.POST or None, initial=initial, user=user)
    temp = None
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        if user is None:
            user = User(username=d["username"])
            temp = secrets.token_urlsafe(8)
            user.set_password(temp)
        elif user.pk == request.user.pk and not d["is_active"]:
            form.add_error("is_active", "You cannot deactivate your own account.")
            return render(request, "settings/user_form.html", {"page_title": "Edit user", "form": form, "edit": user})
        user.username, user.first_name, user.email, user.is_active = d["username"], d["first_name"], d["email"], d["is_active"]
        wanted = set(d["roles"])
        if user.pk == request.user.pk and not user.is_superuser and ROLE_ADMIN not in wanted:
            form.add_error("roles", "You cannot remove your own Admin role.")
            return render(request, "settings/user_form.html", {"page_title": "Edit user", "form": form, "edit": user})
        user.save()
        member = d["member"]
        if member is not None:
            wanted.add(ROLE_MEMBER)
        user.groups.set(Group.objects.filter(name__in=wanted) | user.groups.exclude(name__in=ALL_ROLES))
        old = getattr(user, "member_profile", None)
        if old and (member is None or old.pk != member.pk):
            old.user = None
            old.save(update_fields=["user"])
        if member is not None and member.user_id != user.pk:
            member.user = user
            member.save(update_fields=["user"])
        if temp:
            messages.success(request, f"User created. Username: {user.username}  Temporary password: {temp}  (shown once).")
        else:
            messages.success(request, "User updated.")
        return redirect("user_list")
    return render(request, "settings/user_form.html", {"page_title": "Edit user" if user else "New user", "form": form, "edit": user})


@admin_required
@require_POST
def user_reset_password(request, pk):
    user = get_object_or_404(User, pk=pk)
    temp = secrets.token_urlsafe(8)
    user.set_password(temp)
    user.save()
    member = getattr(user, "member_profile", None)
    if member:
        member.must_change_password = True
        member.save(update_fields=["must_change_password"])
    AuditLog.record("role", model="auth.User", object_id=user.pk, object_repr=user.username, message=f"Password reset by {request.user.username}")
    messages.success(request, f"Password reset for {user.username}. Temporary password: {temp}  (shown once).")
    return redirect("user_list")


# ---------------------------------------------------------------------------
# help
# ---------------------------------------------------------------------------
@login_required
def help_guide(request):
    return render(request, "help/guide.html", {"page_title": "Help & guide"})


# ---------------------------------------------------------------------------
# legacy URL aliases
# ---------------------------------------------------------------------------
@login_required
def view_deposits(request, member_id):
    return redirect("member_detail", pk=member_id)


@login_required
def legacy_redirect(request, target):
    return redirect(target)
