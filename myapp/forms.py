from decimal import Decimal

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import Group, User

from messaging.phone import normalize_bd_phone

from .models import (
    BuildingProject, ContributionPlan, Deposit, Expense, ExpenseHead, SiteSetting, SocietyMember,
)
from .permissions import ALL_ROLES

# --- Tailwind component classes (defined in static/src/input.css) -----------
C_INPUT, C_SELECT, C_TEXTAREA, C_CHECK, C_FILE = "ui-input", "ui-select", "ui-textarea", "ui-check", "ui-file"


class TailwindMixin:
    """Applies the design-system classes to every widget."""

    def _style(self):
        for name, field in self.fields.items():
            w = field.widget
            if isinstance(w, forms.CheckboxInput):
                cls = C_CHECK
            elif isinstance(w, (forms.Select, forms.SelectMultiple)):
                cls = C_SELECT
            elif isinstance(w, forms.Textarea):
                cls = C_TEXTAREA
                w.attrs.setdefault("rows", 3)
            elif isinstance(w, forms.ClearableFileInput):
                cls = C_FILE
            else:
                cls = C_INPUT
            w.attrs["class"] = (w.attrs.get("class", "") + " " + cls).strip()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()


class StyledForm(TailwindMixin, forms.Form):
    pass


class StyledModelForm(TailwindMixin, forms.ModelForm):
    pass


class LoginForm(TailwindMixin, AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update({"autofocus": True, "placeholder": "Username", "autocomplete": "username"})
        self.fields["password"].widget.attrs.update({"placeholder": "Password", "autocomplete": "current-password"})


DATE = lambda: forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


# --------------------------------------------------------------------------
# Members
# --------------------------------------------------------------------------
class MemberForm(StyledModelForm):
    class Meta:
        model = SocietyMember
        fields = ["name", "society_designation", "phone_number", "address", "shares", "joining_date", "is_active", "sms_opt_in"]
        widgets = {"joining_date": DATE(), "address": forms.Textarea(attrs={"rows": 2})}
        help_texts = {"phone_number": "Bangladesh mobile, e.g. 01712345678. Needed for SMS receipts and reminders."}

    def clean_phone_number(self):
        raw = self.cleaned_data["phone_number"]
        norm = normalize_bd_phone(raw)
        if not norm:
            raise forms.ValidationError("Enter a valid Bangladesh mobile number (e.g. 01712345678).")
        return "0" + norm[3:]

    def clean_shares(self):
        shares = self.cleaned_data["shares"]
        if shares < 1:
            raise forms.ValidationError("A member holds at least one share.")
        return shares


class MemberImportForm(StyledForm):
    file = forms.FileField(label="CSV file", help_text="Columns: name, phone_number, society_designation, address, shares, joining_date. "
                                                      "Include an 'id' column to update existing members instead of adding new ones.")


# --------------------------------------------------------------------------
# Deposits & expenses
# --------------------------------------------------------------------------
class DepositForm(StyledModelForm):
    class Meta:
        model = Deposit
        fields = ["date", "member", "amount", "fund_head", "method", "reference", "remark"]
        widgets = {"date": DATE(), "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member"].queryset = SocietyMember.objects.filter(is_active=True)
        if self.instance.pk:  # keep the current member selectable when editing
            self.fields["member"].queryset = SocietyMember.objects.filter(pk=self.instance.member_id) | self.fields["member"].queryset
        self.fields["remark"].required = False

    def clean_remark(self):
        return self.cleaned_data.get("remark") or "Nil"


class ExpenseForm(StyledModelForm):
    class Meta:
        model = Expense
        fields = ["date", "description", "expense_head", "amount", "created_by", "project", "voucher_no", "attachment", "remark"]
        widgets = {"date": DATE(), "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["created_by"].queryset = SocietyMember.objects.filter(is_active=True) | SocietyMember.objects.filter(pk=getattr(self.instance, "created_by_id", None))
        self.fields["created_by"].empty_label = "Select member"
        self.fields["remark"].required = False

    def clean_remark(self):
        return self.cleaned_data.get("remark") or "Nil"

    def clean_attachment(self):
        f = self.cleaned_data.get("attachment")
        if f and hasattr(f, "size"):
            if f.size > 5 * 1024 * 1024:
                raise forms.ValidationError("File is larger than 5 MB.")
            if not f.name.lower().endswith((".jpg", ".jpeg", ".png", ".pdf", ".webp")):
                raise forms.ValidationError("Upload a JPG, PNG, WEBP or PDF.")
        return f


class ReasonForm(StyledForm):
    reason = forms.CharField(max_length=255, label="Reason", widget=forms.TextInput(attrs={"placeholder": "Why? (kept in the audit trail)"}))


class DateRangeForm(StyledForm):
    start = forms.DateField(required=False, widget=DATE(), label="From")
    end = forms.DateField(required=False, widget=DATE(), label="To")


# --------------------------------------------------------------------------
# Plans, projects, heads, settings
# --------------------------------------------------------------------------
class ContributionPlanForm(StyledModelForm):
    class Meta:
        model = ContributionPlan
        fields = ["description", "amount", "start_date", "end_date"]
        widgets = {"start_date": DATE(), "end_date": DATE()}

    def clean(self):
        data = super().clean()
        if data.get("start_date") and data.get("end_date") and data["end_date"] < data["start_date"]:
            raise forms.ValidationError("The due date cannot be before the start date.")
        return data


class BuildingProjectForm(StyledModelForm):
    class Meta:
        model = BuildingProject
        fields = ["name", "start_date", "end_date", "budget", "description"]
        widgets = {"start_date": DATE(), "end_date": DATE(), "description": forms.Textarea(attrs={"rows": 3})}


class ExpenseHeadForm(StyledModelForm):
    class Meta:
        model = ExpenseHead
        fields = ["name"]


class SiteSettingForm(StyledModelForm):
    class Meta:
        model = SiteSetting
        exclude = ["updated_at"]
        widgets = {
            "bank_details": forms.Textarea(attrs={"rows": 3}),
            "payment_instructions": forms.Textarea(attrs={"rows": 3}),
        }


# --------------------------------------------------------------------------
# Users & roles
# --------------------------------------------------------------------------
class UserForm(TailwindMixin, forms.Form):
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=False)
    roles = forms.MultipleChoiceField(choices=[(r, r) for r in ALL_ROLES], widget=forms.CheckboxSelectMultiple, required=False)
    member = forms.ModelChoiceField(queryset=SocietyMember.objects.none(), required=False, label="Linked member",
                                    help_text="Link a society member to give them the portal.")
    is_active = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, user=None, **kwargs):
        self.user_obj = user
        super().__init__(*args, **kwargs)
        qs = SocietyMember.objects.filter(user__isnull=True)
        if user is not None and hasattr(user, "member_profile") and user.member_profile:
            qs = qs | SocietyMember.objects.filter(pk=user.member_profile.pk)
        self.fields["member"].queryset = qs
        self.fields["roles"].widget.attrs["class"] = ""  # rendered as checkboxes by the template
        self.fields["is_active"].widget.attrs["class"] = C_CHECK

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        qs = User.objects.filter(username__iexact=username)
        if self.user_obj is not None:
            qs = qs.exclude(pk=self.user_obj.pk)
        if qs.exists():
            raise forms.ValidationError("That username is taken.")
        return username
