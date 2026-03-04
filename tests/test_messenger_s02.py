"""Tests for S02 messenger changes: TEMPLATE_MAP routing, skipped placeholder (T12)."""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from server.messenger.wazzup import (
    MessageData,
    MessengerError,
    WazzupMessenger,
    _reset_messenger,
)


@pytest.fixture(autouse=True)
def reset_messenger():
    _reset_messenger()
    yield
    _reset_messenger()


def _make_messenger():
    """Create a WazzupMessenger with mocked session (no real HTTP calls)."""
    messenger = WazzupMessenger.__new__(WazzupMessenger)
    messenger.channel_id = "test-channel"
    messenger.base_url = "https://api.wazzup24.com/v3"
    messenger.session = MagicMock()
    return messenger


# -----------------------------------------------------------------------
# MessageData
# -----------------------------------------------------------------------

class TestMessageData:
    def test_valid_s02_lines_accepted(self):
        for line in [
            "gosniki_consultation_done", "berater_accepted",
            "berater_day_minus_7", "berater_day_minus_3",
            "berater_day_minus_1", "berater_day_0",
        ]:
            md = MessageData(line=line, termin_date="")
            assert md.line == line

    def test_s01_lines_still_accepted(self):
        md = MessageData(line="first", termin_date="25.02.2026")
        assert md.line == "first"

    def test_empty_termin_date_allowed(self):
        # For gosniki/berater lines, termin_date="" is allowed
        md = MessageData(line="gosniki_consultation_done", termin_date="")
        assert md.termin_date == ""

    def test_invalid_line_raises(self):
        with pytest.raises(ValueError, match="Invalid line"):
            MessageData(line="unknown_line", termin_date="")

    def test_optional_fields_default_none(self):
        md = MessageData(line="berater_accepted", termin_date="")
        assert md.name is None
        assert md.institution is None
        assert md.weekday is None
        assert md.date is None

    def test_optional_fields_can_be_set(self):
        md = MessageData(
            line="berater_day_minus_3",
            termin_date="25.03.2026",
            name="Анна",
            institution="Jobcenter",
            weekday="Среда",
            date="25.03.2026",
        )
        assert md.name == "Анна"
        assert md.institution == "Jobcenter"
        assert md.weekday == "Среда"
        assert md.date == "25.03.2026"


# -----------------------------------------------------------------------
# send_message() routing to correct template
# -----------------------------------------------------------------------

class TestSendMessageTemplateRouting:
    def test_gosniki_consultation_done_uses_correct_guid(self):
        messenger = _make_messenger()
        md = MessageData(line="gosniki_consultation_done", termin_date="", name="Анна")

        ok_resp = MagicMock()
        ok_resp.status_code = 201
        ok_resp.json.return_value = {"messageId": "msg-g1-001"}
        messenger.session.post.return_value = ok_resp

        result = messenger.send_message("+491234567890", md)
        assert result["status"] == "sent"
        assert result["message_id"] == "msg-g1-001"

        call_payload = messenger.session.post.call_args[1]["json"]
        assert call_payload["templateId"] == "d253993f-e2fc-441f-a877-0c2252cb300b"
        assert call_payload["templateValues"] == ["Анна"]

    def test_berater_accepted_uses_correct_guid(self):
        messenger = _make_messenger()
        md = MessageData(line="berater_accepted", termin_date="", name="Иван")

        ok_resp = MagicMock()
        ok_resp.status_code = 201
        ok_resp.json.return_value = {"messageId": "msg-b1-001"}
        messenger.session.post.return_value = ok_resp

        result = messenger.send_message("+491234567890", md)
        call_payload = messenger.session.post.call_args[1]["json"]
        assert call_payload["templateId"] == "18b763f8-1841-43fb-af65-669ab4c8dcea"
        assert call_payload["templateValues"] == ["Иван"]

    def test_berater_day_minus_7_is_skipped(self):
        """berater_day_minus_7 has template_guid=None → skipped, no HTTP call."""
        messenger = _make_messenger()
        md = MessageData(line="berater_day_minus_7", termin_date="25.03.2026", name="Тест")

        result = messenger.send_message("+491234567890", md)
        assert result == {"status": "skipped"}
        messenger.session.post.assert_not_called()

    def test_berater_day_minus_3_sends_4_vars(self):
        messenger = _make_messenger()
        md = MessageData(
            line="berater_day_minus_3",
            termin_date="25.03.2026",
            name="Анна",
            institution="Jobcenter",
            weekday="Среда",
            date="25.03.2026",
        )

        ok_resp = MagicMock()
        ok_resp.status_code = 201
        ok_resp.json.return_value = {"messageId": "msg-b3-001"}
        messenger.session.post.return_value = ok_resp

        result = messenger.send_message("+491234567890", md)
        assert result["status"] == "sent"

        call_payload = messenger.session.post.call_args[1]["json"]
        assert call_payload["templateId"] == "140a1ed5-7047-4de1-aa0d-d3fe5e0d912a"
        assert call_payload["templateValues"] == ["Анна", "Jobcenter", "Среда", "25.03.2026"]

    def test_berater_day_minus_1_sends_name(self):
        messenger = _make_messenger()
        md = MessageData(line="berater_day_minus_1", termin_date="25.03.2026", name="Борис")

        ok_resp = MagicMock()
        ok_resp.status_code = 201
        ok_resp.json.return_value = {"messageId": "msg-b4-001"}
        messenger.session.post.return_value = ok_resp

        messenger.send_message("+491234567890", md)
        call_payload = messenger.session.post.call_args[1]["json"]
        assert call_payload["templateId"] == "7732e8ac-1bcc-42d6-a723-bbb80b635c79"
        assert call_payload["templateValues"] == ["Борис"]

    def test_berater_day_0_sends_name(self):
        messenger = _make_messenger()
        md = MessageData(line="berater_day_0", termin_date="25.03.2026", name="Карина")

        ok_resp = MagicMock()
        ok_resp.status_code = 201
        ok_resp.json.return_value = {"messageId": "msg-b5-001"}
        messenger.session.post.return_value = ok_resp

        messenger.send_message("+491234567890", md)
        call_payload = messenger.session.post.call_args[1]["json"]
        assert call_payload["templateId"] == "176a8b5b-8704-4d04-aee5-0fbd08641806"
        assert call_payload["templateValues"] == ["Карина"]


# -----------------------------------------------------------------------
# build_message_text()
# -----------------------------------------------------------------------

class TestBuildMessageText:
    def _messenger(self):
        return _make_messenger()

    def test_gosniki_build_text(self):
        m = self._messenger()
        md = MessageData(line="gosniki_consultation_done", termin_date="", name="Анна")
        text = m.build_message_text(md)
        assert "Анна" in text
        assert "[template]" in text

    def test_berater_day_minus_3_build_text(self):
        m = self._messenger()
        md = MessageData(
            line="berater_day_minus_3", termin_date="25.03.2026",
            name="Анна", institution="Jobcenter", weekday="Среда", date="25.03.2026",
        )
        text = m.build_message_text(md)
        assert "Анна" in text
        assert "Jobcenter" in text
        assert "Среда" in text

    def test_berater_day_minus_7_build_text_is_placeholder(self):
        m = self._messenger()
        md = MessageData(line="berater_day_minus_7", termin_date="25.03.2026")
        text = m.build_message_text(md)
        assert "berater_day_minus_7" in text
        assert "placeholder" in text
