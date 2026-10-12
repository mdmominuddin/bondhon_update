from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    # --- authentication ---
    path("", views.user_login, name="login"),
    path("logout/", views.user_logout, name="logout"),
    path("register/", views.register, name="register"),
    path("password/change/", views.password_change, name="password_change"),

    # --- dashboards / member portal ---
    path("home/", views.home, name="home"),
    path("help/", views.help_guide, name="help_guide"),
    path("me/notifications/", views.notifications, name="notifications"),

    # --- members ---
    path("members/", views.member_list, name="member_list"),
    path("members/add/", views.member_add, name="member_add"),
    path("members/import/", views.member_import, name="member_import"),
    path("members/<int:pk>/", views.member_detail, name="member_detail"),
    path("members/<int:pk>/edit/", views.member_edit, name="member_edit"),
    path("members/<int:pk>/login/", views.member_create_login, name="member_create_login"),
    path("view_deposits/<int:member_id>/", views.view_deposits, name="view_deposits"),

    # --- deposits ---
    path("deposit/", views.create_deposit, name="create_deposit"),
    path("deposits/", views.deposit_list, name="deposit_list"),
    path("deposits/<int:pk>/", views.deposit_detail, name="deposit_detail"),
    path("deposits/<int:pk>/edit/", views.deposit_edit, name="deposit_edit"),
    path("deposits/<int:pk>/void/", views.deposit_void, name="deposit_void"),
    path("date_wise_deposit/", RedirectView.as_view(pattern_name="deposit_list", query_string=True), name="date_wise_deposit"),

    # --- expenses ---
    path("expense/", views.create_expense, name="create_expense"),
    path("expenses/", views.expense_list, name="expense_list"),
    path("expenses/<int:pk>/", views.expense_detail, name="expense_detail"),
    path("expenses/<int:pk>/edit/", views.expense_edit, name="expense_edit"),
    path("expenses/<int:pk>/approve/", views.expense_approve, name="expense_approve"),
    path("expenses/<int:pk>/reject/", views.expense_reject, name="expense_reject"),
    path("expenses/<int:pk>/void/", views.expense_void, name="expense_void"),
    path("date_wise_expense/", RedirectView.as_view(pattern_name="expense_list", query_string=True), name="date_wise_expense"),

    # --- plans & projects ---
    path("contribution-plans/", views.contribution_plan_list, name="contribution_plan_list"),
    path("contribution-plan/<int:pk>/", views.contribution_plan_detail, name="contribution_plan_detail"),
    path("create-contribution-plan/", views.create_contribution_plan, name="create_contribution_plan"),
    path("projects/", views.project_list, name="project_list"),
    path("projects/add/", views.project_form, name="project_add"),
    path("projects/<int:pk>/edit/", views.project_form, name="project_edit"),

    # --- reports ---
    path("reports/", views.reports_index, name="reports"),
    path("monthly-summary/", views.monthly_summary, name="monthly_summary"),
    path("monthly-summary/<int:year>/<int:month>/", views.monthly_summary, name="monthly_summary_by_date"),
    path("month_wise_summary/", RedirectView.as_view(pattern_name="monthly_summary"), name="month_wise_summary"),
    path("dues_list/", views.members_with_dues, name="members_with_dues"),
    path("reports/funds/", views.fund_report, name="fund_report"),
    path("reports/collection/", views.collection_report, name="collection_report"),

    # --- administration ---
    path("settings/", views.site_settings, name="site_settings"),
    path("users/", views.user_list, name="user_list"),
    path("users/add/", views.user_form, name="user_add"),
    path("users/<int:pk>/", views.user_form, name="user_edit"),
    path("users/<int:pk>/reset-password/", views.user_reset_password, name="user_reset_password"),
]
