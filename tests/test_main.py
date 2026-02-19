from io import StringIO
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree

import pytest
from django.core.management import call_command
from django.urls import reverse
from django_scopes import scope, scopes_disabled
from pretalx.schedule.models import Room
from pretalx.submission.models import Submission, Track

from pretalx_downstream.models import UpstreamResult
from pretalx_downstream.signals import refresh_upstream_schedule
from pretalx_downstream.tasks import (
    _create_user,
    process_frab,
    task_refresh_upstream_schedule,
)

SETTINGS_URL_NAME = "plugins:pretalx_downstream:settings"

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
        reverse(SETTINGS_URL_NAME, kwargs={"event": event.slug}),
        follow=True,
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_reviewer_cannot_access_settings(review_client, event):
    response = review_client.get(
        reverse(SETTINGS_URL_NAME, kwargs={"event": event.slug}),
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
    root = ElementTree.fromstring(SAMPLE_XML)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)
    with scopes_disabled():
        sub = Submission.objects.get(event=event, code="AAAAAA")
        assert sub.title == "Opening Talk"
        assert sub.speakers.count() == 1


@pytest.mark.django_db
def test_process_frab_creates_room(event):
    root = ElementTree.fromstring(SAMPLE_XML)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)
    with scopes_disabled():
        assert Room.objects.filter(event=event, name="Main Hall").exists()


@pytest.mark.django_db
def test_process_frab_creates_track(event):
    root = ElementTree.fromstring(SAMPLE_XML)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)
    with scopes_disabled():
        assert Track.objects.filter(event=event, name="General").exists()


@pytest.mark.django_db
def test_process_frab_release_new_version(event):
    root = ElementTree.fromstring(SAMPLE_XML)
    with scope(event=event):
        _, schedule = process_frab(root, event, release_new_version=True)
    assert schedule is not None
    assert schedule.version == "1.0"


@pytest.mark.django_db
def test_process_frab_detects_changes_on_reimport(event):
    root = ElementTree.fromstring(SAMPLE_XML)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)
        modified_xml = SAMPLE_XML.replace("Opening Talk", "Updated Talk")
        root2 = ElementTree.fromstring(modified_xml)
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
    with pytest.raises(Exception, match="no upstream URL"):
        task_refresh_upstream_schedule(event.slug)


@pytest.mark.django_db
def test_task_bad_response_raises(event):
    event.settings.downstream_upstream_url = "https://example.com/schedule.xml"
    mock_response = MagicMock()
    mock_response.status_code = 404
    with (
        patch("pretalx_downstream.tasks.requests.get", return_value=mock_response),
        pytest.raises(Exception, match="Could not retrieve"),
    ):
        task_refresh_upstream_schedule(event.slug)


@pytest.mark.django_db
def test_task_skips_unchanged(event):
    event.settings.downstream_upstream_url = "https://example.com/schedule.xml"
    content = SAMPLE_XML.encode()
    UpstreamResult.objects.create(event=event, content=SAMPLE_XML)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = SAMPLE_XML
    mock_response.content = content

    with patch("pretalx_downstream.tasks.requests.get", return_value=mock_response):
        task_refresh_upstream_schedule(event.slug)

    with scope(event=event):
        assert event.upstream_results.count() == 1


@pytest.mark.django_db
def test_task_processes_new_schedule(event):
    event.settings.downstream_upstream_url = "https://example.com/schedule.xml"
    content = SAMPLE_XML.encode()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = SAMPLE_XML
    mock_response.content = content

    with patch("pretalx_downstream.tasks.requests.get", return_value=mock_response):
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
    mock_response.status_code = 200
    mock_response.text = SAMPLE_XML
    mock_response.content = content

    with (
        patch("pretalx_downstream.tasks.requests.get", return_value=mock_response),
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
    root = ElementTree.fromstring(xml_with_guid)
    with scope(event=event):
        process_frab(root, event, release_new_version=False)
    with scopes_disabled():
        room = Room.objects.get(event=event, name="Main Hall")
        assert str(room.guid) == "12345678-abcd-1234-abcd-123456789012"


@pytest.mark.django_db
def test_talk_with_recording_optout(event):
    xml_optout = SAMPLE_XML.replace("<optout>false</optout>", "<optout>true</optout>")
    root = ElementTree.fromstring(xml_optout)
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
    root = ElementTree.fromstring(xml_subtitle)
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
    root = ElementTree.fromstring(xml_versioned)
    with scope(event=event):
        _, schedule = process_frab(root, event, release_new_version=True)
    assert schedule.version == "1.0"
