import pytest
from django.core import mail
from django.test import Client

from feedback.models import Feedback

GOOD = {"name": "Pat Reader", "email": "pat@example.edu", "message": "The reader export worked well for my class.",
        "page": "/build/abc"}


def send(**over):
    return Client().post("/api/feedback", {**GOOD, **over}, content_type="application/json")


def test_feedback_is_saved_and_emailed_with_reply_to(db, settings):
    settings.EMAIL_HOST = "smtp.example.org"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.FEEDBACK_TO = "owner@example.org"
    assert send().json() == {"ok": True}
    item = Feedback.objects.get()
    assert (item.name, item.email, item.page, item.emailed) == ("Pat Reader", "pat@example.edu", "/build/abc", True)
    (m,) = mail.outbox
    assert m.to == ["owner@example.org"] and m.reply_to == ["pat@example.edu"]
    assert m.subject == "Aletheia feedback from Pat Reader"
    assert "The reader export worked well" in m.body and "Page: /build/abc" in m.body


def test_without_mail_settings_the_message_is_still_kept(db, settings):
    settings.EMAIL_HOST = ""
    assert send().status_code == 200
    assert Feedback.objects.get().emailed is False and mail.outbox == []


def test_a_failing_mail_server_does_not_lose_the_message(db, settings):
    settings.EMAIL_HOST = "smtp.example.org"
    # Port 1 on localhost refuses at once, so this exercises the failure path without a network wait.
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    settings.EMAIL_HOST, settings.EMAIL_PORT = "127.0.0.1", 1
    assert send().status_code == 200
    assert Feedback.objects.get().emailed is False


@pytest.mark.parametrize("over,fragment", [
    ({"name": "  "}, "name"),
    ({"email": "not-an-email"}, "email"),
    ({"message": "too short"}, "more"),
    ({"message": "x" * 5001}, "under"),
])
def test_bad_input_is_refused(db, over, fragment):
    r = send(**over)
    assert r.status_code == 400 and fragment in r.json()["detail"]
    assert Feedback.objects.count() == 0


def test_the_honeypot_drops_bots_quietly(db, settings):
    settings.EMAIL_HOST = "smtp.example.org"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    assert send(website="http://spam.example").json() == {"ok": True}
    assert Feedback.objects.count() == 0 and mail.outbox == []


def test_newlines_in_the_name_cannot_inject_headers(db, settings):
    settings.EMAIL_HOST = "smtp.example.org"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    send(name="Pat\r\nBcc: spam@example.org")
    assert "\n" not in mail.outbox[0].subject and mail.outbox[0].bcc == []
