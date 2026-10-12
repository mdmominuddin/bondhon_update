from django.core.management.base import BaseCommand

from messaging.services import dispatch_queued


class Command(BaseCommand):
    help = "Send queued SMS messages (and optionally retry failed ones). Schedule this every few minutes."

    def add_arguments(self, parser):
        parser.add_argument("--retry-failed", action="store_true")
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **opts):
        sent, failed = dispatch_queued(limit=opts["limit"], include_failed=opts["retry_failed"])
        self.stdout.write(f"SMS processed: {sent} sent, {failed} failed.")
