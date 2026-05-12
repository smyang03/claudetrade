from __future__ import annotations

from unittest.mock import patch

import requests

import telegram_reporter


class _Response:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            exc = requests.HTTPError(f"{self.status_code} error")
            exc.response = self
            raise exc


def test_send_plain_text_fallback_recovers_html_400() -> None:
    with patch.object(telegram_reporter, "TOKEN", "token"), patch.object(
        telegram_reporter,
        "CHAT_ID",
        "chat",
    ), patch.object(
        telegram_reporter.requests,
        "post",
        side_effect=[_Response(400, "can't parse entities"), _Response(200, "ok")],
    ) as post_mock:
        assert telegram_reporter.send("<b>broken", parse_mode="HTML") is True

    first_payload = post_mock.call_args_list[0].kwargs["json"]
    fallback_payload = post_mock.call_args_list[1].kwargs["json"]
    assert first_payload["parse_mode"] == "HTML"
    assert "parse_mode" not in fallback_payload
