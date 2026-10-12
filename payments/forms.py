import re
from decimal import Decimal

from django import forms

from myapp.forms import StyledForm, StyledModelForm
from myapp.models import ExpenseHead, SiteSetting

from .models import OnlinePayment

MANUAL_METHODS = [("bkash", "bKash"), ("nagad", "Nagad"), ("rocket", "Rocket"), ("bank", "Bank transfer")]


class PayForm(StyledForm):
    """Member's 'Deposit money' form. Fields depend on the active gateway."""
    amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("1"),
                                widget=forms.NumberInput(attrs={"step": "1", "placeholder": "Amount in Taka", "inputmode": "decimal"}))
    method = forms.ChoiceField(choices=MANUAL_METHODS, label="Paid via")
    trx_id = forms.CharField(max_length=60, required=False, label="Transaction ID (TrxID)")
    sender_number = forms.CharField(max_length=40, required=False, label="Paid from (your number / account)")
    note = forms.CharField(max_length=255, required=False, widget=forms.TextInput(attrs={"placeholder": "Optional note for the treasurer"}))

    def __init__(self, *args, member=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.member = member
        self.site = SiteSetting.get()
        self.manual = self.site.online_gateway == "manual"
        if not self.manual:
            # gateway handles the method; the member just enters an amount
            for f in ("method", "trx_id", "sender_number"):
                self.fields[f].required = False
                self.fields[f].widget = forms.HiddenInput()
        else:
            self.fields["trx_id"].required = True
            self.fields["trx_id"].help_text = "Shown in the confirmation message from bKash / Nagad / Rocket or your bank slip."
            self.fields["sender_number"].required = True

    def clean_amount(self):
        a = self.cleaned_data["amount"]
        if a < self.site.min_online_amount:
            raise forms.ValidationError(f"The minimum online payment is Tk {self.site.min_online_amount:,.0f}.")
        if a > self.site.max_online_amount:
            raise forms.ValidationError(f"The maximum online payment is Tk {self.site.max_online_amount:,.0f}. Please contact the office for larger amounts.")
        return a

    def clean_trx_id(self):
        trx = re.sub(r"\s+", "", self.cleaned_data.get("trx_id", "")).upper()
        return trx

    def clean(self):
        d = super().clean()
        if self.manual and d.get("trx_id") and d.get("method"):
            exists = OnlinePayment.objects.filter(method=d["method"], trx_id=d["trx_id"]).exclude(status__in=["rejected", "failed", "cancelled"]).exists()
            if exists:
                self.add_error("trx_id", "This transaction ID has already been submitted. If you think this is a mistake, contact the treasurer.")
        open_count = OnlinePayment.objects.filter(member=self.member, status__in=OnlinePayment.OPEN_STATUSES).count() if self.member else 0
        if open_count >= 3:
            raise forms.ValidationError("You already have 3 payments waiting. Please wait for them to be confirmed before submitting more.")
        return d


class VerifyForm(StyledForm):
    fund_head = forms.ModelChoiceField(queryset=ExpenseHead.objects.all(), label="Credit to fund head")
    note = forms.CharField(max_length=255, required=False, label="Note (optional)")

    def __init__(self, *args, payment=None, **kwargs):
        super().__init__(*args, **kwargs)
        from .services import default_fund_head
        self.fields["fund_head"].initial = (payment.fund_head if payment and payment.fund_head_id else default_fund_head())


class RejectForm(StyledForm):
    reason = forms.CharField(max_length=255, label="Reason shown to the member",
                             widget=forms.TextInput(attrs={"placeholder": "e.g. TrxID not found / amount does not match"}))
