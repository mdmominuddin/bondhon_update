from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="messaging_home"),
    path("compose/", views.compose, name="compose"),
    path("preview/", views.preview, name="sms_preview"),
    path("outbox/", views.outbox, name="sms_outbox"),
    path("outbox/process/", views.process_queue, name="sms_process"),
    path("outbox/<int:pk>/", views.sms_detail, name="sms_detail"),
    path("outbox/<int:pk>/retry/", views.sms_retry, name="sms_retry"),
    path("sent/", views.campaign_list, name="campaign_list"),
    path("sent/<int:pk>/", views.campaign_detail, name="campaign_detail"),
    path("templates/", views.template_list, name="template_list"),
    path("templates/add/", views.template_form, name="template_add"),
    path("templates/<int:pk>/", views.template_form, name="template_edit"),
]
