# -*- coding: utf-8 -*-
"""KIS 거래소 코드 캐시 자가 교정 계약 테스트 (2026-08-21).

실측 배경(WMT): KIS는 rt_cd=0 "정상처리"를 주면서 output 필드를 전부 빈 문자열로
돌려준다 — 오류가 아니라 "그 거래소엔 그 종목이 없다"는 뜻이다. Finnhub가 알려준
NYSE가 현실과는 맞지만 KIS 내부 심볼은 DNASWMT라 빈 응답이 됐고, Finnhub 폴백이
받아주는 바람에 08-20~21 62건이 조용히 묻혔다.

픽스처의 빈 응답은 실측 그대로 쓴다(rt_cd='0', last='') — 픽스처가 프로덕션과
다르면 테스트는 같은 버그를 영원히 통과시킨다.
"""
from __future__ import annotations

import unittest
from unittest import mock

import kis_api


# 실측 응답 형태 그대로. 키는 있고 값만 빈 문자열이다.
EMPTY_OUTPUT = {
    "base": "", "diff": "", "last": "", "ordy": "", "pvol": "", "rate": "",
    "rsym": "", "sign": "", "tamt": "", "tvol": "", "zdiv": "",
}
GOOD_OUTPUT = {
    "base": "114.3000", "diff": "-10.6150", "last": "103.6850", "ordy": "",
    "pvol": "39158161", "rate": "-9.29", "rsym": "DNASWMT", "sign": "5",
    "tamt": "4059000000", "tvol": "39158161", "zdiv": "4",
}


class _Resp:
    def __init__(self, output):
        self._output = output

    def raise_for_status(self):
        return None

    def json(self):
        return {"rt_cd": "0", "msg1": "정상처리 되었습니다.", "output": self._output}


class ExchangeSelfHealTests(unittest.TestCase):
    def setUp(self):
        self._cache = dict(kis_api._US_EXCHANGE_CACHE)
        self._tried = set(kis_api._US_EXCHANGE_SELF_HEAL_TRIED)
        kis_api._US_EXCHANGE_SELF_HEAL_TRIED.clear()
        self._saves = []
        p = mock.patch.object(kis_api, "_save_exchange_cache",
                              side_effect=lambda: self._saves.append(1))
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        kis_api._US_EXCHANGE_CACHE.clear()
        kis_api._US_EXCHANGE_CACHE.update(self._cache)
        kis_api._US_EXCHANGE_SELF_HEAL_TRIED.clear()
        kis_api._US_EXCHANGE_SELF_HEAL_TRIED.update(self._tried)

    def test_empty_response_triggers_cache_correction_and_returns_price(self):
        """NYS 빈 응답 → 재판별 → 캐시 NASD 교정 → 재조회 성공.

        하드코딩 맵에 없는 티커를 쓴다 — WMT는 08-21 교정으로 맵에 들어갔고,
        맵 종목은 자동 교정 대상이 아니다(사람이 고칠 일).
        """
        kis_api._US_EXCHANGE_CACHE["ZZTEST"] = "NYSE"
        calls = []

        def fake_get(url, headers=None, params=None, timeout=None):
            calls.append(params["EXCD"])
            return _Resp(GOOD_OUTPUT if params["EXCD"] == "NAS" else EMPTY_OUTPUT)

        with mock.patch.object(kis_api, "_kis_get", side_effect=fake_get):
            out = kis_api._get_price_us_kis("ZZTEST", "tok")

        self.assertAlmostEqual(out["price"], 103.685, places=3)
        self.assertEqual(kis_api._US_EXCHANGE_CACHE["ZZTEST"], "NASD")
        self.assertTrue(self._saves, "교정했으면 캐시 파일도 저장해야 한다")
        self.assertEqual(calls[0], "NYS", "첫 조회는 캐시된 NYS로 나가야 한다")

    def test_wmt_is_mapped_to_nasd(self):
        """WMT 회귀 — KIS는 NYSE 상장인 월마트를 NAS로 서비스한다(rsym=DNASWMT).

        이 값이 NYSE로 되돌아가면 08-20~21처럼 매 사이클 빈 응답 → Finnhub 폴백이
        재발하고, 하드코딩 맵이 캐시를 덮으므로 캐시 파일 수정으로는 못 고친다.
        """
        self.assertEqual(kis_api._hardcoded_us_exchange_code("WMT"), "NASD")
        self.assertEqual(kis_api._US_QUOTE_CODE_MAP["NASD"], "NAS")

    def test_self_heal_is_attempted_once_per_process(self):
        """어느 거래소에도 없는 종목이 매 사이클 REST를 더 치면 안 된다."""
        kis_api._US_EXCHANGE_CACHE["GONE"] = "NYSE"

        def fake_get(url, headers=None, params=None, timeout=None):
            return _Resp(EMPTY_OUTPUT)

        with mock.patch.object(kis_api, "_kis_get", side_effect=fake_get) as m:
            with self.assertRaises(ValueError):
                kis_api._get_price_us_kis("GONE", "tok")
            first = m.call_count
            with self.assertRaises(ValueError):
                kis_api._get_price_us_kis("GONE", "tok")
            second = m.call_count - first

        self.assertGreater(first, 1, "첫 회는 probe까지 시도한다")
        self.assertEqual(second, 1, "두 번째부터는 재-probe 없이 바로 폴백해야 한다")

    def test_hardcoded_ticker_is_not_auto_corrected(self):
        """사람이 검증한 override를 덮으면 다음 조회에서 되돌아가 진동한다."""
        kis_api._US_EXCHANGE_CACHE["HARD"] = "NYSE"

        def fake_get(url, headers=None, params=None, timeout=None):
            return _Resp(EMPTY_OUTPUT)

        with mock.patch.object(kis_api, "_hardcoded_us_exchange_code", return_value="NYSE"), \
                mock.patch.object(kis_api, "_kis_get", side_effect=fake_get), \
                mock.patch.object(kis_api, "_probe_us_exchange_code") as probe:
            with self.assertRaises(ValueError):
                kis_api._get_price_us_kis("HARD", "tok")

        probe.assert_not_called()
        self.assertEqual(kis_api._US_EXCHANGE_CACHE["HARD"], "NYSE")
        self.assertFalse(self._saves)

    def test_no_token_skips_self_heal(self):
        kis_api._US_EXCHANGE_CACHE["NOTOK"] = "NYSE"
        with mock.patch.object(kis_api, "_probe_us_exchange_code") as probe:
            self.assertIsNone(kis_api._self_heal_us_exchange("NOTOK", "", "NYS"))
        probe.assert_not_called()

    def test_same_code_resolved_is_not_a_correction(self):
        """재판별이 같은 코드를 주면 교정이 아니다 — 캐시를 건드리지 않는다."""
        kis_api._US_EXCHANGE_CACHE["SAME"] = "NYSE"
        with mock.patch.object(kis_api, "_hardcoded_us_exchange_code", return_value=None), \
                mock.patch.object(kis_api, "_probe_us_exchange_code", return_value="NYSE"):
            self.assertIsNone(kis_api._self_heal_us_exchange("SAME", "tok", "NYS"))
        self.assertEqual(kis_api._US_EXCHANGE_CACHE["SAME"], "NYSE")
        self.assertFalse(self._saves)


if __name__ == "__main__":
    unittest.main()
