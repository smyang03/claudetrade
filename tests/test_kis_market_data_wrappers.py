from __future__ import annotations

from unittest.mock import Mock, patch

import kis_api


def test_kr_orderbook_wrapper_returns_degraded_on_timeout() -> None:
    with patch.object(kis_api, "_kis_get", side_effect=TimeoutError("timeout")):
        row = kis_api.get_kr_orderbook_snapshot("token", "005930")

    assert row["source"] == "kis_orderbook"
    assert row["data_quality"] == "ERROR"
    assert "orderbook" in row["missing_fields"]


def test_kr_vi_wrapper_parses_success_payload() -> None:
    response = Mock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = {"output": {"vi_cls_code": "1", "vi_trg_prc": "70100"}}

    with patch.object(kis_api, "_kis_get", return_value=response):
        row = kis_api.get_kr_vi_state("token", "005930")

    assert row["source"] == "kis_vi"
    assert row["data_quality"] == "OK"
    assert row["vi_active"] is True
    assert row["vi_trigger_price"] == 70100.0
