"""A program is a writing program or course team: shared starting courses plus a zero-textbook-cost report.

Like builds, programs have no accounts. Creating one returns a secret admin token; the program's
public page (its published courses) needs only the id, and every change or report needs the token.
Instructors' builds link to a program through Build.program and carry their section info in
Build.section.
"""
import uuid

from django.db import models

from builder.models import new_token


def default_settings() -> dict:
    return {"textbook_cost_per_student": 100.00, "currency": "USD", "terms": []}


class Program(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    admin_token = models.CharField(max_length=64, default=new_token, editable=False)
    name = models.CharField(max_length=300)
    institution = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    # textbook_cost_per_student (what a section would otherwise spend per student), currency, terms.
    settings = models.JSONField(default=default_settings)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def setting(self, key: str):
        return {**default_settings(), **(self.settings or {})}.get(key)


class ProgramArc(models.Model):
    """A course the program shares for instructors to start from. `state` has the same shape as Build.state."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name="arcs")
    title = models.CharField(max_length=300)
    summary = models.TextField(blank=True)
    focus = models.CharField(max_length=20, blank=True)
    state = models.JSONField(default=dict)
    source_build = models.ForeignKey("builder.Build", null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    published = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "created"]

    def __str__(self):
        return self.title
