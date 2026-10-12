from django.contrib import admin

from .models import OnlinePayment


@admin.register(OnlinePayment)
class OnlinePaymentAdmin(admin.ModelAdmin):
    list_display = ("reference", "member", "amount", "method", "status", "created_at")
    list_filter = ("status", "method", "gateway")
    search_fields = ("reference", "trx_id", "member__name")
