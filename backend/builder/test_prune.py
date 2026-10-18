"""prune_builds: retention for saved builds."""
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from builder.management.commands import prune_builds
from builder.models import Build
from catalog.models import Arc, Pack


@pytest.fixture
def pack(db):
    return Pack.objects.create(id="t", name="Test", version="0", config={})


def build(pack, title, days_ago, **kw):
    b = Build.objects.create(pack=pack, title=title, state={"title": title}, **kw)
    # `updated` is auto_now, so set it with update() to backdate it.
    Build.objects.filter(id=b.id).update(updated=timezone.now() - timedelta(days=days_ago))
    return b


def run(*args):
    out = StringIO()
    call_command("prune_builds", *args, stdout=out)
    return out.getvalue()


def test_deletes_only_builds_older_than_the_cutoff(pack):
    old = build(pack, "Old course", 600)
    recent = build(pack, "Recent course", 30)
    edge = build(pack, "Just inside", 539)
    out = run()
    assert "Deleting 1 build not updated in 540 days." in out and "Old course" in out
    assert set(Build.objects.values_list("id", flat=True)) == {recent.id, edge.id}
    assert not Build.objects.filter(id=old.id).exists()


def test_dry_run_lists_and_keeps(pack):
    old = build(pack, "Old course", 600)
    out = run("--dry-run")
    assert "Would delete 1 build" in out and str(old.id) in out and "Old course" in out
    assert Build.objects.filter(id=old.id).exists()


def test_older_than_days_option(pack):
    build(pack, "A", 400)
    build(pack, "B", 100)
    run("--older-than-days", "365")
    assert list(Build.objects.values_list("title", flat=True)) == ["B"]
    with pytest.raises(CommandError):
        run("--older-than-days", "0")


def test_forks_survive_their_source(pack):
    source = build(pack, "Source", 700)
    fork = build(pack, "Fork", 10, forked_from=source)
    run()
    fork.refresh_from_db()
    assert fork.forked_from_id is None


def test_builds_linked_to_a_program_are_kept(pack, monkeypatch):
    # Without a `program` field (before that model lands) the guard is a no-op...
    assert prune_builds.protecting_fields() == [f for f in ("program",) if hasattr(Build, "program")]
    # ...and with one, linked builds stay. Stand in with an existing nullable FK.
    monkeypatch.setattr(prune_builds, "PROTECTING_FIELDS", ("program", "arc"))
    arc = Arc.objects.create(id="arc", pack=pack, title="Arc", focus="fyc")
    linked = build(pack, "Linked", 900, arc=arc)
    loose = build(pack, "Loose", 900)
    out = run()
    assert "Linked" not in out
    assert Build.objects.filter(id=linked.id).exists() and not Build.objects.filter(id=loose.id).exists()
