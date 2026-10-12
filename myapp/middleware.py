from django.shortcuts import redirect
from django.urls import resolve, Resolver404


class ForcePasswordChangeMiddleware:
    """Members created with a temporary password must choose their own first."""
    ALLOWED = {"password_change", "logout"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            member = getattr(user, "member_profile", None)
            if member is not None and member.must_change_password:
                try:
                    name = resolve(request.path_info).url_name
                except Resolver404:
                    name = None
                if name not in self.ALLOWED and not request.path_info.startswith(("/static/", "/media/")):
                    return redirect("password_change")
        return self.get_response(request)
