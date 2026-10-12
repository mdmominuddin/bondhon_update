import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q, Sum
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from messaging import services as msg
from myapp import services as ledger
from myapp.models import SiteSetting
from myapp.permissions import ROLE_MEMBER, finance_required, has_role, is_office, member_required, office_required

from . import services
from .forms import PayForm, RejectForm, VerifyForm
from .gateways import GatewayError, get_gateway, sign
from .models import OnlinePayment

log = logging.getLogger("bondhon.payments")


def _payment_ctx(payment):
    return {"words": ledger.amount_in_words(payment.amount)}


# ---------------------------------------------------------------------------
# member side
# ---------------------------------------------------------------------------
@member_required
def pay(request):
    member = request.user.member_profile
    site = SiteSetting.get()
    form = PayForm(request.POST or None, member=member)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        gw_name = site.online_gateway
        gateway = get_gateway(gw_name)
        method = d.get("method") or "card"
        payment = OnlinePayment(member=member, amount=d["amount"], method=method if gw_name == "manual" else "card",
                                gateway=gw_name, trx_id=d.get("trx_id", ""), sender_number=d.get("sender_number", ""),
                                note=d.get("note", ""), status="pending" if gw_name == "manual" else "initiated",
                                fund_head=site.default_fund_head)
        try:
            payment.save()
        except IntegrityError:
            form.add_error("trx_id", "This transaction ID has already been submitted.")
        else:
            if gw_name == "manual":
                msg.send_event("payment_pending", member, related=("payment", payment.pk), created_by=request.user,
                               amount=msg._fmt(payment.amount), reference=payment.reference)
                messages.success(request, "Payment submitted. The treasurer will verify it and you will receive an SMS receipt.")
                return redirect("payment_detail", pk=payment.pk)
            try:
                url = gateway.initiate(payment)
            except GatewayError as exc:
                payment.status, payment.decision_note = "failed", str(exc)[:255]
                payment.save()
                messages.error(request, f"Could not start the online payment: {exc}")
                return redirect("payment_detail", pk=payment.pk)
            return redirect(url)
    return render(request, "payments/pay.html", {
        "page_title": "Deposit money", "form": form, "site": site, "position": ledger.member_position(member),
        "open_payments": member.online_payments.filter(status__in=OnlinePayment.OPEN_STATUSES),
    })


@login_required
def payment_detail(request, pk):
    payment = get_object_or_404(OnlinePayment.objects.select_related("member", "deposit", "decided_by", "fund_head"), pk=pk)
    mine = getattr(request.user, "member_profile", None)
    if not is_office(request.user) and not (mine and mine.pk == payment.member_id):
        raise PermissionDenied
    ctx = {"page_title": f"Payment {payment.reference}", "payment": payment, **_payment_ctx(payment)}
    if payment.is_open and has_role(request.user, "Admin", "Treasurer"):
        ctx["verify_form"] = VerifyForm(payment=payment)
        ctx["reject_form"] = RejectForm()
    return render(request, "payments/detail.html", ctx)


@login_required
def my_payments(request):
    mine = getattr(request.user, "member_profile", None)
    if not mine:
        raise PermissionDenied
    page = Paginator(mine.online_payments.select_related("deposit"), 20).get_page(request.GET.get("page"))
    return render(request, "payments/my_payments.html", {"page_title": "My payments", "page": page})


@member_required
@require_POST
def cancel_payment(request, pk):
    payment = get_object_or_404(OnlinePayment, pk=pk, member=request.user.member_profile)
    if payment.is_open:
        payment.status, payment.decision_note = "cancelled", "Cancelled by member"
        payment.save()
        messages.info(request, "Payment cancelled.")
    return redirect("payment_detail", pk=pk)


# ---------------------------------------------------------------------------
# office side
# ---------------------------------------------------------------------------
@office_required
def payment_list(request):
    qs = OnlinePayment.objects.select_related("member", "deposit")
    status, q = request.GET.get("status", ""), request.GET.get("q", "").strip()
    if status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(trx_id__icontains=q) | Q(member__name__icontains=q) | Q(sender_number__icontains=q))
    pending = OnlinePayment.objects.filter(status="pending")
    ctx = {"page_title": "Member payments", "page": Paginator(qs, 25).get_page(request.GET.get("page")), "status": status, "q": q,
           "statuses": OnlinePayment.STATUS, "pending_count": pending.count(), "pending_total": pending.aggregate(s=Sum("amount"))["s"] or 0,
           "params": request.GET}
    return render(request, "payments/list.html", ctx)


@finance_required
@require_POST
def verify_payment(request, pk):
    payment = get_object_or_404(OnlinePayment, pk=pk)
    form = VerifyForm(request.POST, payment=payment)
    if not form.is_valid():
        messages.error(request, "Choose the fund head to credit.")
        return redirect("payment_detail", pk=pk)
    if not payment.is_open:
        messages.info(request, "This payment has already been decided.")
        return redirect("payment_detail", pk=pk)
    try:
        payment, created = services.confirm_payment(pk, by_user=request.user, fund_head=form.cleaned_data["fund_head"], note=form.cleaned_data["note"])
    except Exception as exc:
        from myapp.models import PeriodLockedError
        messages.error(request, exc.messages[0] if isinstance(exc, PeriodLockedError) else str(exc))
        return redirect("payment_detail", pk=pk)
    messages.success(request, f"Payment confirmed - deposit {payment.deposit.receipt_no} created and the member has been notified.")
    return redirect("deposit_detail", pk=payment.deposit_id)


@finance_required
@require_POST
def reject_payment(request, pk):
    payment = get_object_or_404(OnlinePayment, pk=pk)
    form = RejectForm(request.POST)
    if not form.is_valid():
        messages.error(request, "A reason is required to reject a payment.")
        return redirect("payment_detail", pk=pk)
    payment, changed = services.reject_payment(pk, by_user=request.user, reason=form.cleaned_data["reason"])
    if changed:
        msg.send_event("payment_rejected", payment.member, related=("payment-rejected", payment.pk), created_by=request.user,
                       amount=msg._fmt(payment.amount), reference=payment.reference, reason=payment.decision_note)
        messages.success(request, "Payment rejected and the member informed.")
    return redirect("payment_detail", pk=pk)


# ---------------------------------------------------------------------------
# gateways
# ---------------------------------------------------------------------------
@login_required
def sandbox_page(request, reference):
    """Fake hosted payment page - development / demo only."""
    if not settings.PAYMENT_SANDBOX_ENABLED:
        raise Http404
    payment = get_object_or_404(OnlinePayment, reference=reference, gateway="sandbox")
    mine = getattr(request.user, "member_profile", None)
    if not (mine and mine.pk == payment.member_id) and not is_office(request.user):
        raise PermissionDenied
    return render(request, "payments/sandbox.html", {"page_title": "Sandbox gateway", "payment": payment,
                                                      "sig": {s: sign(payment.reference, s) for s in ("success", "failed", "cancelled")}})


@login_required
def sandbox_return(request, reference):
    if not settings.PAYMENT_SANDBOX_ENABLED:
        raise Http404
    payment = get_object_or_404(OnlinePayment, reference=reference, gateway="sandbox")
    status, sig = request.GET.get("status", ""), request.GET.get("sig", "")
    if status not in ("success", "failed", "cancelled") or sig != sign(reference, status):
        return HttpResponseBadRequest("Invalid gateway signature.")
    if payment.is_open:
        if status == "success":
            payment.gateway_ref = f"SBX-{payment.reference}"
            payment.trx_id = payment.gateway_ref
            payment.save()
            services.confirm_payment(payment.pk, raw={"sandbox": True}, note="Sandbox gateway")
        else:
            payment.status = status
            payment.decision_note = f"Sandbox gateway: {status}"
            payment.save()
    return redirect("payment_detail", pk=payment.pk)


@csrf_exempt
def gateway_return(request, gateway, outcome):
    """
    Return/IPN endpoint for redirect gateways.  Nothing is trusted from the
    browser: success is only recorded after a server-to-server validation.
    """
    if gateway != "sslcommerz":
        raise Http404
    data = request.POST if request.method == "POST" else request.GET
    reference = data.get("tran_id", "")
    payment = OnlinePayment.objects.filter(reference=reference, gateway="sslcommerz").first()
    if not payment:
        return HttpResponseBadRequest("Unknown transaction.")
    gw = get_gateway("sslcommerz")
    if outcome in ("success", "ipn") and payment.is_open:
        try:
            ok, raw = gw.validate(payment, data)
        except GatewayError as exc:
            ok, raw = False, {"error": str(exc)}
        if ok:
            payment.gateway_ref = raw.get("bank_tran_id", "") or data.get("val_id", "")
            payment.trx_id = payment.gateway_ref
            payment.save()
            services.confirm_payment(payment.pk, raw=raw, note="SSLCommerz validated")
        else:
            log.warning("SSLCommerz validation failed for %s: %s", reference, raw)
            payment.raw_response = raw
            payment.status, payment.decision_note = "failed", "Gateway validation failed"
            payment.save()
    elif outcome in ("fail", "cancel") and payment.is_open:
        payment.status = "failed" if outcome == "fail" else "cancelled"
        payment.decision_note = f"Gateway reported {outcome}"
        payment.save()
    if outcome == "ipn":
        return HttpResponse("OK")
    payment.refresh_from_db()
    if request.user.is_authenticated:
        return redirect("payment_detail", pk=payment.pk)
    return render(request, "payments/gateway_done.html", {"payment": payment})
