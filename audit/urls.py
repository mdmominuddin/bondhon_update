from django.urls import path

from . import views

urlpatterns = [
    path("", views.overview, name="audit_overview"),
    path("log/", views.log_list, name="audit_log"),
    path("log/verify/", views.verify_chain, name="audit_verify"),
    path("log/<int:pk>/", views.log_detail, name="audit_log_detail"),
    path("analytics/", views.adhoc_checks, name="audit_checks"),
    path("summaries/rebuild/", views.rebuild_summaries, name="audit_rebuild_summaries"),

    path("engagements/", views.engagement_list, name="audit_engagements"),
    path("engagements/add/", views.engagement_form, name="audit_engagement_add"),
    path("engagements/<int:pk>/", views.engagement_detail, name="audit_engagement"),
    path("engagements/<int:pk>/edit/", views.engagement_form, name="audit_engagement_edit"),
    path("engagements/<int:pk>/checks/", views.engagement_run_checks, name="audit_engagement_checks"),
    path("engagements/<int:pk>/raise/", views.raise_finding, name="audit_raise_finding"),
    path("engagements/<int:engagement_pk>/findings/add/", views.finding_form, name="audit_finding_add"),
    path("engagements/<int:pk>/reconcile/add/", views.reconciliation_add, name="audit_recon_add"),
    path("engagements/<int:pk>/close/", views.engagement_close, name="audit_engagement_close"),
    path("engagements/<int:pk>/reopen/", views.engagement_reopen, name="audit_engagement_reopen"),
    path("engagements/<int:pk>/report/", views.engagement_report, name="audit_engagement_report"),
    path("findings/<int:pk>/edit/", views.finding_form, name="audit_finding_edit"),
    path("findings/<int:pk>/respond/", views.finding_respond, name="audit_finding_respond"),
    path("reconcile/<int:pk>/delete/", views.reconciliation_delete, name="audit_recon_delete"),
]
