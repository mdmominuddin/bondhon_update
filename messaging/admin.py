from django.contrib import admin

from .models import Campaign, MessageTemplate, SMSMessage

admin.site.register(MessageTemplate)
admin.site.register(Campaign)


@admin.register(SMSMessage)
class SMSMessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "phone", "trigger", "status", "segments")
    list_filter = ("status", "trigger")
    search_fields = ("phone", "body")
