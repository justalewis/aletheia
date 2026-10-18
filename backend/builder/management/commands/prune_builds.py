"""Delete saved builds nobody has touched in a long while.

    python manage.py prune_builds --dry-run                # list what would go
    python manage.py prune_builds                          # not updated in 540 days (about 18 months)
    python manage.py prune_builds --older-than-days 365

Builds linked to a program are always kept. Forks of a deleted build keep working; they just
lose the "forked from" link.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import ProtectedError, RestrictedError
from django.utils import timezone

from builder.models import Build

# Foreign keys that mark a build as part of something larger. A field that doesn't exist on
# Build yet is skipped, so this works before and after the program model lands.
PROTECTING_FIELDS = ("program",)


def protecting_fields() -> list[str]:
    names = {f.name for f in Build._meta.get_fields()}
    return [f for f in PROTECTING_FIELDS if f in names]


def prunable(days: int):
    qs = Build.objects.filter(updated__lt=timezone.now() - timedelta(days=days))
    for field in protecting_fields():
        qs = qs.filter(**{f"{field}__isnull": True})
    return qs.order_by("updated")


class Command(BaseCommand):
    help = "Delete saved builds not updated in --older-than-days days (default 540). Builds in a program are kept."

    def add_arguments(self, parser):
        parser.add_argument("--older-than-days", type=int, default=540)
        parser.add_argument("--dry-run", action="store_true", help="list the builds without deleting them")

    def handle(self, *args, older_than_days, dry_run, **opts):
        if older_than_days < 1:
            raise CommandError("--older-than-days must be at least 1")
        qs = prunable(older_than_days)
        builds = list(qs.values_list("id", "title", "updated"))
        verb = "Would delete" if dry_run else "Deleting"
        self.stdout.write(f"{verb} {len(builds)} build{'s' if len(builds) != 1 else ''} "
                          f"not updated in {older_than_days} days.")
        for bid, title, updated in builds:
            self.stdout.write(f"  {bid}  {updated:%Y-%m-%d}  {title}")
        if dry_run or not builds:
            return
        deleted = 0
        for bid, title, _ in builds:
            try:
                Build.objects.filter(id=bid).delete()
                deleted += 1
            except (ProtectedError, RestrictedError):
                self.stdout.write(self.style.WARNING(f"  kept {bid} ({title}): something else still points to it"))
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted}."))
