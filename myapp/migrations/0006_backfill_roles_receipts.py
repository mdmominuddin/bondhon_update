"""Data migration for the Management & Auditing upgrade.

* numbers every existing deposit with a receipt (oldest first, per year)
* marks every pre-existing expense as approved (it was already in the books)
* creates the Admin / Treasurer / Auditor / Member roles
* superusers become Admin; other existing users keep their old ability to
  record entries, so they become Treasurer (change under Users & Roles)
* creates the single SiteSetting row
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Deposit = apps.get_model("myapp", "Deposit")
    Expense = apps.get_model("myapp", "Expense")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")
    SiteSetting = apps.get_model("myapp", "SiteSetting")
    ExpenseHead = apps.get_model("myapp", "ExpenseHead")

    site, _ = SiteSetting.objects.get_or_create(pk=1)
    prefix = site.receipt_prefix or "BND"

    counters = {}
    for d in Deposit.objects.filter(receipt_no__isnull=True).order_by("date", "id"):
        counters[d.date.year] = counters.get(d.date.year, 0) + 1
        d.receipt_no = f"{prefix}-{d.date.year}-{counters[d.date.year]:05d}"
        d.save(update_fields=["receipt_no"])

    Expense.objects.all().update(status="approved")

    groups = {name: Group.objects.get_or_create(name=name)[0] for name in ("Admin", "Treasurer", "Auditor", "Member")}
    for user in User.objects.all():
        target = groups["Admin"] if user.is_superuser else groups["Treasurer"]
        user.groups.add(target)

    head = ExpenseHead.objects.order_by("id").first()
    if head and site.default_fund_head_id is None:
        site.default_fund_head = head
        site.save()


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("myapp", "0005_management_audit_upgrade"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
