from django import forms

from myapp.forms import DATE, StyledForm, StyledModelForm

from .models import AuditEngagement, AuditFinding, ReconciliationEntry


class EngagementForm(StyledModelForm):
    class Meta:
        model = AuditEngagement
        fields = ["title", "period_start", "period_end", "scope", "lead_auditor"]
        widgets = {"period_start": DATE(), "period_end": DATE(), "scope": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth.models import User
        from django.db.models import Q
        self.fields["lead_auditor"].queryset = User.objects.filter(Q(groups__name__in=["Auditor", "Admin"]) | Q(is_superuser=True), is_active=True).distinct()

    def clean(self):
        d = super().clean()
        if d.get("period_start") and d.get("period_end") and d["period_end"] < d["period_start"]:
            raise forms.ValidationError("The period end cannot be before its start.")
        return d


class FindingForm(StyledModelForm):
    class Meta:
        model = AuditFinding
        fields = ["title", "severity", "category", "reference", "description", "recommendation"]
        widgets = {"description": forms.Textarea(attrs={"rows": 4}), "recommendation": forms.Textarea(attrs={"rows": 3})}


class ManagementResponseForm(StyledModelForm):
    class Meta:
        model = AuditFinding
        fields = ["status", "management_response"]
        widgets = {"management_response": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [c for c in AuditFinding.STATUS]
        self.fields["management_response"].required = True


class ReconciliationForm(StyledModelForm):
    class Meta:
        model = ReconciliationEntry
        fields = ["kind", "label", "amount", "counted_on", "note"]
        widgets = {"counted_on": DATE(), "amount": forms.NumberInput(attrs={"step": "0.01"})}


class CloseForm(StyledForm):
    opinion = forms.ChoiceField(choices=[c for c in AuditEngagement.OPINIONS if c[0]], label="Audit opinion")
    conclusion = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), label="Conclusion / summary for the committee")
    confirm = forms.BooleanField(label="I understand that closing locks every deposit and expense dated inside this period.")


class LogFilterForm(StyledForm):
    q = forms.CharField(required=False, label="Search", widget=forms.TextInput(attrs={"placeholder": "user, record, message"}))
    action = forms.ChoiceField(required=False, choices=[("", "All actions")] + __import__("audit.models", fromlist=["AuditLog"]).AuditLog.ACTIONS)
    model = forms.ChoiceField(required=False, choices=[("", "All records")])
    start = forms.DateField(required=False, widget=DATE())
    end = forms.DateField(required=False, widget=DATE())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import AuditLog
        models_ = AuditLog.objects.exclude(model="").values_list("model", flat=True).distinct().order_by("model")
        self.fields["model"].choices = [("", "All records")] + [(m, m.split(".")[-1]) for m in models_]


class AdHocRangeForm(StyledForm):
    start = forms.DateField(widget=DATE())
    end = forms.DateField(widget=DATE())
