"""Per-request context so signal handlers know who/where a change came from."""
from contextvars import ContextVar

_current = ContextVar("bondhon_audit_ctx", default=None)


def set_context(user=None, ip=None, user_agent=""):
    return _current.set({"user": user, "ip": ip, "ua": (user_agent or "")[:255]})


def reset_context(token):
    _current.reset(token)


def get_context():
    return _current.get() or {"user": None, "ip": None, "ua": ""}
