"""Tests for parse_bracket_form — PHP bracket notation parser (T06)."""

from server.utils import parse_bracket_form


class TestParseBracketForm:
    """Parse x-www-form-urlencoded with PHP bracket notation."""

    def test_kommo_status_change(self):
        """Real-world Kommo webhook: leads[status][0][...]=..."""
        body = (
            b"leads[status][0][id]=123"
            b"&leads[status][0][status_id]=456"
            b"&leads[status][0][pipeline_id]=789"
            b"&leads[status][0][old_status_id]=111"
        )
        result = parse_bracket_form(body)
        assert result == {
            "leads": {
                "status": [{
                    "id": "123",
                    "status_id": "456",
                    "pipeline_id": "789",
                    "old_status_id": "111",
                }]
            }
        }

    def test_multiple_status_entries(self):
        """Multiple status changes in one webhook."""
        body = (
            b"leads[status][0][id]=1&leads[status][0][status_id]=10"
            b"&leads[status][1][id]=2&leads[status][1][status_id]=20"
        )
        result = parse_bracket_form(body)
        status_list = result["leads"]["status"]
        assert len(status_list) == 2
        assert status_list[0] == {"id": "1", "status_id": "10"}
        assert status_list[1] == {"id": "2", "status_id": "20"}

    def test_nested_dict(self):
        """Non-array nested keys: account[id]=xxx."""
        body = b"account[id]=xxxxx&account[subdomain]=sternmeister"
        result = parse_bracket_form(body)
        assert result == {"account": {"id": "xxxxx", "subdomain": "sternmeister"}}

    def test_flat_key(self):
        """Simple key=value without brackets."""
        body = b"simple_key=hello"
        result = parse_bracket_form(body)
        assert result == {"simple_key": "hello"}

    def test_empty_body(self):
        """Empty body returns empty dict."""
        assert parse_bracket_form(b"") == {}

    def test_blank_value(self):
        """Blank values are preserved."""
        body = b"key[sub]="
        result = parse_bracket_form(body)
        assert result == {"key": {"sub": ""}}

    def test_url_encoded_value(self):
        """URL-encoded characters in values."""
        body = b"key=hello%20world"
        result = parse_bracket_form(body)
        assert result == {"key": "hello world"}

    def test_full_kommo_payload(self):
        """Combined payload: leads + account."""
        body = (
            b"leads[status][0][id]=12345"
            b"&leads[status][0][status_id]=9386032"
            b"&leads[status][0][pipeline_id]=12154099"
            b"&account[id]=99999"
            b"&account[subdomain]=sternmeister"
        )
        result = parse_bracket_form(body)
        assert result["leads"]["status"][0]["id"] == "12345"
        assert result["leads"]["status"][0]["pipeline_id"] == "12154099"
        assert result["account"]["subdomain"] == "sternmeister"
