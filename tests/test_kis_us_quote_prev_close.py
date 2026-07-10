from __future__ import annotations

from unittest.mock import Mock, patch

from kis_api import _get_price_us_kis


def test_kis_us_quote_exposes_independent_previous_close() -> None:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "rt_cd": "0",
        "output": {
            "last": "100.5",
            "base": "99.5",
            "open": "100.0",
            "high": "101.0",
            "low": "99.8",
            "diff": "1.0",
            "rate": "1.005",
            "tvol": "12345",
        },
    }

    with patch("kis_api._get_us_quote_codes", return_value=("NASD", "NAS")), patch(
        "kis_api._kis_get", return_value=response
    ):
        quote = _get_price_us_kis("TEST", "token")

    assert quote["price"] == 100.5
    assert quote["prev_close"] == 99.5
    assert quote["volume"] == 12345
