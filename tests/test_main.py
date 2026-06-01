import datetime as dt
import importlib
from io import StringIO
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree as ET

import pytest
from django.apps import apps
from django.core.management import call_command
from django.db.utils import IntegrityError
from django.urls import reverse
from django.utils.timezone import now
from django_scopes import scope, scopes_disabled

from pretalx.person.models import SpeakerProfile, User
from pretalx.schedule.models import Room, Schedule, TalkSlot
from pretalx.submission.models import (
    Submission,
    SubmissionStates,
    SubmissionType,
    Track,
)

from pretalx_downstream.models import ImportedSubmission, UpstreamResult
from pretalx_downstream.signals import refresh_upstream_schedule
from pretalx_downstream.tasks import (
    _create_user,
    process_frab,
    task_refresh_upstream_schedule,
)

backfill_imported_submissions = importlib.import_module(
    "pretalx_downstream.migrations.0002_importedsubmission"
).backfill_imported_submissions

SETTINGS_URL_NAME = "plugins:pretalx_downstream:settings"

EMPTY_SCHEDULE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<schedule>
  <version>2.0</version>
  <conference>
    <title>Test Conference</title>
    <start>2024-01-15</start>
    <end>2024-01-17</end>
  </conference>
</schedule>
"""

SAMPLE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<schedule>
  <version>1.0</version>
  <conference>
    <title>Test Conference</title>
    <start>2024-01-15</start>
    <end>2024-01-17</end>
  </conference>
  <day index="1" date="2024-01-15">
    <room name="Main Hall">
      <event id="AAAAAA" guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee">
        <date>2024-01-15</date>
        <start>10:00</start>
        <duration>00:30</duration>
        <title>Opening Talk</title>
        <subtitle></subtitle>
        <track>General</track>
        <type>Talk</type>
        <language>en</language>
        <abstract>An opening talk.</abstract>
        <description>Full description here.</description>
        <recording>
          <optout>false</optout>
        </recording>
        <persons>
          <person id="1">Alice Speaker</person>
        </persons>
      </event>
    </room>
  </day>
</schedule>
"""


@pytest.mark.django_db
def test_orga_can_access_settings(orga_client, event):
    response = orga_client.get(
        reverse(SETTINGS_URL_NAME, kwargs={"event": event.slug}), follow=True
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_reviewer_cannot_access_settings(review_client, event):
    response = review_client.get(
        reverse(SETTINGS_URL_NAME, kwargs={"event": event.slug})
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_orga_can_save_settings(orga_client, event):
    url = reverse(SETTINGS_URL_NAME, kwargs={"event": event.slug})
    response = orga_client.post(
        url,
        {
            "downstream_upstream_url": "https://example.com/schedule.xml",
            "downstream_interval": "10",
            "downstream_checking_time": "always",
            "downstream_discard_after": "",
            "action": "save",
        },
        follow=True,
    )
    assert response.status_code == 200
    event.settings.flush()
    assert event.settings.downstream_upstream_url == "https://example.com/schedule.xml"
    assert event.settings.downstream_interval == "10"


@pytest.mark.django_db
def test_orga_can_trigger_refresh(orga_client, event):
    event.settings.downstream_upstream_url = "https://example.com/schedule.xml"
    event.settings.downstream_interval = 10
    event.settings.downstream_checking_time = "always"
    url = reverse(SETTINGS_URL_NAME, kwargs={"event": event.slug})
    with patch("pretalx_downstream.views.task_refresh_upstream_schedule") as mock_task:
        mock_task.apply_async = MagicMock()
        response = orga_client.post(
            url,
            {
                "downstream_upstream_url": "https://example.com/schedule.xml",
                "downstream_interval": "10",
                "downstream_checking_time": "always",
                "downstream_discard_after": "",
                "action": "refresh",
            },
            follow=True,
        )
    assert response.status_code == 200
    mock_task.apply_async.assert_called_once()


@pytest.mark.django_db
def test_settings_shows_last_pulled(orga_client, event):
    event.settings.upstream_last_sync = "2024-01-15T10:00:00.000000+00:00"
    url = reverse(SETTINGS_URL_NAME, kwargs={"event": event.slug})
    response = orga_client.get(url)
    assert response.status_code == 200
    assert b"Last automatic sync" in response.content


@pytest.mark.django_db
def test_upstream_result_checksum(event):
    result = UpstreamResult.objects.create(event=event, content="test content")
    assert result.checksum is not None
    assert len(result.checksum) == 64


@pytest.mark.django_db
def test_upstream_result_checksum_none_content(event):
    result = UpstreamResult.objects.create(event=event, content=None)
    assert result.checksum is None


@pytest.mark.django_db
def test_upstream_result_checksum_deterministic(event):
    r1 = UpstreamResult.objects.create(event=event, content="same")
    r2 = UpstreamResult.objects.create(event=event, content="same")
    assert r1.checksum == r2.checksum


@pytest.mark.django_db
def test_process_frab_creates_submissions(event):
    root = ET.fromstring(SAMPLE_XML)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)
    with scopes_disabled():
        sub = Submission.objects.get(event=event, code="AAAAAA")
        assert sub.title == "Opening Talk"
        assert sub.speakers.count() == 1


@pytest.mark.django_db
def test_process_frab_creates_room(event):
    root = ET.fromstring(SAMPLE_XML)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)
    with scopes_disabled():
        assert Room.objects.filter(event=event, name="Main Hall").exists()


@pytest.mark.django_db
def test_process_frab_creates_track(event):
    root = ET.fromstring(SAMPLE_XML)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)
    with scopes_disabled():
        assert Track.objects.filter(event=event, name="General").exists()


@pytest.mark.django_db
def test_process_frab_release_new_version(event):
    root = ET.fromstring(SAMPLE_XML)
    with scope(event=event):
        _, schedule = process_frab(root, event, release_new_version=True)
    assert schedule is not None
    assert schedule.version == "1.0"


@pytest.mark.django_db
def test_process_frab_detects_changes_on_reimport(event):
    root = ET.fromstring(SAMPLE_XML)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)
        modified_xml = SAMPLE_XML.replace("Opening Talk", "Updated Talk")
        root2 = ET.fromstring(modified_xml)
        changes, _ = process_frab(root2, event, release_new_version=False)
    assert "AAAAAA" in changes
    assert "title" in changes["AAAAAA"]


@pytest.mark.django_db
def test_create_user(event):
    with scope(event=event):
        profile = _create_user("Test Person", event)
    assert profile.user.email == "test person@localhost"
    assert profile.event == event


@pytest.mark.django_db
def test_create_user_idempotent(event):
    with scope(event=event):
        profile1 = _create_user("Test Person", event)
        profile2 = _create_user("Test Person", event)
    assert profile1.pk == profile2.pk


@pytest.mark.django_db
def test_task_no_url_raises(event):
    with pytest.raises(RuntimeError, match="no upstream URL"):
        task_refresh_upstream_schedule(event.slug)


@pytest.mark.django_db
def test_task_bad_response_raises(event):
    event.settings.downstream_upstream_url = "https://example.com/schedule.xml"
    mock_response = MagicMock()
    mock_response.status = 404
    with (
        patch("pretalx_downstream.tasks.urllib3.request", return_value=mock_response),
        pytest.raises(RuntimeError, match="Could not retrieve"),
    ):
        task_refresh_upstream_schedule(event.slug)


@pytest.mark.django_db
def test_task_skips_unchanged(event):
    event.settings.downstream_upstream_url = "https://example.com/schedule.xml"
    content = SAMPLE_XML.encode()
    UpstreamResult.objects.create(event=event, content=SAMPLE_XML)

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.data = content

    with patch("pretalx_downstream.tasks.urllib3.request", return_value=mock_response):
        task_refresh_upstream_schedule(event.slug)

    with scope(event=event):
        assert event.upstream_results.count() == 1


@pytest.mark.django_db
def test_task_processes_new_schedule(event):
    event.settings.downstream_upstream_url = "https://example.com/schedule.xml"
    content = SAMPLE_XML.encode()

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.data = content

    with patch("pretalx_downstream.tasks.urllib3.request", return_value=mock_response):
        task_refresh_upstream_schedule(event.slug)

    with scope(event=event):
        assert event.upstream_results.count() == 1
        result = event.upstream_results.first()
        assert result.schedule is not None
        assert result.schedule.version == "1.0"


@pytest.mark.django_db
@pytest.mark.parametrize("sync", (True, False))
def test_management_command(event, sync):
    event.settings.downstream_upstream_url = "https://example.com/schedule.xml"
    content = SAMPLE_XML.encode()

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.data = content

    with (
        patch("pretalx_downstream.tasks.urllib3.request", return_value=mock_response),
        patch(
            "pretalx_downstream.tasks.task_refresh_upstream_schedule.apply_async"
        ) as mock_async,
    ):
        call_command("downstream_pull", event=event.slug, sync=sync, stdout=StringIO())

    if sync:
        with scope(event=event):
            assert event.upstream_results.count() == 1
        mock_async.assert_not_called()
    else:
        mock_async.assert_called_once_with(args=(event.slug,), ignore_result=True)


@pytest.mark.django_db
def test_room_with_guid(event):
    xml_with_guid = SAMPLE_XML.replace(
        '<room name="Main Hall">',
        '<room name="Main Hall" guid="12345678-abcd-1234-abcd-123456789012">',
    )
    root = ET.fromstring(xml_with_guid)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)
    with scopes_disabled():
        room = Room.objects.get(event=event, name="Main Hall")
        assert str(room.guid) == "12345678-abcd-1234-abcd-123456789012"


@pytest.mark.django_db
def test_talk_with_recording_optout(event):
    xml_optout = SAMPLE_XML.replace("<optout>false</optout>", "<optout>true</optout>")
    root = ET.fromstring(xml_optout)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)
    with scopes_disabled():
        sub = Submission.objects.get(event=event, code="AAAAAA")
        assert sub.do_not_record is True


@pytest.mark.django_db
def test_talk_with_subtitle(event):
    xml_subtitle = SAMPLE_XML.replace(
        "<subtitle></subtitle>", "<subtitle>A great subtitle</subtitle>"
    )
    root = ET.fromstring(xml_subtitle)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)
    with scopes_disabled():
        sub = Submission.objects.get(event=event, code="AAAAAA")
        assert "A great subtitle" in sub.description


@pytest.mark.django_db
def test_periodic_signal_triggers_task(event):
    event.settings.downstream_upstream_url = "https://example.com/schedule.xml"
    event.settings.downstream_checking_time = "always"
    event.settings.downstream_interval = 5
    with patch(
        "pretalx_downstream.signals.task_refresh_upstream_schedule"
    ) as mock_task:
        mock_task.apply_async = MagicMock()
        refresh_upstream_schedule(sender=None)
    mock_task.apply_async.assert_called_once_with(
        kwargs={"event_slug": event.slug}, ignore_result=True
    )


@pytest.mark.django_db
def test_periodic_signal_skips_event_without_url(event):
    with patch(
        "pretalx_downstream.signals.task_refresh_upstream_schedule"
    ) as mock_task:
        mock_task.apply_async = MagicMock()
        refresh_upstream_schedule(sender=None)
    mock_task.apply_async.assert_not_called()


@pytest.mark.django_db
def test_periodic_signal_cleans_old_results(event):
    event.settings.downstream_upstream_url = "https://example.com/schedule.xml"
    event.settings.downstream_checking_time = "always"
    for i in range(5):
        UpstreamResult.objects.create(event=event, content=f"content {i}")
    with patch(
        "pretalx_downstream.signals.task_refresh_upstream_schedule"
    ) as mock_task:
        mock_task.apply_async = MagicMock()
        refresh_upstream_schedule(sender=None)
    with scope(event=event):
        assert event.upstream_results.count() == 3


@pytest.mark.django_db
def test_discard_after_setting(event):
    event.settings.downstream_discard_after = "-"
    xml_versioned = SAMPLE_XML.replace(
        "<version>1.0</version>", "<version>1.0-beta1</version>"
    )
    root = ET.fromstring(xml_versioned)
    with scope(event=event):
        _, schedule = process_frab(root, event, release_new_version=True)
    assert schedule.version == "1.0"


@pytest.mark.django_db
def test_empty_schedule_does_not_crash(event):
    original_date_from = event.date_from
    original_date_to = event.date_to
    root = ET.fromstring(EMPTY_SCHEDULE_XML)
    with scope(event=event):
        _, schedule = process_frab(root, event, release_new_version=True)
    assert schedule is not None
    assert schedule.version == "2.0"
    event.refresh_from_db()
    assert event.date_from == original_date_from
    assert event.date_to == original_date_to


@pytest.mark.django_db
def test_empty_then_full_schedule_recovers(event):
    empty_root = ET.fromstring(EMPTY_SCHEDULE_XML)
    with scope(event=event):
        process_frab(empty_root, event, release_new_version=True)
        _, schedule = process_frab(
            ET.fromstring(SAMPLE_XML), event, release_new_version=True
        )
    assert schedule is not None
    assert schedule.version == "1.0"
    with scopes_disabled():
        assert Submission.objects.filter(event=event, code="AAAAAA").exists()


@pytest.mark.django_db
def test_orga_trigger_refresh_handles_error(orga_client, event):
    url = reverse(SETTINGS_URL_NAME, kwargs={"event": event.slug})
    with patch("pretalx_downstream.views.task_refresh_upstream_schedule") as mock_task:
        mock_task.apply_async.side_effect = RuntimeError("boom")
        response = orga_client.post(
            url,
            {
                "downstream_upstream_url": "https://example.com/schedule.xml",
                "downstream_interval": "10",
                "downstream_checking_time": "always",
                "downstream_discard_after": "",
                "action": "refresh",
            },
            follow=True,
        )
    assert response.status_code == 200
    assert b"Failure when processing remote schedule" in response.content


@pytest.mark.django_db
def test_periodic_signal_skips_finished_event(event):
    with scopes_disabled():
        event.date_from = dt.date.today() - dt.timedelta(days=10)
        event.date_to = dt.date.today() - dt.timedelta(days=8)
        event.save()
    event.settings.downstream_upstream_url = "https://example.com/schedule.xml"
    event.settings.downstream_checking_time = "always"
    with patch(
        "pretalx_downstream.signals.task_refresh_upstream_schedule"
    ) as mock_task:
        mock_task.apply_async = MagicMock()
        refresh_upstream_schedule(sender=None)
    mock_task.apply_async.assert_not_called()


@pytest.mark.django_db
def test_periodic_signal_skips_not_started_event(event):
    with scopes_disabled():
        event.date_from = dt.date.today() + dt.timedelta(days=10)
        event.date_to = dt.date.today() + dt.timedelta(days=12)
        event.save()
    event.settings.downstream_upstream_url = "https://example.com/schedule.xml"
    event.settings.downstream_checking_time = "event"
    with patch(
        "pretalx_downstream.signals.task_refresh_upstream_schedule"
    ) as mock_task:
        mock_task.apply_async = MagicMock()
        refresh_upstream_schedule(sender=None)
    mock_task.apply_async.assert_not_called()


@pytest.mark.django_db
def test_periodic_signal_interval_typeerror_falls_back(event):
    event.settings.downstream_upstream_url = "https://example.com/schedule.xml"
    event.settings.downstream_checking_time = "always"
    with (
        patch("pretalx_downstream.signals.task_refresh_upstream_schedule") as mock_task,
        patch("pretalx_downstream.signals.int", side_effect=TypeError),
    ):
        mock_task.apply_async = MagicMock()
        refresh_upstream_schedule(sender=None)
    mock_task.apply_async.assert_called_once()


@pytest.mark.django_db
def test_periodic_signal_skips_when_recently_pulled(event):
    event.settings.downstream_upstream_url = "https://example.com/schedule.xml"
    event.settings.downstream_checking_time = "always"
    event.settings.downstream_interval = 15
    event.settings.upstream_last_sync = now().strftime("%Y-%m-%dT%H:%M:%S.%f%z")
    with patch(
        "pretalx_downstream.signals.task_refresh_upstream_schedule"
    ) as mock_task:
        mock_task.apply_async = MagicMock()
        refresh_upstream_schedule(sender=None)
    mock_task.apply_async.assert_not_called()


@pytest.mark.django_db
def test_task_processes_changed_schedule(event):
    event.settings.downstream_upstream_url = "https://example.com/schedule.xml"
    UpstreamResult.objects.create(event=event, content="totally different content")
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.data = SAMPLE_XML.encode()
    with patch("pretalx_downstream.tasks.urllib3.request", return_value=mock_response):
        task_refresh_upstream_schedule(event.slug)
    with scope(event=event):
        assert event.upstream_results.count() == 2


@pytest.mark.django_db
def test_task_discard_after(event):
    event.settings.downstream_upstream_url = "https://example.com/schedule.xml"
    event.settings.downstream_discard_after = "-"
    versioned = SAMPLE_XML.replace(
        "<version>1.0</version>", "<version>1.0-beta1</version>"
    )
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.data = versioned.encode()
    with patch("pretalx_downstream.tasks.urllib3.request", return_value=mock_response):
        task_refresh_upstream_schedule(event.slug)
    with scope(event=event):
        result = event.upstream_results.first()
        assert result.schedule.version == "1.0"


@pytest.mark.django_db
def test_process_frab_freeze_failure(event):
    root = ET.fromstring(SAMPLE_XML)
    with (
        scope(event=event),
        patch(
            "pretalx_downstream.tasks.freeze_schedule", side_effect=RuntimeError("nope")
        ),
        pytest.raises(RuntimeError, match="Could not import"),
    ):
        process_frab(root, event, release_new_version=True)


@pytest.mark.django_db
def test_process_frab_missing_abstract_and_description(event):
    xml = SAMPLE_XML.replace("<abstract>An opening talk.</abstract>", "").replace(
        "<description>Full description here.</description>", ""
    )
    root = ET.fromstring(xml)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)
    with scopes_disabled():
        sub = Submission.objects.get(event=event, code="AAAAAA")
        assert sub.abstract == ""
        assert sub.description == ""


@pytest.mark.django_db
def test_process_frab_creates_submission_type(event):
    xml = SAMPLE_XML.replace("<type>Talk</type>", "<type></type>")
    root = ET.fromstring(xml)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)
    with scopes_disabled():
        assert SubmissionType.objects.filter(event=event, name="default").exists()


@pytest.mark.django_db
def test_process_frab_without_track(event):
    xml = SAMPLE_XML.replace("<track>General</track>", "<track></track>")
    root = ET.fromstring(xml)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)
    with scopes_disabled():
        sub = Submission.objects.get(event=event, code="AAAAAA")
        assert sub.track is None


@pytest.mark.django_db
def test_process_frab_without_persons(event):
    xml = SAMPLE_XML.replace(
        """        <persons>
          <person id="1">Alice Speaker</person>
        </persons>
""",
        "",
    )
    root = ET.fromstring(xml)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)
    with scopes_disabled():
        sub = Submission.objects.get(event=event, code="AAAAAA")
        assert sub.speakers.count() == 0


@pytest.mark.django_db
def test_process_frab_skips_empty_person(event):
    xml = SAMPLE_XML.replace(
        '<person id="1">Alice Speaker</person>',
        '<person id="1"></person>\n          <person id="2">Bob Speaker</person>',
    )
    root = ET.fromstring(xml)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)
    with scopes_disabled():
        sub = Submission.objects.get(event=event, code="AAAAAA")
        assert sub.speakers.count() == 1
        assert sub.speakers.first().user.name == "Bob Speaker"


@pytest.mark.django_db
def test_import_does_not_overwrite_organic_proposal(event):
    with scope(event=event):
        original_type = SubmissionType.objects.create(
            event=event, name="Original Type", default_duration=45
        )
        speaker = User.objects.create_user(
            email="real.speaker@example.org", name="Real Speaker", password="x"
        )
        profile = SpeakerProfile.objects.create(user=speaker, event=event)
        victim = Submission.objects.create(
            event=event,
            code="AAAAAA",
            title="My Original Proposal",
            abstract="My carefully written abstract.",
            description="My original description.",
            content_locale="en",
            submission_type=original_type,
            state=SubmissionStates.SUBMITTED,
        )
        victim.speakers.add(profile)

    root = ET.fromstring(SAMPLE_XML)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)

    with scopes_disabled():
        victim.refresh_from_db()
        assert victim.title == "My Original Proposal"
        assert victim.abstract == "My carefully written abstract."
        assert victim.description == "My original description."
        assert victim.state == SubmissionStates.SUBMITTED
        assert victim.submission_type == original_type
        assert list(victim.speakers.values_list("user__name", flat=True)) == [
            "Real Speaker"
        ]
        assert not ImportedSubmission.objects.filter(submission=victim).exists()

        imported = ImportedSubmission.objects.get(
            event=event, upstream_id="AAAAAA"
        ).submission
        assert imported != victim
        assert imported.code != "AAAAAA"
        assert imported.title == "Opening Talk"


@pytest.mark.django_db
def test_reimport_updates_same_submission_via_mapping(event):
    root = ET.fromstring(SAMPLE_XML)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)
        modified = SAMPLE_XML.replace("Opening Talk", "Updated Talk")
        process_frab(ET.fromstring(modified), event, release_new_version=False)
    with scopes_disabled():
        assert Submission.objects.filter(event=event, code="AAAAAA").count() == 1
        assert ImportedSubmission.objects.filter(event=event).count() == 1
        sub = Submission.objects.get(event=event, code="AAAAAA")
        assert sub.title == "Updated Talk"


def _give_schedule_provenance(event, submission):
    schedule = Schedule.objects.create(event=event, version="frozen-1")
    room = Room.objects.create(event=event, name="Some Room")
    TalkSlot.objects.create(submission=submission, schedule=schedule, room=room)
    UpstreamResult.objects.create(event=event, schedule=schedule, content="prior feed")


@pytest.mark.django_db
def test_legacy_backfill_maps_scheduled_submission(event):
    with scope(event=event):
        original_type = SubmissionType.objects.create(
            event=event, name="Original Type", default_duration=30
        )
        legacy = Submission.objects.create(
            event=event,
            code="AAAAAA",
            title="Outdated upstream title",
            submission_type=original_type,
            state=SubmissionStates.CONFIRMED,
        )
        _give_schedule_provenance(event, legacy)

    with scopes_disabled():
        backfill_imported_submissions(apps, None)
        assert ImportedSubmission.objects.filter(
            event=event, upstream_id="AAAAAA", submission=legacy
        ).exists()

    root = ET.fromstring(SAMPLE_XML.replace("Opening Talk", "Updated Talk"))
    with scope(event=event):
        process_frab(root, event, release_new_version=False)

    with scopes_disabled():
        assert Submission.objects.filter(event=event, code="AAAAAA").count() == 1
        legacy.refresh_from_db()
        assert legacy.title == "Updated Talk"


@pytest.mark.django_db
def test_backfill_ignores_submission_without_schedule_provenance(event):
    with scope(event=event):
        original_type = SubmissionType.objects.create(
            event=event, name="Original Type", default_duration=30
        )
        Submission.objects.create(
            event=event,
            code="AAAAAA",
            title="Organic proposal",
            submission_type=original_type,
            state=SubmissionStates.SUBMITTED,
        )

    with scopes_disabled():
        backfill_imported_submissions(apps, None)
        assert not ImportedSubmission.objects.filter(event=event).exists()


@pytest.mark.django_db
def test_process_frab_integrity_error_falls_back_to_fresh_code(event):
    calls = []
    real_get_or_create = Submission.objects.get_or_create

    def fake_get_or_create(*args, **kwargs):
        if not calls:
            calls.append(1)
            raise IntegrityError("duplicate")
        return real_get_or_create(*args, **kwargs)

    root = ET.fromstring(SAMPLE_XML)
    with (
        scope(event=event),
        patch.object(
            Submission.objects, "get_or_create", side_effect=fake_get_or_create
        ),
    ):
        process_frab(root, event, release_new_version=False)

    assert len(calls) == 1
    with scopes_disabled():
        imported = ImportedSubmission.objects.get(event=event, upstream_id="AAAAAA")
        assert imported.submission.code != "AAAAAA"
        assert imported.submission.title == "Opening Talk"


@pytest.mark.django_db
def test_process_frab_handles_oversized_upstream_id(event):
    long_id = "X" * 40
    xml = SAMPLE_XML.replace('id="AAAAAA"', f'id="{long_id}"')
    root = ET.fromstring(xml)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)

    with scopes_disabled():
        mapping = ImportedSubmission.objects.get(event=event, upstream_id=long_id)
        assert len(mapping.submission.code) <= 16
        assert mapping.submission.title == "Opening Talk"
        with scope(event=event):
            process_frab(ET.fromstring(xml), event, release_new_version=False)
        assert Submission.objects.filter(event=event, title="Opening Talk").count() == 1
