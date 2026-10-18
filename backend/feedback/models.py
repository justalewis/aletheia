from django.db import models


class Feedback(models.Model):
    """A message from the feedback form. Kept even when the email can't be sent."""

    created = models.DateTimeField(auto_now_add=True)
    name = models.CharField(max_length=120)
    email = models.EmailField()
    message = models.TextField()
    page = models.CharField(max_length=300, blank=True)   # where in the site they were
    emailed = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created"]
        verbose_name_plural = "feedback"

    def __str__(self):
        return f"{self.name} ({self.created:%Y-%m-%d})"
