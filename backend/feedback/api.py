"""POST /api/feedback: save a message from the feedback form and email it to the site's maintainer."""
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from ninja import Router, Schema
from ninja.errors import HttpError

from feedback.models import Feedback

log = logging.getLogger(__name__)
router = Router(tags=["feedback"])

MIN_MESSAGE, MAX_MESSAGE = 10, 5000


class FeedbackIn(Schema):
    name: str
    email: str
    message: str
    page: str = ""
    website: str = ""   # honeypot: a field people never see, so only bots fill it in


def _one_line(value: str, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


def send(item: Feedback) -> bool:
    """Email the message with the sender's address as Reply-To. False when mail isn't set up or fails."""
    if not settings.EMAIL_HOST:
        log.warning("feedback %s saved but not emailed: ALETHEIA_EMAIL_HOST is not set", item.pk)
        return False
    body = (f"From: {item.name} <{item.email}>\nPage: {item.page or '(not given)'}\n"
            f"Sent: {item.created:%Y-%m-%d %H:%M} UTC\n\n{item.message}\n")
    try:
        EmailMessage(subject=f"Aletheia feedback from {item.name}", body=body, to=[settings.FEEDBACK_TO],
                     reply_to=[item.email]).send()
    except Exception:  # SMTP and network errors vary; the message is already saved
        log.exception("feedback %s saved but the email failed", item.pk)
        return False
    return True


@router.post("/feedback")
def submit_feedback(request, body: FeedbackIn):
    if body.website.strip():
        return {"ok": True}   # a bot: say thanks and drop it
    name, email, message = _one_line(body.name, 120), body.email.strip(), body.message.strip()
    if not name:
        raise HttpError(400, "Please tell us your name.")
    try:
        validate_email(email)
    except ValidationError:
        raise HttpError(400, "That email address doesn't look right.")
    if len(message) < MIN_MESSAGE:
        raise HttpError(400, "Please write a little more.")
    if len(message) > MAX_MESSAGE:
        raise HttpError(400, f"Please keep feedback under {MAX_MESSAGE:,} characters.")
    item = Feedback.objects.create(name=name, email=email, message=message, page=_one_line(body.page, 300))
    item.emailed = send(item)
    item.save(update_fields=["emailed"])
    return {"ok": True}
