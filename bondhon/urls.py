from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Bondhon Management and Auditing System"
admin.site.site_title = "Bondhon MAS Admin"
admin.site.index_title = "Back-office administration"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("audit/", include("audit.urls")),
    path("messages/", include("messaging.urls")),
    path("payments/", include("payments.urls")),
    path("", include("myapp.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
