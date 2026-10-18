"""A build is one instructor's course: term settings plus the readings and assignments they kept.

No accounts in the MVP. Creating a build returns a secret edit token; anyone with the build's
id can view or fork it, and only holders of the token can change it.
"""
import secrets
import uuid

from django.db import models


def new_token() -> str:
    return secrets.token_urlsafe(24)


class Build(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    edit_token = models.CharField(max_length=64, default=new_token, editable=False)
    pack = models.ForeignKey("catalog.Pack", on_delete=models.PROTECT)
    title = models.CharField(max_length=300, default="Untitled course")
    focus = models.CharField(max_length=20, blank=True)
    arc = models.ForeignKey("catalog.Arc", null=True, blank=True, on_delete=models.SET_NULL)
    forked_from = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL)
    # The whole editable state as the frontend holds it: term, units, selections, outcomes.
    state = models.JSONField(default=dict)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title
