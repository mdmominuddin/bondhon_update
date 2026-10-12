from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit import checks
from audit.models import AuditEngagement, AuditLog, ImmutableError
from messaging import services as msg
from messaging.models import MessageTemplate, SMSMessage
from messaging.phone import normalize_bd_phone
from myapp import services
from myapp.models import (
    ContributionPlan, Deposit, Expense, ExpenseHead, PeriodLockedError, SiteSetting, SocietyMember,
)
from payments.models import OnlinePayment
from payments.gateways import sign


def make_user(name, role=None, superuser=False):
    u = User.objects.create_user(name, password="pw-12345-xyz", is_superuser=superuser, is_staff=superuser)
    if role:
        u.groups.add(Group.objects.get(name=role))
    return u


class Base(TestCase):
    def setUp(self):
        # TestCase wraps each test in a transaction, so on_commit hooks would never fire; run them immediately.
        patcher = mock.patch("django.db.transaction.on_commit", side_effect=lambda fn, *a, **k: fn())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.head = ExpenseHead.objects.create(name="General")
        site = SiteSetting.get()
        site.default_fund_head = self.head
        site.save()
        ContributionPlan.objects.create(description="Phase 1", amount=10000, start_date=date(2024, 1, 1), end_date=date(2024, 6, 30))
        self.admin = make_user("admin", "Admin", superuser=True)
        self.treasurer = make_user("treasurer", "Treasurer")
        self.auditor = make_user("auditor", "Auditor")
        self.m1 = SocietyMember.objects.create(name="Rahim Uddin", phone_number="01712345678", shares=2)
        self.m2 = SocietyMember.objects.create(name="Karim Ali", phone_number="123", shares=1)  # invalid number (like legacy data)
        self.member_user = make_user("rahim", "Member")
        self.m1.user = self.member_user
        self.m1.save()

    def login(self, user):
        self.client.logout()
        self.client.force_login(user)

    def deposit(self, member=None, amount="5000", when=None, **kw):
        return Deposit.objects.create(date=when or date(2024, 3, 1), member=member or self.m1, amount=Decimal(amount),
                                      fund_head=self.head, recorded_by=self.admin, **kw)


class PhoneAndSmsTests(TestCase):
    def test_phone_normalisation(self):
        for raw in ("01712345678", "+8801712345678", "8801712345678", "1712345678", "017 1234-5678", "০১৭১২৩৪৫৬৭৮"):
            self.assertEqual(normalize_bd_phone(raw), "8801712345678", raw)
        for raw in ("123", "", None, "0212345678", "01212345678", "017123456789"):
            self.assertIsNone(normalize_bd_phone(raw), raw)

    def test_segments(self):
        self.assertEqual(msg.analyze("a" * 160), ("gsm", 160, 1))
        self.assertEqual(msg.analyze("a" * 161)[2], 2)
        enc, length, seg = msg.analyze("প্রিয় সদস্য")
        self.assertEqual(enc, "unicode")
        self.assertEqual(msg.analyze("ব" * 71)[2], 2)

    def test_render_is_safe(self):
        self.assertEqual(msg.render("Hi {name} {x.__class__} {unknown}", {"name": "A"}), "Hi A {x.__class__} ")


class LedgerTests(Base):
    def test_receipt_numbers_sequential_per_year(self):
        a = self.deposit(when=date(2024, 3, 1))
        b = self.deposit(when=date(2024, 3, 2))
        c = self.deposit(when=date(2025, 1, 2))
        self.assertEqual((a.receipt_no, b.receipt_no, c.receipt_no), ("BND-2024-00001", "BND-2024-00002", "BND-2025-00001"))

    def test_totals_and_positions(self):
        self.deposit(amount="5000")
        self.deposit(self.m2, "10000")
        t = services.society_totals()
        self.assertEqual(t["expected"], Decimal(30000))  # 10,000/share * 3 shares
        self.assertEqual(t["deposits"], Decimal(15000))
        self.assertEqual(services.member_position(self.m1)["due"], Decimal(15000))
        self.assertEqual(services.member_position(self.m2)["due"], Decimal(0))

    def test_void_removes_from_totals(self):
        d = self.deposit(amount="5000")
        d.void(self.admin, "wrong member")
        self.assertEqual(services.society_totals()["deposits"], Decimal(0))
        self.assertTrue(Deposit.objects.get(pk=d.pk).is_void)

    def test_expense_counts_only_when_approved(self):
        e = Expense.objects.create(date=date(2024, 3, 3), description="Cement", expense_head=self.head, amount=Decimal(2000),
                                   created_by=self.m1, status="pending", recorded_by=self.treasurer)
        self.assertEqual(services.society_totals()["expenses"], 0)
        e.approve(self.admin)
        self.assertEqual(services.society_totals()["expenses"], Decimal(2000))

    def test_fifo_plan_breakdown(self):
        ContributionPlan.objects.create(description="Phase 2", amount=5000, start_date=date(2024, 7, 1), end_date=date(2024, 12, 31))
        self.deposit(amount="25000")  # m1 has 2 shares: phase1 = 20000, phase2 = 10000
        rows, advance = services.member_plan_breakdown(self.m1)
        self.assertEqual([r["paid"] for r in rows], [Decimal(20000), Decimal(5000)])
        self.assertEqual(rows[1]["due"], Decimal(5000))

    def test_amount_must_be_positive(self):
        with self.assertRaises(ValidationError):
            Deposit(date=date(2024, 1, 1), member=self.m1, amount=Decimal(0), fund_head=self.head).full_clean()


class AutoSmsTests(Base):
    def test_deposit_sends_exactly_one_receipt_sms(self):
        d = self.deposit(amount="5000")
        sms = SMSMessage.objects.filter(related_type="deposit", related_id=d.pk)
        self.assertEqual(sms.count(), 1)
        s = sms.get()
        self.assertEqual(s.status, "sent")  # console backend counts as delivered
        self.assertEqual(s.phone, "8801712345678")
        self.assertIn(d.receipt_no, s.body)
        self.assertIn("5,000", s.body)
        d.remark = "edited"
        d.save()
        self.assertEqual(SMSMessage.objects.filter(related_type="deposit", related_id=d.pk).count(), 1)

    def test_invalid_phone_is_skipped_not_lost(self):
        d = self.deposit(self.m2, "1000")
        s = SMSMessage.objects.get(related_type="deposit", related_id=d.pk)
        self.assertEqual(s.status, "skipped")
        self.assertIn("valid mobile", s.error)

    def test_switched_off_and_opt_out(self):
        site = SiteSetting.get(); site.sms_enabled = False; site.save()
        d = self.deposit()
        self.assertEqual(SMSMessage.objects.get(related_id=d.pk).status, "skipped")
        site.sms_enabled = True; site.save()
        self.m1.sms_opt_in = False; self.m1.save()
        d2 = self.deposit()
        self.assertEqual(SMSMessage.objects.get(related_id=d2.pk).status, "skipped")

    def test_bangla_template_selected_by_language_setting(self):
        site = SiteSetting.get(); site.sms_language = "bn"; site.save()
        d = self.deposit()
        s = SMSMessage.objects.get(related_id=d.pk)
        self.assertEqual(s.encoding, "unicode")
        self.assertIn("রসিদ", s.body)

    def test_inapp_notification_for_member_with_login(self):
        self.deposit()
        self.assertEqual(self.m1.notification_set.count(), 1)

    def test_provider_failure_never_breaks_deposit(self):
        site = SiteSetting.get(); site.sms_backend = "http"; site.save()  # not configured -> fails
        d = self.deposit()
        self.assertTrue(Deposit.objects.filter(pk=d.pk).exists())
        self.assertEqual(SMSMessage.objects.get(related_id=d.pk).status, "failed")


class AuditTests(Base):
    def test_changes_are_logged_with_diff_and_actor(self):
        self.login(self.admin)
        self.client.post(reverse("create_deposit"), {"date": "2024-03-01", "member": self.m1.pk, "amount": "5000", "fund_head": self.head.pk,
                                                    "method": "cash", "reference": "", "remark": ""})
        d = Deposit.objects.get()
        log = AuditLog.objects.filter(model="myapp.Deposit", action="create").get()
        self.assertEqual(log.username, "admin")
        self.assertEqual(log.changes["amount"][1], "5000.00")
        d.amount = Decimal("6000"); d.save()
        up = AuditLog.objects.filter(model="myapp.Deposit", action="update").get()
        self.assertEqual(up.changes["amount"], ["5000.00", "6000.00"])
        d.void(self.admin, "test")
        self.assertTrue(AuditLog.objects.filter(action="void").exists())

    def test_login_events(self):
        self.client.post(reverse("login"), {"username": "admin", "password": "wrong"})
        self.client.post(reverse("login"), {"username": "admin", "password": "pw-12345-xyz"})
        self.assertTrue(AuditLog.objects.filter(action="login_failed").exists())
        self.assertTrue(AuditLog.objects.filter(action="login", username="admin").exists())

    def test_chain_valid_then_detects_tampering(self):
        self.deposit(); self.deposit()
        self.assertTrue(AuditLog.verify_chain()[0])
        victim = AuditLog.objects.filter(model="myapp.Deposit").order_by("id").first()
        with connection.cursor() as c:  # bypass the ORM guards, as an attacker with DB access would
            c.execute("UPDATE audit_auditlog SET object_repr = 'forged' WHERE id = %s", [victim.id])
        ok, _, bad = AuditLog.verify_chain()
        self.assertFalse(ok)
        self.assertEqual(bad, victim.id)

    def test_log_is_immutable_via_orm(self):
        self.deposit()
        entry = AuditLog.objects.first()
        with self.assertRaises(ImmutableError):
            entry.delete()
        with self.assertRaises(ImmutableError):
            AuditLog.objects.all().update(message="x")
        entry.message = "changed"
        with self.assertRaises(ImmutableError):
            entry.save()

    def test_deleted_entry_breaks_chain(self):
        self.deposit(); self.deposit(); self.deposit()
        mid = list(AuditLog.objects.order_by("id").values_list("id", flat=True))[2]
        with connection.cursor() as c:
            c.execute("DELETE FROM audit_auditlog WHERE id = %s", [mid])
        self.assertFalse(AuditLog.verify_chain()[0])

    def test_password_hash_never_logged(self):
        make_user("someone", "Member")
        for log in AuditLog.objects.filter(model="auth.User"):
            self.assertNotIn("pbkdf2", str(log.changes))

    def test_period_lock_blocks_changes_until_reopened(self):
        d = self.deposit(when=date(2024, 3, 1))
        eng = AuditEngagement.objects.create(title="Q1", period_start=date(2024, 1, 1), period_end=date(2024, 3, 31), lead_auditor=self.auditor)
        self.login(self.auditor)
        r = self.client.post(reverse("audit_engagement_close", args=[eng.pk]), {"opinion": "unqualified", "conclusion": "ok", "confirm": "on"})
        eng.refresh_from_db()
        self.assertTrue(eng.locked)
        self.assertTrue(AuditLog.objects.filter(action="lock").exists())
        with self.assertRaises(PeriodLockedError):
            self.deposit(when=date(2024, 2, 1))
        with self.assertRaises(PeriodLockedError):
            d.void(self.admin, "x")
        self.deposit(when=date(2024, 4, 5))  # outside the period is fine
        self.login(self.admin)
        self.client.post(reverse("audit_engagement_reopen", args=[eng.pk]), {"reason": "late invoice"})
        eng.refresh_from_db()
        self.assertFalse(eng.locked)
        self.deposit(when=date(2024, 2, 1))
        self.assertTrue(AuditLog.objects.filter(action="unlock").exists())

    def test_cannot_close_unfinished_period(self):
        eng = AuditEngagement.objects.create(title="Future", period_start=date(2024, 1, 1), period_end=timezone.localdate() + timedelta(days=5))
        self.login(self.auditor)
        self.client.post(reverse("audit_engagement_close", args=[eng.pk]), {"opinion": "unqualified", "conclusion": "ok", "confirm": "on"})
        eng.refresh_from_db()
        self.assertFalse(eng.locked)

    def test_analytics_detect_planted_problems(self):
        a = self.deposit(amount="5000", reference="TRX1", method="bkash")
        self.deposit(amount="5000", reference="TRX1", method="bkash")            # duplicate + reused reference
        e = Expense.objects.create(date=date(2024, 3, 3), description="Mystery", expense_head=self.head, amount=Decimal(90000),
                                   created_by=self.m1, status="approved", recorded_by=self.admin, approved_by=self.admin)
        a.amount = Decimal(5500); a.save()                                       # material edit
        results = {r.code: r for r in checks.run_all(date(2024, 1, 1), date(2024, 12, 31))}
        self.assertEqual(results["DUP_REF"].count, 1)
        self.assertEqual(results["UNDOCUMENTED"].count, 1)
        self.assertEqual(results["LARGE_EXPENSE"].count, 1)
        self.assertEqual(results["SELF_APPROVED"].count, 1)
        self.assertEqual(results["MATERIAL_EDIT"].count, 1)
        self.assertEqual(results["BAD_PHONE"].count, 1)
        self.assertTrue(results["LOG_CHAIN"].passed)

    def test_receipt_gap_and_summary_mismatch_detected(self):
        a = self.deposit(); self.deposit(); c = self.deposit()
        with connection.cursor() as cur:
            cur.execute("DELETE FROM myapp_deposit WHERE id = %s", [a.pk + 1])
        from myapp.models import MonthlyFinancialSummary
        MonthlyFinancialSummary.objects.create(month=date(2024, 3, 1), total_deposits=Decimal(999999))
        results = {r.code: r for r in checks.run_all(date(2024, 1, 1), date(2024, 12, 31))}
        self.assertEqual(results["RECEIPT_GAPS"].count, 1)
        self.assertEqual(results["SUMMARY_MISMATCH"].count, 1)
        services.rebuild_monthly_summary()
        self.assertTrue(checks.check_summary_mismatch(date(2024, 1, 1), date(2024, 12, 31)).passed)


class AccessTests(Base):
    PAGES_OFFICE = ["home", "member_list", "deposit_list", "expense_list", "members_with_dues", "reports", "monthly_summary", "fund_report",
                    "collection_report", "contribution_plan_list", "project_list", "payment_list", "messaging_home", "sms_outbox",
                    "template_list", "campaign_list", "audit_engagements"]
    PAGES_ADMIN_ONLY = ["site_settings", "user_list", "user_add", "create_contribution_plan"]
    PAGES_AUDITOR = ["audit_overview", "audit_log", "audit_checks"]
    PAGES_FINANCE = ["create_deposit", "create_expense", "compose", "member_add", "member_import", "template_add", "project_add"]
    PAGES_MEMBER = ["home", "deposit_list", "my_payments", "pay", "notifications", "monthly_summary", "expense_list"]

    def status(self, name, **kw):
        return self.client.get(reverse(name, kwargs=kw or None)).status_code

    def test_anonymous_redirected_everywhere(self):
        for name in self.PAGES_OFFICE + self.PAGES_ADMIN_ONLY + self.PAGES_AUDITOR + self.PAGES_FINANCE + self.PAGES_MEMBER:
            self.assertEqual(self.status(name), 302, name)
        d = self.deposit()
        for path in (f"/view_deposits/{self.m1.pk}/", f"/deposits/{d.pk}/", f"/members/{self.m1.pk}/", "/dues_list/", "/contribution-plans/"):
            self.assertEqual(self.client.get(path).status_code, 302, path)  # the old app exposed several of these publicly

    def test_admin_can_open_everything(self):
        self.login(self.admin)
        d = self.deposit()
        Expense.objects.create(date=date(2024, 3, 3), description="x", expense_head=self.head, amount=1, created_by=self.m1, status="approved")
        for name in self.PAGES_OFFICE + self.PAGES_ADMIN_ONLY + self.PAGES_AUDITOR + self.PAGES_FINANCE:
            self.assertEqual(self.status(name), 200, name)
        for path in (f"/members/{self.m1.pk}/", f"/deposits/{d.pk}/", f"/deposits/{d.pk}/edit/", "/payments/", "/messages/preview/?body=hi"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_treasurer_limits(self):
        self.login(self.treasurer)
        for name in self.PAGES_OFFICE + self.PAGES_FINANCE:
            self.assertEqual(self.status(name), 200, name)
        for name in self.PAGES_ADMIN_ONLY + self.PAGES_AUDITOR:
            self.assertEqual(self.status(name), 403, name)

    def test_auditor_is_read_only(self):
        self.login(self.auditor)
        for name in self.PAGES_OFFICE + self.PAGES_AUDITOR:
            self.assertEqual(self.status(name), 200, name)
        for name in self.PAGES_FINANCE + self.PAGES_ADMIN_ONLY:
            self.assertEqual(self.status(name), 403, name)
        d = self.deposit()
        self.assertEqual(self.client.post(reverse("deposit_void", args=[d.pk]), {"reason": "x"}).status_code, 403)

    def test_member_sees_only_own_data(self):
        self.login(self.member_user)
        d_own, d_other = self.deposit(self.m1), self.deposit(self.m2)
        for name in self.PAGES_MEMBER:
            self.assertEqual(self.status(name), 200, name)
        for name in ("member_list", "members_with_dues", "reports", "payment_list", "messaging_home", "audit_engagements", "site_settings", "create_deposit"):
            self.assertEqual(self.status(name), 403, name)
        self.assertEqual(self.client.get(f"/deposits/{d_own.pk}/").status_code, 200)
        self.assertEqual(self.client.get(f"/deposits/{d_other.pk}/").status_code, 403)
        self.assertEqual(self.client.get(f"/members/{self.m2.pk}/").status_code, 403)
        self.assertEqual(self.client.get(f"/members/{self.m1.pk}/").status_code, 200)
        body = self.client.get(reverse("deposit_list")).content.decode()
        self.assertIn(d_own.receipt_no, body)
        self.assertNotIn(d_other.receipt_no, body)

    def test_members_cannot_see_expenses_when_switched_off(self):
        site = SiteSetting.get(); site.members_see_expenses = False; site.save()
        self.login(self.member_user)
        self.assertEqual(self.status("expense_list"), 403)

    def test_forced_password_change(self):
        self.m1.must_change_password = True; self.m1.save()
        self.login(self.member_user)
        self.assertRedirects(self.client.get(reverse("home")), reverse("password_change"))
        r = self.client.post(reverse("password_change"), {"old_password": "pw-12345-xyz", "new_password1": "Brand-new-pass-77", "new_password2": "Brand-new-pass-77"})
        self.assertRedirects(r, reverse("home"))
        self.m1.refresh_from_db()
        self.assertFalse(self.m1.must_change_password)

    def test_logout_requires_post(self):
        self.login(self.admin)
        self.client.get(reverse("logout"))
        self.assertEqual(self.status("home"), 200)
        self.client.post(reverse("logout"))
        self.assertEqual(self.status("home"), 302)


class WorkflowTests(Base):
    def test_treasurer_expense_needs_admin_approval(self):
        self.login(self.treasurer)
        r = self.client.post(reverse("create_expense"), {"date": "2024-03-03", "description": "Sand", "expense_head": self.head.pk, "amount": "3000",
                                                        "created_by": self.m1.pk, "remark": "", "voucher_no": "V-1"})
        e = Expense.objects.get()
        self.assertEqual(e.status, "pending")
        self.assertEqual(services.society_totals()["expenses"], 0)
        self.assertEqual(self.client.post(reverse("expense_approve", args=[e.pk])).status_code, 403)
        self.login(self.admin)
        self.client.post(reverse("expense_approve", args=[e.pk]))
        e.refresh_from_db()
        self.assertEqual(e.status, "approved")
        self.assertEqual(e.approved_by, self.admin)
        self.assertEqual(services.society_totals()["expenses"], Decimal(3000))
        self.assertTrue(AuditLog.objects.filter(action="approve", model="myapp.Expense").exists())

    def test_treasurer_edit_of_approved_expense_goes_back_to_pending(self):
        e = Expense.objects.create(date=date(2024, 3, 3), description="Sand", expense_head=self.head, amount=Decimal(3000), created_by=self.m1,
                                   status="approved", recorded_by=self.admin, approved_by=self.admin)
        self.login(self.treasurer)
        self.client.post(reverse("expense_edit", args=[e.pk]), {"date": "2024-03-03", "description": "Sand", "expense_head": self.head.pk, "amount": "3500",
                                                               "created_by": self.m1.pk, "remark": ""})
        e.refresh_from_db()
        self.assertEqual((e.status, e.amount), ("pending", Decimal(3500)))

    def test_admin_deposit_form_end_to_end(self):
        self.login(self.admin)
        r = self.client.post(reverse("create_deposit"), {"date": "2024-03-01", "member": self.m1.pk, "amount": "7500", "fund_head": self.head.pk, "method": "bkash", "reference": "ABC", "remark": ""})
        d = Deposit.objects.get()
        self.assertRedirects(r, reverse("deposit_detail", args=[d.pk]))
        self.assertEqual(d.source, "admin")
        page = self.client.get(reverse("deposit_detail", args=[d.pk])).content.decode()
        self.assertIn("Seven Thousand Five Hundred Taka only", page)
        self.assertEqual(SMSMessage.objects.filter(trigger="payment_received").count(), 1)

    def test_deposit_into_locked_period_is_a_form_error_not_a_crash(self):
        AuditEngagement.objects.create(title="Q1", period_start=date(2024, 1, 1), period_end=date(2024, 3, 31), status="closed", locked=True)
        self.login(self.admin)
        r = self.client.post(reverse("create_deposit"), {"date": "2024-02-01", "member": self.m1.pk, "amount": "100", "fund_head": self.head.pk, "method": "cash", "remark": ""})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "locked")
        self.assertEqual(Deposit.objects.count(), 0)

    def test_member_login_creation_and_sms(self):
        self.login(self.admin)
        self.client.post(reverse("member_create_login", args=[self.m2.pk]), {"send_sms": "1"})
        self.m2.refresh_from_db()
        self.assertIsNotNone(self.m2.user)
        self.assertTrue(self.m2.must_change_password)
        self.assertTrue(self.m2.user.groups.filter(name="Member").exists())

    def test_csv_import_updates_phones(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.login(self.admin)
        csv_bytes = f"id,name,phone_number\n{self.m2.pk},Karim Ali,01812345678\n".encode()
        self.client.post(reverse("member_import"), {"file": SimpleUploadedFile("m.csv", csv_bytes)})
        self.m2.refresh_from_db()
        self.assertEqual(self.m2.phone_number, "01812345678")


class OnlinePaymentTests(Base):
    def pay(self, **over):
        data = {"amount": "3000", "method": "bkash", "trx_id": "8n7a6d5e", "sender_number": "01712345678", "note": ""}
        data.update(over)
        return self.client.post(reverse("pay"), data)

    def test_manual_flow_verify_creates_deposit_and_sms(self):
        self.login(self.member_user)
        r = self.pay()
        p = OnlinePayment.objects.get()
        self.assertRedirects(r, reverse("payment_detail", args=[p.pk]))
        self.assertEqual((p.status, p.trx_id), ("pending", "8N7A6D5E"))
        self.assertEqual(Deposit.objects.count(), 0)                          # not in the books yet
        self.assertEqual(SMSMessage.objects.filter(trigger="payment_pending").count(), 1)
        self.login(self.treasurer)
        r = self.client.post(reverse("payment_verify", args=[p.pk]), {"fund_head": self.head.pk, "note": "checked"})
        p.refresh_from_db()
        self.assertEqual(p.status, "success")
        d = Deposit.objects.get()
        self.assertEqual((d.source, d.amount, d.member, d.reference), ("online", Decimal(3000), self.m1, "8N7A6D5E"))
        self.assertEqual(SMSMessage.objects.filter(trigger="payment_received", related_id=d.pk).count(), 1)
        self.client.post(reverse("payment_verify", args=[p.pk]), {"fund_head": self.head.pk})   # double click
        self.assertEqual(Deposit.objects.count(), 1)

    def test_reuse_of_transaction_id_blocked(self):
        self.login(self.member_user)
        self.pay()
        r = self.pay()
        self.assertEqual(OnlinePayment.objects.count(), 1)
        self.assertContains(r, "already been submitted")

    def test_reject_notifies_member_and_frees_trx(self):
        self.login(self.member_user)
        self.pay()
        p = OnlinePayment.objects.get()
        self.login(self.treasurer)
        self.client.post(reverse("payment_reject", args=[p.pk]), {"reason": "TrxID not found"})
        p.refresh_from_db()
        self.assertEqual(p.status, "rejected")
        self.assertIn("TrxID not found", SMSMessage.objects.get(trigger="payment_rejected").body)
        self.assertEqual(Deposit.objects.count(), 0)
        self.login(self.member_user)
        self.pay(trx_id="8N7A6D5E")
        self.assertEqual(OnlinePayment.objects.count(), 2)

    def test_limits_and_validation(self):
        self.login(self.member_user)
        self.pay(amount="50")
        self.pay(amount="9999999")
        self.assertEqual(OnlinePayment.objects.count(), 0)
        for i in range(3):
            self.pay(trx_id=f"T{i}")
        self.pay(trx_id="T9")
        self.assertEqual(OnlinePayment.objects.count(), 3)  # max 3 waiting payments

    def test_only_member_role_can_pay(self):
        self.login(self.treasurer)
        self.assertEqual(self.client.get(reverse("pay")).status_code, 403)

    def test_member_cannot_see_or_verify_other_payments(self):
        p = OnlinePayment.objects.create(member=self.m2, amount=100, method="bkash", trx_id="X1")
        self.login(self.member_user)
        self.assertEqual(self.client.get(reverse("payment_detail", args=[p.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("payment_verify", args=[p.pk]), {"fund_head": self.head.pk}).status_code, 403)

    def test_sandbox_gateway_flow(self):
        site = SiteSetting.get(); site.online_gateway = "sandbox"; site.save()
        self.login(self.member_user)
        r = self.client.post(reverse("pay"), {"amount": "2000"})
        p = OnlinePayment.objects.get()
        self.assertRedirects(r, reverse("payment_sandbox", args=[p.reference]), fetch_redirect_response=False)
        bad = self.client.get(reverse("payment_sandbox_return", args=[p.reference]) + "?status=success&sig=forged")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(Deposit.objects.count(), 0)
        url = reverse("payment_sandbox_return", args=[p.reference]) + f"?status=success&sig={sign(p.reference, 'success')}"
        self.client.get(url); self.client.get(url)                                             # replayed return URL
        p.refresh_from_db()
        self.assertEqual(p.status, "success")
        self.assertEqual(Deposit.objects.count(), 1)
        self.assertEqual(SMSMessage.objects.filter(trigger="payment_received").count(), 1)

    def test_sandbox_failure_and_cancel(self):
        site = SiteSetting.get(); site.online_gateway = "sandbox"; site.save()
        self.login(self.member_user)
        self.client.post(reverse("pay"), {"amount": "2000"})
        p = OnlinePayment.objects.get()
        self.client.get(reverse("payment_sandbox_return", args=[p.reference]) + f"?status=failed&sig={sign(p.reference, 'failed')}")
        p.refresh_from_db()
        self.assertEqual(p.status, "failed")
        self.assertEqual(Deposit.objects.count(), 0)

    def test_sslcommerz_return_without_validation_does_not_credit(self):
        site = SiteSetting.get(); site.online_gateway = "sslcommerz"; site.save()
        p = OnlinePayment.objects.create(member=self.m1, amount=500, method="card", gateway="sslcommerz", status="initiated")
        r = self.client.post(reverse("payment_gateway_return", args=["sslcommerz", "success"]), {"tran_id": p.reference, "val_id": "forged", "status": "VALID"})
        p.refresh_from_db()
        self.assertNotEqual(p.status, "success")        # unconfigured/failed validation must never credit money
        self.assertEqual(Deposit.objects.count(), 0)

    def test_orphan_check(self):
        OnlinePayment.objects.create(member=self.m1, amount=500, method="bkash", status="success")
        self.assertFalse(checks.check_payments_without_deposit(date(2024, 1, 1), date(2030, 1, 1)).passed)


class MessagingUiTests(Base):
    def test_campaign_all_and_dues(self):
        self.deposit(self.m1, "20000")  # m1 fully paid (2 shares * 10,000)
        self.login(self.treasurer)
        self.client.post(reverse("compose"), {"audience": "dues", "body": "Dear {member_name}, please pay Tk {due}.", "title": "Reminder"})
        msgs = SMSMessage.objects.filter(trigger="campaign")
        self.assertEqual(msgs.count(), 1)                # only m2 owes
        self.assertEqual(msgs.get().member, self.m2)
        self.assertIn("Karim Ali", msgs.get().body)
        self.assertEqual(msgs.get().status, "skipped")   # m2 has a bad number

    def test_unknown_placeholder_rejected(self):
        self.login(self.treasurer)
        r = self.client.post(reverse("compose"), {"audience": "all", "body": "Hello {nope}"})
        self.assertContains(r, "Unknown placeholder")

    def test_template_edit_changes_auto_message(self):
        t = MessageTemplate.objects.get(key="payment_received", language="en")
        self.login(self.treasurer)
        self.client.post(reverse("template_edit", args=[t.pk]), {"title": t.title, "key": t.key, "language": "en", "event": t.event, "body": "Thanks {member_name}: Tk {amount} rcpt {receipt_no}", "is_active": "on"})
        d = self.deposit(amount="1500")
        self.assertTrue(SMSMessage.objects.get(related_id=d.pk).body.startswith("Thanks Rahim Uddin: Tk 1,500 rcpt BND-"))

    def test_retry_failed(self):
        site = SiteSetting.get(); site.sms_backend = "http"; site.save()
        d = self.deposit()
        s = SMSMessage.objects.get(related_id=d.pk)
        self.assertEqual(s.status, "failed")
        site.sms_backend = "console"; site.save()
        self.login(self.treasurer)
        self.client.post(reverse("sms_retry", args=[s.pk]))
        s.refresh_from_db()
        self.assertEqual((s.status, s.attempts), ("sent", 2))


class FormattingTests(TestCase):
    def test_bangladeshi_grouping(self):
        from myapp.templatetags.bondhon_extras import bdnum
        self.assertEqual(bdnum(325000, 0), "3,25,000")
        self.assertEqual(bdnum(12345678.5), "1,23,45,678.50")
        self.assertEqual(bdnum(-1500), "-1,500.00")
        self.assertEqual(bdnum(0), "0.00")
