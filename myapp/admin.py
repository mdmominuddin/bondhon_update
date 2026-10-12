from django.contrib import admin

from .models import (
    BuildingProject, Category, ContributionPlan, Deposit, DueDeposit, Expense, ExpenseCategory, ExpenseHead,
    MonthlyFinancialSummary, Notification, SiteSetting, SocietyMember,
)

# The day-to-day work happens in the Bondhon UI.  The Django admin is a
# back-office tool: ledger rows are read-only here so they can only change
# through the application (which enforces approvals, period locks and the audit trail).


class ReadOnlyLedgerAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SocietyMember)
class SocietyMemberAdmin(admin.ModelAdmin):
    list_display = ("name", "society_designation", "phone_number", "shares", "joining_date", "is_active")
    search_fields = ("name", "phone_number")
    list_filter = ("society_designation", "is_active")


@admin.register(Deposit)
class DepositAdmin(ReadOnlyLedgerAdmin):
    list_display = ("receipt_no", "date", "member", "amount", "fund_head", "method", "source", "is_void")
    search_fields = ("receipt_no", "member__name", "reference")
    list_filter = ("date", "method", "source", "is_void")


@admin.register(Expense)
class ExpenseAdmin(ReadOnlyLedgerAdmin):
    list_display = ("date", "description", "expense_head", "amount", "status", "is_void")
    search_fields = ("description", "voucher_no")
    list_filter = ("date", "status", "is_void", "expense_head")


@admin.register(MonthlyFinancialSummary)
class MonthlyFinancialSummaryAdmin(ReadOnlyLedgerAdmin):
    list_display = ("month", "total_deposits", "total_expenses", "balance")


for model in (ExpenseHead, ContributionPlan, BuildingProject, SiteSetting, DueDeposit, Notification, Category, ExpenseCategory):
    admin.site.register(model)
