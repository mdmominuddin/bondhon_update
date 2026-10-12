"""Turning a member's payment into a ledger deposit - idempotently."""
from django.db import transaction
from django.utils import timezone

from myapp.models import Deposit, ExpenseHead, SiteSetting

from .models import OnlinePayment

METHOD_TO_DEPOSIT = {"bkash": "bkash", "nagad": "nagad", "rocket": "rocket", "bank": "bank", "card": "card"}


def default_fund_head():
    site = SiteSetting.get()
    return site.default_fund_head or ExpenseHead.objects.order_by("id").first()


@transaction.atomic
def confirm_payment(payment_id, *, by_user=None, fund_head=None, note="", raw=None):
    """
    Mark a payment successful and create its Deposit.  Safe to call twice (e.g.
    gateway success redirect + IPN): the second call finds the deposit already there.
    Creating the Deposit triggers the automatic receipt SMS.
    """
    payment = OnlinePayment.objects.select_for_update().get(pk=payment_id)
    if payment.deposit_id:
        return payment, False
    head = fund_head or payment.fund_head or default_fund_head()
    if head is None:
        raise ValueError("No fund head is configured - add one under Settings before accepting payments.")
    deposit = Deposit.objects.create(
        date=timezone.localdate(), member=payment.member, amount=payment.amount, fund_head=head,
        remark=f"Online payment {payment.reference}"[:200], method=METHOD_TO_DEPOSIT.get(payment.method, "other"),
        reference=payment.trx_id or payment.gateway_ref, source="online", recorded_by=by_user,
    )
    payment.deposit = deposit
    payment.status = "success"
    payment.fund_head = head
    payment.decided_by = by_user
    payment.decided_at = timezone.now()
    if note:
        payment.decision_note = note[:255]
    if raw:
        payment.raw_response = raw
    payment.save()
    return payment, True


@transaction.atomic
def reject_payment(payment_id, *, by_user, reason):
    payment = OnlinePayment.objects.select_for_update().get(pk=payment_id)
    if not payment.is_open:
        return payment, False
    payment.status = "rejected"
    payment.decided_by = by_user
    payment.decided_at = timezone.now()
    payment.decision_note = reason[:255]
    payment.save()
    return payment, True
