"""
Auto payment message.

Every way money can enter the books ends in a `Deposit` row - an office entry,
a treasurer-verified bKash/Nagad payment, or a gateway callback.  Hooking the
single post_save of Deposit therefore guarantees the member is informed exactly
once (the SMS table has a uniqueness constraint per deposit), whatever the route.
"""
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from myapp.models import Deposit

from . import services


@receiver(post_save, sender=Deposit)
def announce_deposit(sender, instance, created, raw=False, **kwargs):
    if raw or not created or instance.is_void:
        return
    deposit_id = instance.pk

    def _send():
        deposit = Deposit.objects.select_related("member").get(pk=deposit_id)
        services.send_event(
            "payment_received", deposit.member,
            related=("deposit", deposit.pk), created_by=deposit.recorded_by,
            amount=services._fmt(deposit.amount), date=f"{deposit.date:%d-%m-%Y}",
            receipt_no=deposit.receipt_no or "", method=deposit.get_method_display(),
            reference=deposit.reference,
        )

    transaction.on_commit(_send)
