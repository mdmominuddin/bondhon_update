from django.urls import path

from . import views

urlpatterns = [
    path("", views.payment_list, name="payment_list"),
    path("deposit-money/", views.pay, name="pay"),
    path("mine/", views.my_payments, name="my_payments"),
    path("<int:pk>/", views.payment_detail, name="payment_detail"),
    path("<int:pk>/verify/", views.verify_payment, name="payment_verify"),
    path("<int:pk>/reject/", views.reject_payment, name="payment_reject"),
    path("<int:pk>/cancel/", views.cancel_payment, name="payment_cancel"),
    path("sandbox/<str:reference>/", views.sandbox_page, name="payment_sandbox"),
    path("sandbox/<str:reference>/return/", views.sandbox_return, name="payment_sandbox_return"),
    path("gateway/<str:gateway>/<str:outcome>/", views.gateway_return, name="payment_gateway_return"),
]
