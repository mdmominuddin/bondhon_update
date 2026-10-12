from django.db import migrations

TEMPLATES = [
    # key, language, title, event, body
    ("payment_received", "en", "Payment received", "payment_received",
     "Dear {member_name}, your payment of Tk {amount} to {society_name} was received on {date}. Receipt {receipt_no}. "
     "Total paid: Tk {total_paid}, due: Tk {due}. Thank you."),
    ("payment_received", "bn", "Payment received (Bangla)", "payment_received",
     "প্রিয় {member_name}, {society_name}-এ আপনার ৳{amount} জমা {date} তারিখে গৃহীত হয়েছে। রসিদ নং {receipt_no}। "
     "মোট জমা ৳{total_paid}, বকেয়া ৳{due}। ধন্যবাদ।"),
    ("payment_pending", "en", "Payment submitted", "payment_pending",
     "Dear {member_name}, we received your payment request of Tk {amount} (ref {reference}). "
     "It will be confirmed after verification. - {society_name}"),
    ("payment_pending", "bn", "Payment submitted (Bangla)", "payment_pending",
     "প্রিয় {member_name}, আপনার ৳{amount} পেমেন্টের অনুরোধ (রেফ {reference}) পেয়েছি। যাচাইয়ের পর নিশ্চিত করা হবে। - {society_name}"),
    ("payment_rejected", "en", "Payment rejected", "payment_rejected",
     "Dear {member_name}, your payment request of Tk {amount} (ref {reference}) could not be verified: {reason}. "
     "Please contact the treasurer. - {society_name}"),
    ("payment_rejected", "bn", "Payment rejected (Bangla)", "payment_rejected",
     "প্রিয় {member_name}, আপনার ৳{amount} পেমেন্ট (রেফ {reference}) যাচাই করা যায়নি: {reason}। অনুগ্রহ করে কোষাধ্যক্ষের সাথে যোগাযোগ করুন। - {society_name}"),
    ("due_reminder", "en", "Dues reminder", "due_reminder",
     "Dear {member_name}, your outstanding due to {society_name} is Tk {due} (paid Tk {total_paid} of Tk {expected}). "
     "Please pay at your earliest convenience."),
    ("due_reminder", "bn", "Dues reminder (Bangla)", "due_reminder",
     "প্রিয় {member_name}, {society_name}-এ আপনার বকেয়া ৳{due} (মোট ৳{expected}-এর মধ্যে জমা ৳{total_paid})। অনুগ্রহ করে দ্রুত পরিশোধ করুন।"),
    ("account_created", "en", "Portal login created", "account_created",
     "Dear {member_name}, your {society_name} portal login: {login_url} user {username} password {temp_password}. "
     "You must change the password at first sign-in."),
    ("account_created", "bn", "Portal login created (Bangla)", "account_created",
     "প্রিয় {member_name}, {society_name} পোর্টাল: {login_url} ইউজার {username} পাসওয়ার্ড {temp_password}। প্রথমবার লগইনের পর পাসওয়ার্ড পরিবর্তন করুন।"),
]


def seed(apps, schema_editor):
    MessageTemplate = apps.get_model("messaging", "MessageTemplate")
    for key, lang, title, event, body in TEMPLATES:
        MessageTemplate.objects.get_or_create(
            key=key, language=lang,
            defaults={"title": title, "event": event, "body": body, "is_system": True, "is_active": True},
        )


class Migration(migrations.Migration):
    dependencies = [("messaging", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
