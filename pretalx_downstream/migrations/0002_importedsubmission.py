import django.db.models.deletion
from django.db import migrations, models


def backfill_imported_submissions(apps, schema_editor):
    ImportedSubmission = apps.get_model("pretalx_downstream", "ImportedSubmission")
    Submission = apps.get_model("submission", "Submission")
    TalkSlot = apps.get_model("schedule", "TalkSlot")
    UpstreamResult = apps.get_model("pretalx_downstream", "UpstreamResult")

    schedule_ids = (
        UpstreamResult.objects.exclude(schedule=None)
        .values_list("schedule_id", flat=True)
        .distinct()
    )
    submission_ids = (
        TalkSlot.objects.filter(schedule_id__in=schedule_ids, submission__isnull=False)
        .values_list("submission_id", flat=True)
        .distinct()
    )
    for submission in Submission.objects.filter(id__in=submission_ids).iterator():
        if not submission.code:
            continue
        ImportedSubmission.objects.get_or_create(
            event_id=submission.event_id,
            upstream_id=submission.code,
            defaults={"submission_id": submission.id},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("submission", "0001_initial"),
        ("event", "0017_auto_20180922_0511"),
        ("schedule", "0011_auto_20180205_1127"),
        ("pretalx_downstream", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ImportedSubmission",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("upstream_id", models.CharField(max_length=255)),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="imported_submissions",
                        to="event.event",
                    ),
                ),
                (
                    "submission",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="downstream_import",
                        to="submission.submission",
                    ),
                ),
            ],
            options={"unique_together": {("event", "upstream_id")}},
        ),
        migrations.RunPython(backfill_imported_submissions, migrations.RunPython.noop),
    ]
