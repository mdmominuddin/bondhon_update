from django.core.management.base import BaseCommand
from django.utils import timezone

from messaging import services
from myapp.services import member_positions
from myapp.models import SocietyMember


class Command(BaseCommand):
    help = "Send the 'due_reminder' message to every active member with an outstanding due (once per day per member)."

    def handle(self, *args, **opts):
        today = timezone.localdate()
        sent = 0
        for pos in member_positions(SocietyMember.objects.filter(is_active=True)):
            if pos["due"] > 0:
                sms = services.send_event("due_reminder", pos["member"], related=("reminder", f"{pos['member'].pk}-{today:%Y%m%d}"), in_app=True)
                sent += bool(sms and sms.status == "sent")
        self.stdout.write(f"Reminders sent: {sent}")
