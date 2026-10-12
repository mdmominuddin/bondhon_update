from .context import reset_context, set_context


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class AuditContextMiddleware:
    """Makes the acting user / IP available to the audit signal handlers."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # request.user is lazy; resolve it at use-time via a small proxy
        token = set_context(user=_LazyUser(request), ip=_client_ip(request),
                            user_agent=request.META.get("HTTP_USER_AGENT", ""))
        try:
            return self.get_response(request)
        finally:
            reset_context(token)


class _LazyUser:
    def __init__(self, request):
        self._request = request

    def resolve(self):
        user = getattr(self._request, "user", None)
        return user if user is not None and user.is_authenticated else None
