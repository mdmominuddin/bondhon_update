"""SMS transport.  Backends return (ok: bool, provider_id: str, raw_response: str)."""
import json
import logging
import urllib.parse
import urllib.request

from django.conf import settings

log = logging.getLogger("bondhon.sms")


class SMSBackendError(Exception):
    pass


class ConsoleBackend:
    """Test mode - writes to the log, sends nothing."""
    name = "console"

    def send(self, to, message, sender=""):
        log.info("[SMS console] to=%s sender=%s :: %s", to, sender, message)
        return True, "console", "Logged only (test mode - no SMS was sent)."


class HTTPBackend:
    """
    Generic HTTP provider.  Configure through environment variables:

      SMS_HTTP_URL, SMS_HTTP_METHOD (GET/POST), SMS_HTTP_PARAMS (JSON, with
      {to} {message} {sender} {api_key} {username} {password} placeholders),
      SMS_HTTP_SUCCESS_MARKER (text that must appear in a successful reply).
    """
    name = "http"

    def send(self, to, message, sender=""):
        url, raw = settings.SMS_HTTP_URL, settings.SMS_HTTP_PARAMS
        if not url or not raw:
            raise SMSBackendError("HTTP SMS backend selected but SMS_HTTP_URL / SMS_HTTP_PARAMS are not configured.")
        try:
            template = json.loads(raw)
        except ValueError as exc:
            raise SMSBackendError(f"SMS_HTTP_PARAMS is not valid JSON: {exc}")
        values = {
            "to": to, "message": message, "sender": sender, "api_key": settings.SMS_HTTP_API_KEY,
            "username": settings.SMS_HTTP_USERNAME, "password": settings.SMS_HTTP_PASSWORD,
        }
        params = {k: (v.format(**values) if isinstance(v, str) else v) for k, v in template.items()}
        data = urllib.parse.urlencode(params).encode("utf-8")
        if settings.SMS_HTTP_METHOD == "GET":
            req = urllib.request.Request(f"{url}?{data.decode()}")
        else:
            req = urllib.request.Request(url, data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=settings.SMS_HTTP_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", "replace")
                status = resp.status
        except Exception as exc:  # network / HTTP errors
            raise SMSBackendError(f"Provider request failed: {exc}")
        marker = settings.SMS_HTTP_SUCCESS_MARKER
        ok = 200 <= status < 300 and (marker in body if marker else True)
        return ok, "", body[:2000]


def get_backend(name):
    return HTTPBackend() if name == "http" else ConsoleBackend()
