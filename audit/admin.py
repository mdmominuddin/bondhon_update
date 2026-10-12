from django.contrib import admin

from .models import AuditEngagement, AuditFinding, AuditLog, ReconciliationEntry


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "username", "action", "model", "object_repr")
    list_filter = ("action", "model")
    search_fields = ("username", "object_repr", "message")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(AuditEngagement)
admin.site.register(AuditFinding)
admin.site.register(ReconciliationEntry)
