"""Tests for S02 messenger changes: TEMPLATE_MAP routing and payload mapping."""

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
from server.template_helpers import (
    B2_CHECKLIST_TEXT,
    CUSTOMER_FACING_BERATER,
    build_gosniki_consultation_done_texts,
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

    def test_legacy_s01_lines_rejected(self):
        with pytest.raises(ValueError, match="Invalid line"):
            MessageData(line="first", termin_date="25.02.2026")
        with pytest.raises(ValueError, match="Invalid line"):
            MessageData(line="second", termin_date="25.02.2026")

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
        assert md.time is None
        assert md.checklist_text is None
        assert md.schedule_text is None
        assert md.topic is None
        assert md.subject_text is None
        assert md.datetime_text is None
        assert md.location_text is None

    def test_optional_fields_can_be_set(self):
        md = MessageData(
            line="berater_day_minus_3",
            termin_date="25.03.2026",
            name="Анна",
            institution=CUSTOMER_FACING_BERATER,
            weekday="Среда",
            date="25.03.2026",
            time="13:30",
            checklist_text=B2_CHECKLIST_TEXT,
            schedule_text="Среда, 25.03.2026",
            topic="встречу с Бератором",
            subject_text="вашем термине с Бератором",
            datetime_text="25.03.2026 в 13:30",
            location_text="на встрече с Бератором",
        )
        assert md.name == "Анна"
        assert md.institution == CUSTOMER_FACING_BERATER
        assert md.weekday == "Среда"
        assert md.date == "25.03.2026"
        assert md.time == "13:30"
        assert md.checklist_text == B2_CHECKLIST_TEXT
        assert md.schedule_text == "Среда, 25.03.2026"
        assert md.topic == "встречу с Бератором"
        assert md.subject_text == "вашем термине с Бератором"
        assert md.datetime_text == "25.03.2026 в 13:30"
        assert md.location_text == "на встрече с Бератором"


# -----------------------------------------------------------------------
# send_message() routing to correct template
# -----------------------------------------------------------------------

class TestSendMessageTemplateRouting:
    def test_gosniki_consultation_done_uses_correct_guid(self):
        messenger = _make_messenger()
        texts = build_gosniki_consultation_done_texts("Анна")
        md = MessageData(
            line="gosniki_consultation_done",
            termin_date="",
            name="Анна",
            news_text=texts["news_text"],
        )

        ok_resp = MagicMock()
        ok_resp.status_code = 201
        ok_resp.json.return_value = {"messageId": "msg-g1-001"}
        messenger.session.post.return_value = ok_resp

        result = messenger.send_message("+491234567890", md)
        assert result["status"] == "sent"
        assert result["message_id"] == "msg-g1-001"

        call_payload = messenger.session.post.call_args[1]["json"]
        assert call_payload["templateId"] == "95ddec60-bb6b-44a8-b5fb-a98abd76f974"
        assert call_payload["templateValues"] == [
            "SternMeister",
            (
                "Анна, вы получили комплект документов, необходимых для записи на термин. "
                "Мы уже забронировали для вас место для консультации с нашим карьерным экспертом. "
                "Пожалуйста, постарайтесь сегодня записаться на термин."
            ),
        ]

    def test_berater_accepted_uses_correct_guid(self):
        messenger = _make_messenger()
        md = MessageData(
            line="berater_accepted",
            termin_date="",
            name="Иван",
        )

        ok_resp = MagicMock()
        ok_resp.status_code = 201
        ok_resp.json.return_value = {"messageId": "msg-b1-001"}
        messenger.session.post.return_value = ok_resp

        result = messenger.send_message("+491234567890", md)
        call_payload = messenger.session.post.call_args[1]["json"]
        assert call_payload["templateId"] == "47d2946c-f66a-4697-b702-eb5d138bb1f1"
        assert call_payload["templateValues"] == ["Иван"]

    def test_berater_day_minus_7_sends_4_vars(self):
        messenger = _make_messenger()
        md = MessageData(
            line="berater_day_minus_7",
            termin_date="25.03.2026",
            name="Тест",
            date="25.03.2026",
            institution=CUSTOMER_FACING_BERATER,
            checklist_text=B2_CHECKLIST_TEXT,
        )

        ok_resp = MagicMock()
        ok_resp.status_code = 201
        ok_resp.json.return_value = {"messageId": "msg-b2-001"}
        messenger.session.post.return_value = ok_resp

        result = messenger.send_message("+491234567890", md)
        assert result["status"] == "sent"

        call_payload = messenger.session.post.call_args[1]["json"]
        assert call_payload["templateId"] == "b028964c-9c27-4bc9-9b97-02a5e283df16"
        assert call_payload["templateValues"] == [
            "Тест",
            "25.03.2026",
            CUSTOMER_FACING_BERATER,
            B2_CHECKLIST_TEXT,
        ]

    def test_berater_day_minus_3_sends_3_vars(self):
        messenger = _make_messenger()
        md = MessageData(
            line="berater_day_minus_3",
            termin_date="25.03.2026",
            name="Анна",
            institution=CUSTOMER_FACING_BERATER,
            schedule_text="Среда, 25.03.2026",
        )

        ok_resp = MagicMock()
        ok_resp.status_code = 201
        ok_resp.json.return_value = {"messageId": "msg-b3-001"}
        messenger.session.post.return_value = ok_resp

        result = messenger.send_message("+491234567890", md)
        assert result["status"] == "sent"

        call_payload = messenger.session.post.call_args[1]["json"]
        assert call_payload["templateId"] == "e1cb07aa-5236-4f8a-84dc-fef26b3cccf6"
        assert call_payload["templateValues"] == [
            "Анна",
            CUSTOMER_FACING_BERATER,
            "Среда, 25.03.2026",
        ]

    def test_berater_day_minus_1_sends_name_and_datetime(self):
        messenger = _make_messenger()
        md = MessageData(
            line="berater_day_minus_1",
            termin_date="25.03.2026",
            name="Борис",
            datetime_text="25.03.2026 в 13:30",
        )

        ok_resp = MagicMock()
        ok_resp.status_code = 201
        ok_resp.json.return_value = {"messageId": "msg-b4-001"}
        messenger.session.post.return_value = ok_resp

        messenger.send_message("+491234567890", md)
        call_payload = messenger.session.post.call_args[1]["json"]
        assert call_payload["templateId"] == "a9b04e05-6b6c-4a5f-9463-d8a0d96316f4"
        assert call_payload["templateValues"] == ["Борис", "25.03.2026 в 13:30"]

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
        texts = build_gosniki_consultation_done_texts("Анна")
        md = MessageData(
            line="gosniki_consultation_done",
            termin_date="",
            name="Анна",
            news_text=texts["news_text"],
        )
        text = m.build_message_text(md)
        assert "Анна" in text
        assert "записаться на термин" in text
        assert "[template]" in text

    def test_berater_day_minus_3_build_text(self):
        m = self._messenger()
        md = MessageData(
            line="berater_day_minus_3", termin_date="25.03.2026",
            name="Анна",
            institution=CUSTOMER_FACING_BERATER,
            schedule_text="Среда, 25.03.2026",
        )
        text = m.build_message_text(md)
        assert "Анна" in text
        assert CUSTOMER_FACING_BERATER in text
        assert "Среда, 25.03.2026" in text

    def test_berater_day_minus_7_build_text_contains_values(self):
        m = self._messenger()
        md = MessageData(
            line="berater_day_minus_7",
            termin_date="25.03.2026",
            name="Анна",
            date="25.03.2026",
            institution=CUSTOMER_FACING_BERATER,
            checklist_text=B2_CHECKLIST_TEXT,
        )
        text = m.build_message_text(md)
        assert "Анна" in text
        assert "25.03.2026" in text
        assert CUSTOMER_FACING_BERATER in text
        assert "Angebot" in text
