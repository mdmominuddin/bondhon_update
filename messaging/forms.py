import re

from django import forms

from myapp.forms import StyledForm, StyledModelForm
from myapp.models import SocietyMember

from .models import Campaign, MessageTemplate
from .services import PLACEHOLDERS


def _validate_placeholders(body):
    unknown = sorted(set(re.findall(r"\{(\w+)\}", body)) - set(PLACEHOLDERS))
    if unknown:
        raise forms.ValidationError("Unknown placeholder(s): " + ", ".join("{%s}" % u for u in unknown))
    return body


class TemplateForm(StyledModelForm):
    class Meta:
        model = MessageTemplate
        fields = ["title", "key", "language", "event", "body", "is_active"]
        widgets = {"body": forms.Textarea(attrs={"rows": 5, "data-sms-body": "1"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.is_system:
            for f in ("key", "language", "event"):
                self.fields[f].disabled = True

    def clean_body(self):
        return _validate_placeholders(self.cleaned_data["body"])

    def clean(self):
        d = super().clean()
        if d.get("key") and d.get("language"):
            qs = MessageTemplate.objects.filter(key=d["key"], language=d["language"])
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError("A template with this key and language already exists.")
        return d


class ComposeForm(StyledForm):
    audience = forms.ChoiceField(choices=Campaign.AUDIENCES, initial="selected", widget=forms.RadioSelect)
    members = forms.ModelMultipleChoiceField(queryset=SocietyMember.objects.none(), required=False, label="Recipients")
    template = forms.ModelChoiceField(queryset=MessageTemplate.objects.none(), required=False, empty_label="- write a custom message -")
    title = forms.CharField(max_length=160, required=False, label="Campaign name (for your records)")
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 6, "data-sms-body": "1"}), label="Message")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["members"].queryset = SocietyMember.objects.filter(is_active=True)
        self.fields["members"].widget.attrs["class"] = ""
        self.fields["audience"].widget.attrs["class"] = ""
        self.fields["template"].queryset = MessageTemplate.objects.filter(is_active=True)

    def clean_body(self):
        return _validate_placeholders(self.cleaned_data["body"])

    def clean(self):
        d = super().clean()
        if d.get("audience") == "selected" and not d.get("members"):
            self.add_error("members", "Choose at least one member.")
        return d
