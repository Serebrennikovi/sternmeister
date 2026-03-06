"""Tests for server.alerts — Telegram alerter (T09).

Unit tests for TelegramAlerter and integration tests verifying that
app.py and cron.py call the alerter on errors.
"""

from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time

from server.alerts import TelegramAlerter, _escape_md, _reset_alerter
from server.utils import mask_phone


# ---------------------------------------------------------------------------
# mask_phone
# ---------------------------------------------------------------------------

class TestMaskPhone:

    def test_normal_phone(self):
        assert mask_phone("+491234567890") == "+49***7890"

    def test_short_phone(self):
        assert mask_phone("+4912") == "***"

    def test_exactly_8_chars(self):
        assert mask_phone("+4912345") == "+49***2345"


# ---------------------------------------------------------------------------
# _escape_md
# ---------------------------------------------------------------------------

class TestEscapeMd:

    def test_no_special_chars(self):
        assert _escape_md("simple text 123") == "simple text 123"

    def test_asterisks(self):
        assert _escape_md("*bold* text") == "\\*bold\\* text"

    def test_underscores(self):
        assert _escape_md("_italic_ text") == "\\_italic\\_ text"

    def test_backticks(self):
        assert _escape_md("`code` text") == "\\`code\\` text"

    def test_brackets(self):
        assert _escape_md("[link](url)") == "\\[link](url)"

    def test_mixed(self):
        assert _escape_md("*err* in `func_name`") == "\\*err\\* in \\`func\\_name\\`"

    def test_empty_string(self):
        assert _escape_md("") == ""


# ---------------------------------------------------------------------------
# TelegramAlerter — unit tests
# ---------------------------------------------------------------------------

@pytest.fixture
def alerter_enabled():
    """Alerter with token and chat_id configured."""
    with patch("server.config.TELEGRAM_BOT_TOKEN", "test-token-123"), \
         patch("server.config.TELEGRAM_ALERT_CHAT_ID", "999"):
        _reset_alerter()
        a = TelegramAlerter()
    assert a.enabled is True
    return a


@pytest.fixture
def alerter_disabled():
    """Alerter with no token (disabled)."""
    with patch("server.config.TELEGRAM_BOT_TOKEN", ""), \
         patch("server.config.TELEGRAM_ALERT_CHAT_ID", ""):
        _reset_alerter()
        a = TelegramAlerter()
    assert a.enabled is False
    return a


class TestSendAlert:

    @freeze_time("2026-02-24T14:00:00Z")
    @patch("server.alerts.requests.post")
    def test_success(self, mock_post, alerter_enabled):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        result = alerter_enabled.send_alert("Test error", level="ERROR")

        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs["json"] if "json" in call_kwargs.kwargs else call_kwargs[1]["json"]
        assert payload["chat_id"] == "999"
        assert "*ERROR*" in payload["text"]
        assert "Test error" in payload["text"]
        assert "14:00 UTC" in payload["text"]
        assert payload["parse_mode"] == "Markdown"

    @patch("server.alerts.requests.post")
    def test_api_error(self, mock_post, alerter_enabled):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        mock_post.return_value = mock_resp

        result = alerter_enabled.send_alert("msg")

        assert result is False

    @patch("server.alerts.requests.post")
    def test_request_exception(self, mock_post, alerter_enabled):
        import requests
        mock_post.side_effect = requests.exceptions.ConnectionError("no network")

        result = alerter_enabled.send_alert("msg")

        assert result is False

    def test_disabled_no_request(self, alerter_disabled):
        with patch("server.alerts.requests.post") as mock_post:
            result = alerter_disabled.send_alert("msg")

        assert result is False
        mock_post.assert_not_called()

    @patch("server.alerts.requests.post")
    def test_warning_level(self, mock_post, alerter_enabled):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        alerter_enabled.send_alert("warn", level="WARNING")

        payload = mock_post.call_args.kwargs["json"]
        assert "*WARNING*" in payload["text"]

    @patch("server.alerts.requests.post")
    def test_info_level(self, mock_post, alerter_enabled):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        alerter_enabled.send_alert("info", level="INFO")

        payload = mock_post.call_args.kwargs["json"]
        assert "*INFO*" in payload["text"]


class TestAlertMessengerError:

    @patch("server.alerts.requests.post")
    def test_masks_phone(self, mock_post, alerter_enabled):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        alerter_enabled.alert_messenger_error("+491234567890", "timeout")

        payload = mock_post.call_args.kwargs["json"]
        assert "+49***7890" in payload["text"]
        assert "+491234567890" not in payload["text"]
        assert "timeout" in payload["text"]


    @patch("server.alerts.requests.post")
    def test_escapes_markdown_in_error(self, mock_post, alerter_enabled):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        alerter_enabled.alert_messenger_error("+491234567890", "*retry* `failed`")

        payload = mock_post.call_args.kwargs["json"]
        # Error text should have Markdown chars escaped
        assert "\\*retry\\*" in payload["text"]
        assert "\\`failed\\`" in payload["text"]
        # But intentional formatting (backtick around phone) should NOT be escaped
        assert "`+49***7890`" in payload["text"]


class TestAlertKommoError:

    @patch("server.alerts.requests.post")
    def test_includes_lead_id(self, mock_post, alerter_enabled):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        alerter_enabled.alert_kommo_error(12345, "404 Not Found")

        payload = mock_post.call_args.kwargs["json"]
        assert "12345" in payload["text"]
        assert "404 Not Found" in payload["text"]


class TestAlertCronError:

    @patch("server.alerts.requests.post")
    def test_includes_error(self, mock_post, alerter_enabled):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        alerter_enabled.alert_cron_error("db connection lost")

        payload = mock_post.call_args.kwargs["json"]
        assert "db connection lost" in payload["text"]
        assert "cron" in payload["text"].lower()


class TestAlertUnexpectedError:

    @patch("server.alerts.requests.post")
    def test_includes_error(self, mock_post, alerter_enabled):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        alerter_enabled.alert_unexpected_error("something broke")

        payload = mock_post.call_args.kwargs["json"]
        assert "something broke" in payload["text"]
        assert "Неожиданная ошибка webhook" in payload["text"]

    @patch("server.alerts.requests.post")
    def test_escapes_markdown(self, mock_post, alerter_enabled):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        alerter_enabled.alert_unexpected_error("*bold* `code`")

        payload = mock_post.call_args.kwargs["json"]
        assert "\\*bold\\*" in payload["text"]
        assert "\\`code\\`" in payload["text"]


class TestAlertInfo:

    @patch("server.alerts.requests.post")
    def test_info(self, mock_post, alerter_enabled):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        alerter_enabled.alert_info("System started")

        payload = mock_post.call_args.kwargs["json"]
        assert "*INFO*" in payload["text"]
        assert "System started" in payload["text"]

    @patch("server.alerts.requests.post")
    def test_escapes_markdown(self, mock_post, alerter_enabled):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        alerter_enabled.alert_info("version *2.0* ready")

        payload = mock_post.call_args.kwargs["json"]
        assert "\\*2.0\\*" in payload["text"]


# ---------------------------------------------------------------------------
# Integration: webhook handler calls alerter on errors
# ---------------------------------------------------------------------------

class TestWebhookAlerterIntegration:
    """Verify that webhook handler triggers alerts on Kommo/Messenger errors."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from server.app import app
        return TestClient(app)

    def _payload(self, lead_id=123, status_id=93860331, pipeline_id=12154099):
        return {
            "leads": {
                "status": [{
                    "id": lead_id,
                    "status_id": status_id,
                    "pipeline_id": pipeline_id,
                    "old_status_id": 99999,
                }]
            },
        }

    @patch("server.app.get_alerter")
    @patch("server.app.get_webhook_line_exists", return_value=False)
    @patch("server.app.get_kommo_client")
    def test_kommo_error_triggers_alert(self, mock_gc, mock_dedup, mock_ga, client):
        from server.kommo import KommoAPIError

        kommo = MagicMock()
        kommo.get_lead_contact.side_effect = KommoAPIError("503 unavailable")
        mock_gc.return_value = kommo

        alerter = MagicMock()
        mock_ga.return_value = alerter

        resp = client.post("/webhook/kommo", json=self._payload())

        assert resp.status_code == 200
        alerter.alert_kommo_error.assert_called_once_with(123, "503 unavailable")

    @patch("server.app.get_alerter")
    @patch("server.app.get_webhook_line_exists", return_value=False)
    @patch("server.app.get_kommo_client")
    def test_no_phone_triggers_warning(self, mock_gc, mock_dedup, mock_ga, client):
        kommo = MagicMock()
        kommo.get_lead_contact.return_value = (
            {"id": 123, "custom_fields_values": []},
            {"id": 456, "custom_fields_values": []},
        )
        kommo.extract_phone.return_value = None
        mock_gc.return_value = kommo

        alerter = MagicMock()
        mock_ga.return_value = alerter

        resp = client.post("/webhook/kommo", json=self._payload())

        assert resp.status_code == 200
        alerter.send_alert.assert_called_once()
        call_args = alerter.send_alert.call_args
        assert "123" in call_args[0][0]
        assert call_args[1]["level"] == "WARNING"

    @patch("server.app.get_alerter")
    @patch("server.app.get_webhook_line_exists", return_value=False)
    @patch("server.app.get_kommo_client")
    def test_no_name_triggers_warning(self, mock_gc, mock_dedup, mock_ga, client):
        """For berater_accepted (S02): missing name → send_alert WARNING."""
        kommo = MagicMock()
        kommo.get_lead_contact.return_value = (
            {"id": 123, "custom_fields_values": []},
            {"id": 456, "custom_fields_values": [
                {"field_code": "PHONE", "values": [{"value": "+491234567890"}]},
            ]},
        )
        kommo.extract_phone.return_value = "+491234567890"
        kommo.extract_termin_date.return_value = None  # optional for berater_accepted
        kommo.extract_name.return_value = None          # name missing → warning
        mock_gc.return_value = kommo

        alerter = MagicMock()
        mock_ga.return_value = alerter

        resp = client.post("/webhook/kommo", json=self._payload())

        assert resp.status_code == 200
        alerter.send_alert.assert_called_once()
        call_args = alerter.send_alert.call_args
        assert "123" in call_args[0][0]
        assert call_args[1]["level"] == "WARNING"

    @freeze_time("2026-02-24T14:00:00Z")  # inside send window
    @patch("server.app.get_alerter")
    @patch("server.app.get_webhook_line_exists", return_value=False)
    @patch("server.app.get_kommo_client")
    @patch("server.app.get_messenger")
    @patch("server.app.create_message", return_value=1)
    def test_messenger_error_triggers_alert(
        self, mock_cm, mock_gm, mock_gc, mock_dedup, mock_ga, client,
    ):
        from server.messenger import MessengerError

        kommo = MagicMock()
        kommo.get_lead_contact.return_value = (
            {"id": 123, "custom_fields_values": [
                {"field_id": 885996, "values": [{"value": 1740000000}]},
            ]},
            {"id": 456, "custom_fields_values": [
                {"field_code": "PHONE", "values": [{"value": "+491234567890"}]},
            ]},
        )
        kommo.extract_phone.return_value = "+491234567890"
        kommo.extract_termin_date.return_value = "25.02.2026"
        kommo.extract_name.return_value = "Test User"  # required for berater_accepted
        mock_gc.return_value = kommo

        messenger = MagicMock()
        messenger.build_message_text.return_value = "Test msg"
        messenger.send_message.side_effect = MessengerError("Wazzup timeout")
        mock_gm.return_value = messenger

        alerter = MagicMock()
        mock_ga.return_value = alerter

        resp = client.post("/webhook/kommo", json=self._payload())

        assert resp.status_code == 200
        alerter.alert_messenger_error.assert_called_once_with(
            "+491234567890", "Wazzup timeout",
        )


# ---------------------------------------------------------------------------
# Integration: cron calls alerter on errors
# ---------------------------------------------------------------------------

class TestCronAlerterIntegration:
    """Verify that cron triggers alerts on errors."""

    @patch("server.cron.get_alerter")
    @patch("server.cron.process_retries", side_effect=RuntimeError("disk full"))
    def test_fatal_error_triggers_alert(self, mock_pr, mock_ga):
        alerter = MagicMock()
        mock_ga.return_value = alerter

        from server.cron import main
        result = main()

        assert result == 1
        alerter.alert_cron_error.assert_called_once_with("disk full")

    @freeze_time("2026-02-24T14:00:00Z")  # inside send window
    @patch("server.cron.get_alerter")
    @patch("server.cron.get_messenger")
    @patch("server.cron.get_messages_for_retry")
    @patch("server.cron.update_message")
    def test_retry_failure_triggers_messenger_alert(
        self, mock_um, mock_gmr, mock_gm, mock_ga,
    ):
        """Individual retry failure calls alert_messenger_error."""
        from server.messenger import MessengerError

        mock_gmr.return_value = [
            {"id": 1, "phone": "+491234567890", "line": "first",
             "termin_date": "25.02.2026", "attempts": 1, "kommo_lead_id": 100,
             "template_values": None},
        ]
        m = MagicMock()
        m.send_message.side_effect = MessengerError("Wazzup timeout")
        mock_gm.return_value = m

        alerter = MagicMock()
        mock_ga.return_value = alerter

        from server.cron import process_retries
        process_retries()

        alerter.alert_messenger_error.assert_called_once_with(
            "+491234567890", "Wazzup timeout",
        )

    @freeze_time("2026-02-24T14:00:00Z")  # inside send window
    @patch("server.cron.get_alerter")
    @patch("server.cron.get_messenger")
    @patch("server.cron.get_pending_messages")
    def test_pending_failure_triggers_messenger_alert(
        self, mock_gpm, mock_gm, mock_ga,
    ):
        """Individual pending send failure calls alert_messenger_error."""
        from server.messenger import MessengerError

        mock_gpm.return_value = [
            {"id": 2, "phone": "+499876543210", "line": "second",
             "termin_date": "01.03.2026", "attempts": 0, "kommo_lead_id": 200,
             "template_values": None},
        ]
        m = MagicMock()
        m.send_message.side_effect = MessengerError("connection refused")
        mock_gm.return_value = m

        alerter = MagicMock()
        mock_ga.return_value = alerter

        from server.cron import process_pending
        process_pending()

        alerter.alert_messenger_error.assert_called_once_with(
            "+499876543210", "connection refused",
        )
