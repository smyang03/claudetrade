# -*- coding: utf-8 -*-
"""KIS 거래량순위 파서 — 신규 상장 영숫자 코드(0009K0 등)를 버리지 않는다(10-31). 네트워크 없음."""
from __future__ import annotations

from unittest.mock import patch

import kis_api


class _Resp:
    def __init__(self, items):
        self._items = items

    def raise_for_status(self):
        return None

    def json(self):
        return {"output": self._items}


def _item(code):
    return {"mksc_shrn_iscd": code, "hts_kor_isnm": code, "stck_prpr": "10000", "prdy_ctrt": "1.0",
            "acml_vol": "1000", "vol_tnrt": "1.0"}


def test_volume_rank_keeps_alphanumeric_six_char_codes():
    items = [_item("005930"), _item("0009k0"), _item("Q500001"), _item(""), _item("12345")]
    with patch.object(kis_api, "_kis_get", return_value=_Resp(items)), patch.object(kis_api, "_headers", return_value={}):
        rows = kis_api._kis_volume_rank("tok", "0", 10, input_iscd="1001")
    assert [r["ticker"] for r in rows] == ["005930", "0009K0"]
    assert all(r["market_type"] == "KOSDAQ" for r in rows)
