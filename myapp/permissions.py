"""Role model: Admin, Treasurer, Auditor, Member (Django groups)."""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

ROLE_ADMIN, ROLE_TREASURER, ROLE_AUDITOR, ROLE_MEMBER = "Admin", "Treasurer", "Auditor", "Member"
ALL_ROLES = [ROLE_ADMIN, ROLE_TREASURER, ROLE_AUDITOR, ROLE_MEMBER]

ROLE_DESCRIPTIONS = {
    ROLE_ADMIN: "Full control: users, settings, approvals, messaging, payments, audit re-open.",
    ROLE_TREASURER: "Records deposits and expenses (expenses need admin approval), verifies member payments, sends messages.",
    ROLE_AUDITOR: "Read-only access to all records plus the audit module (engagements, findings, reconciliation).",
    ROLE_MEMBER: "Member portal: own statement, deposit money, receipts, notifications.",
}


def user_roles(user):
    """Set of role names held by the user."""
    if not user.is_authenticated:
        return set()
    cache = getattr(user, "_bondhon_roles", None)
    if cache is not None:
        return cache
    roles = set(user.groups.filter(name__in=ALL_ROLES).values_list("name", flat=True))
    if user.is_superuser:
        roles.add(ROLE_ADMIN)
    if hasattr(user, "member_profile") and user.member_profile is not None:
        roles.add(ROLE_MEMBER)
    user._bondhon_roles = roles
    return roles


def has_role(user, *roles):
    return bool(user_roles(user) & set(roles))


def is_office(user):
    """Anyone who may see organisation-wide data."""
    return has_role(user, ROLE_ADMIN, ROLE_TREASURER, ROLE_AUDITOR)


def can_manage_finance(user):
    return has_role(user, ROLE_ADMIN, ROLE_TREASURER)


def is_admin(user):
    return has_role(user, ROLE_ADMIN)


def is_auditor(user):
    return has_role(user, ROLE_AUDITOR, ROLE_ADMIN)


def primary_role(user):
    for r in (ROLE_ADMIN, ROLE_TREASURER, ROLE_AUDITOR, ROLE_MEMBER):
        if r in user_roles(user):
            return r
    return None


def role_required(*roles):
    """View decorator: login + at least one of the roles."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if not has_role(request.user, *roles):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


office_required = role_required(ROLE_ADMIN, ROLE_TREASURER, ROLE_AUDITOR)
finance_required = role_required(ROLE_ADMIN, ROLE_TREASURER)
admin_required = role_required(ROLE_ADMIN)
auditor_required = role_required(ROLE_ADMIN, ROLE_AUDITOR)
member_required = role_required(ROLE_MEMBER)
