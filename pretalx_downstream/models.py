import hashlib

from django.db import models
from django_scopes import ScopedManager


class UpstreamResult(models.Model):
    event = models.ForeignKey(
        to="event.Event", on_delete=models.CASCADE, related_name="upstream_results"
    )
    schedule = models.ForeignKey(
        to="schedule.Schedule",
        null=True,
        on_delete=models.CASCADE,
        related_name="upstream_results",
    )
    content = models.TextField(null=True, blank=True)
    changes = models.TextField(
        null=True, blank=True
    )  # contains only content changes, all regular changes will be showin in the related schedule update (if any)
    timestamp = models.DateTimeField(auto_now_add=True)

    objects = ScopedManager(event="event")

    def __str__(self):
        return f"UpstreamResult for {self.event} at {self.timestamp}"

    @property
    def checksum(self):
        if not self.content:
            return None
        m = hashlib.sha256()
        m.update(self.content.encode("utf-8"))
        return m.hexdigest()


class ImportedSubmission(models.Model):
    """Maps an upstream feed's talk id to the submission the importer created for
    it. Upstream feed content is not trusted (both in general + MITM), so ID
    collisions could override data."""

    event = models.ForeignKey(
        to="event.Event", on_delete=models.CASCADE, related_name="imported_submissions"
    )
    upstream_id = models.CharField(max_length=255)
    submission = models.OneToOneField(
        to="submission.Submission",
        on_delete=models.CASCADE,
        related_name="downstream_import",
    )

    objects = ScopedManager(event="event")

    class Meta:
        unique_together = (("event", "upstream_id"),)

    def __str__(self):
        return f"ImportedSubmission({self.upstream_id} -> {self.submission})"
