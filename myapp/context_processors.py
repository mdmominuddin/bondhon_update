from .models import Notification, SiteSetting
from .permissions import (
    can_manage_finance, is_admin, is_auditor, is_office, primary_role, user_roles, ROLE_MEMBER,
)


def bondhon(request):
    ctx = {"site": SiteSetting.get()}
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        member = getattr(user, "member_profile", None)
        ctx.update({
            "role": primary_role(user),
            "roles": sorted(user_roles(user)),
            "is_admin": is_admin(user),
            "is_office": is_office(user),
            "can_finance": can_manage_finance(user),
            "can_audit": is_auditor(user),
            "is_member": ROLE_MEMBER in user_roles(user),
            "my_member": member,
            "unread_count": Notification.objects.filter(member=member, is_read=False).count() if member else 0,
        })
    return ctx
