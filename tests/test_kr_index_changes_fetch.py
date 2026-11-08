# -*- coding: utf-8 -*-
"""KR 지수 정기변경 명단 자동 수집(tools/kr_index_changes_fetch.py) — 파서·판정 게이트·CSV 쓰기."""
from __future__ import annotations

import codecs
import csv
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import kr_index_changes_fetch as F  # noqa: E402

ANN = date(2026, 5, 22)

# 연합뉴스 배포 기사 형식(2026-05-22 실제 기사를 줄인 것) — [코드] 표기·KRX300 구간 포함
YONHAP = """
코스피200 DB하이텍 포함되고, GS건설 빠진다…4개종목 맞교체
(서울=연합뉴스) 김유향 기자 = 코스피200 지수에 DB하이텍 등 4개 종목이 포함되고 GS건설[006360] 등 4개 종목이 빠진다.
한국거래소는 최근 주가지수운영위원회를 열어 주요 대표지수인 코스피200과 코스닥150, KRX300 지수의 구성종목에 대한 정기 변경을 심의했다고 22일 밝혔다.
이번 결정된 종목 변경은 다음달 12일부터 반영될 예정이다.
지수에서 HD건설기계[267270]과 DB하이텍[000990], 달바글로벌, OCI[456040] 등은 편입되고, 반면 GS건설과 세방전지[004490], GKL[114090], 녹십자홀딩스[005250]는 제외된다.
코스닥150 지수에서는 총 3종목이 편입되고, 같은 수의 종목이 편출된다.
이번에 편입되는 종목은 삼표시멘트[038500], 에이치브이엠[295310], 현대무벡스[319400] 등이다.
반면 지수에서 편출되는 종목은 성일하이텍[365340], 현대힘스[460930], 에코프로에이치엔[383310] 등이다.
KRX300 지수에는 총 45종목이 들어오고, 42종목이 나가게 됐다.
대표적인 편입 종목으로는 삼표시멘트와 대한조선[439260], 미래에셋생명[085620] 등이 있고, 편출 종목 중에는 효성[004800] 등이 있다.
"""

# 같은 회차 다른 언론사 — 코드 없이 이름만, 동사가 앞에 오는 문형(△ 나열)
OTHER_OUTLET = """
한국거래소는 22일 코스피200·코스닥150 정기변경 심의 결과를 발표했다. 변경 내용은 6월 12일부터 적용된다.
코스피200에 새로 편입되는 4개 종목은 △HD건설기계 △DB하이텍 △달바글로벌 △OCI 이다.
반면 편출되는 4개 종목은 △GS건설 △세방전지 △GKL △녹십자홀딩스 다.
코스닥150에서는 삼표시멘트, 에이치브이엠, 현대무벡스 등 3개 종목이 신규 편입된다.
"""

# 예상 기사 — 증권사 편입 예상 명단(쓰면 안 됨)
PREDICTION = """
다음달 코스피200 정기변경을 앞두고 증권가가 편입 예상 종목을 내놨다.
한국거래소는 이달 말 정기변경 결과를 발표할 예정이며 변경은 다음달 12일부터 반영된다.
증권가는 HD건설기계, DB하이텍, 달바글로벌, OCI 등 4개 종목이 편입될 것으로 예상했다.
"""

# 특례(분할 재상장) 문장이 섞인 기사
SPECIAL = YONHAP + "\n인적분할로 신설된 삼성에피스홀딩스는 재상장 다음날인 오는 25일 코스피200에 편입되고 정기변경 이후에도 잔류한다고 거래소는 밝혔다.\n"


def _nb() -> F.NameBook:
    nb = F.NameBook()
    for name, code, mk in [
        ("HD건설기계", "267270", "Y"), ("DB하이텍", "000990", "Y"), ("달바글로벌", "483650", "Y"), ("OCI", "456040", "Y"),
        ("GS건설", "006360", "Y"), ("세방전지", "004490", "Y"), ("GKL", "114090", "Y"), ("녹십자홀딩스", "005250", "Y"),
        ("삼표시멘트", "038500", "K"), ("에이치브이엠", "295310", "K"), ("현대무벡스", "319400", "K"),
        ("성일하이텍", "365340", "K"), ("현대힘스", "460930", "K"), ("에코프로에이치엔", "383310", "K"),
        ("대한조선", "439260", "Y"), ("미래에셋생명", "085620", "Y"), ("효성", "004800", "Y"),
        ("삼성에피스홀딩스", "0126Z0", "Y"),
    ]:
        nb.add(name, code, mk)
    return nb.finalize()


def test_expected_effective_is_day_after_second_thursday():
    assert F.expected_effective(date(2026, 5, 22)) == date(2026, 6, 12)
    assert F.expected_effective(date(2025, 11, 18)) == date(2025, 12, 12)
    assert F.expected_effective(date(2024, 11, 21)) == date(2024, 12, 13)
    assert F.expected_effective(date(2023, 11, 23)) == date(2023, 12, 15)
    assert F.expected_effective(date(2026, 11, 20)) == date(2026, 12, 11)
    assert F.expected_effective(date(2026, 2, 26)) is None


def test_parse_yonhap_wire_codes_and_krx300_excluded():
    p = F.parse_text(YONHAP, _nb(), ANN)
    assert p.is_announcement and p.effective == date(2026, 6, 12)
    assert set(p.adds["KOSPI200"]) == {"267270", "000990", "483650", "456040"}
    assert set(p.dels["KOSPI200"]) == {"006360", "004490", "114090", "005250"}
    assert set(p.adds["KOSDAQ150"]) == {"038500", "295310", "319400"}
    assert set(p.dels["KOSDAQ150"]) == {"365340", "460930", "383310"}
    # KRX300 구간 종목(대한조선·미래에셋생명·효성)은 어느 지수에도 들어가지 않는다
    allc = set().union(*[set(v) for v in p.adds.values()], *[set(v) for v in p.dels.values()])
    assert not {"439260", "085620", "004800"} & allc
    assert p.counts[("KOSPI200", "add")] == 4 and p.counts[("KOSDAQ150", "add")] == 3


def test_parse_verb_before_names_and_name_only():
    p = F.parse_text(OTHER_OUTLET, _nb(), ANN)
    assert p.is_announcement
    assert set(p.adds["KOSPI200"]) == {"267270", "000990", "483650", "456040"}
    assert set(p.dels["KOSPI200"]) == {"006360", "004490", "114090", "005250"}
    assert set(p.adds["KOSDAQ150"]) == {"038500", "295310", "319400"}


def test_prediction_sentences_never_become_adds():
    p = F.parse_text(PREDICTION, _nb(), ANN)
    assert not p.adds.get("KOSPI200")


def test_special_inclusion_sentence_excluded():
    p = F.parse_text(SPECIAL, _nb(), ANN)
    assert "0126Z0" not in p.adds.get("KOSPI200", {})
    assert len(p.adds["KOSPI200"]) == 4


def _meta(outlet: str) -> dict:
    return {"url": f"https://n.news.naver.com/mnews/article/{outlet}/1", "orig": f"https://{outlet}.example/1",
            "outlet": outlet, "pub": datetime(2026, 5, 22, 17, 30, tzinfo=F.KST), "title": "코스피200 정기변경"}


def test_decide_needs_two_outlets_with_matching_count():
    nb = _nb()
    one = [(_meta("a"), F.parse_text(YONHAP, nb, ANN))]
    d1 = F.decide(one, ANN, nb, market_lookup=lambda c: "")
    assert not d1["KOSPI200"].ok and not d1["KOSDAQ150"].ok          # 1곳이면 보류
    two = one + [(_meta("b"), F.parse_text(OTHER_OUTLET, nb, ANN))]
    d2 = F.decide(two, ANN, nb, market_lookup=lambda c: "")
    assert d2["KOSPI200"].ok and set(d2["KOSPI200"].adds) == {"267270", "000990", "483650", "456040"}
    assert d2["KOSDAQ150"].ok and set(d2["KOSDAQ150"].adds) == {"038500", "295310", "319400"}


def test_decide_holds_when_lists_disagree_with_stated_count():
    nb = _nb()
    partial = YONHAP.replace("HD건설기계[267270]과 ", "")            # 한 종목 빠진 명단(밝힌 수 4와 불일치)
    rows = [(_meta("a"), F.parse_text(partial, nb, ANN)), (_meta("b"), F.parse_text(partial, nb, ANN))]
    d = F.decide(rows, ANN, nb, market_lookup=lambda c: "")
    assert not d["KOSPI200"].ok


def test_decide_rejects_wrong_effective_date():
    nb = _nb()
    bad = YONHAP.replace("다음달 12일부터", "다음달 19일부터")
    rows = [(_meta("a"), F.parse_text(bad, nb, ANN)), (_meta("b"), F.parse_text(bad, nb, ANN))]
    d = F.decide(rows, ANN, nb, market_lookup=lambda c: "")
    assert not d["KOSPI200"].ok and not d["KOSDAQ150"].ok


def test_decide_market_check_for_unknown_codes():
    nb = _nb()
    nb.market.pop("483650")                                           # 신규 상장 — 사전에 시장 없음
    nb.lookup = lambda c: "Y"                                         # 조회로 유가증권 확인
    rows = [(_meta("a"), F.parse_text(YONHAP, nb, ANN)), (_meta("b"), F.parse_text(OTHER_OUTLET, nb, ANN))]
    assert F.decide(rows, ANN, nb, market_lookup=lambda c: "Y")["KOSPI200"].ok
    nb2 = _nb()
    nb2.market.pop("483650")
    nb2.lookup = lambda c: ""                                         # 확인 불가 → 보류
    rows2 = [(_meta("a"), F.parse_text(YONHAP, nb2, ANN)), (_meta("b"), F.parse_text(OTHER_OUTLET, nb2, ANN))]
    assert not F.decide(rows2, ANN, nb2, market_lookup=lambda c: "")["KOSPI200"].ok
    nb3 = _nb()
    nb3.market["483650"] = "K"                                        # 시장 불일치(코스닥 종목이 코스피200 명단에) → 그 종목은 코스닥150으로 가고 수 불일치로 보류
    rows3 = [(_meta("a"), F.parse_text(YONHAP, nb3, ANN)), (_meta("b"), F.parse_text(OTHER_OUTLET, nb3, ANN))]
    assert not F.decide(rows3, ANN, nb3, market_lookup=lambda c: "")["KOSPI200"].ok


def _write_csv(path: Path, bom: bool = True) -> None:
    head = ",".join(F.FIELDS)
    body = "KOSPI200,2025-11-18,2025-12-12,000660,SK하이닉스,add,https://x\r\n"
    path.write_bytes((("﻿" if bom else "") + head + "\r\n" + body).encode("utf-8"))


def test_append_rows_idempotent_and_preserves_bom_newlines(tmp_path):
    path = tmp_path / "kr_index_changes.csv"
    _write_csv(path)
    nb = _nb()
    rows = [(_meta("a"), F.parse_text(YONHAP, nb, ANN)), (_meta("b"), F.parse_text(OTHER_OUTLET, nb, ANN))]
    dec = F.decide(rows, ANN, nb, market_lookup=lambda c: "")["KOSPI200"]
    new = F.build_rows(dec, ANN)
    assert F.append_rows(new, path) == len(new) and len(new) == 8
    assert F.append_rows(new, path) == 0                              # 같은 행 다시 안 넣음
    raw = path.read_bytes()
    assert raw.startswith(codecs.BOM_UTF8) and raw.count(codecs.BOM_UTF8) == 1
    assert b"\n" not in raw.replace(b"\r\n", b"")                     # CRLF 유지
    with path.open(encoding="utf-8-sig") as fh:
        got = list(csv.DictReader(fh))
    adds = [r for r in got if r["announce_date"] == "2026-05-22" and r["action"] == "add"]
    assert {r["ticker"] for r in adds} == {"267270", "000990", "483650", "456040"}
    assert all(r["effective_date"] == "2026-06-12" for r in adds)
    assert ("KOSPI200", "2026-06-12") in F.existing_rounds(path)
    # 특례편입 행(편입만, 편출 없음)은 정기 회차를 '이미 있음'으로 만들지 않는다
    F.append_rows([{"index": "KOSDAQ150", "announce_date": "2026-11-02", "effective_date": "2026-12-11",
                    "ticker": "038500", "name": "삼표시멘트", "action": "add", "source_url": "x"}], path)
    assert ("KOSDAQ150", "2026-12-11") not in F.existing_rounds(path)
    # 실주문 레인 로더가 그대로 읽는다
    import kr_index_inclusion_live as L
    rounds = L.load_rounds(path)
    assert {x["ticker"] for x in rounds["2026-05-22"]["adds"]} == {"267270", "000990", "483650", "456040"}


def test_round_window_and_candidate_dates():
    mk = lambda d, h: {"pub": datetime.combine(d, datetime.min.time(), F.KST) + timedelta(hours=h)}  # noqa: E731
    items = [mk(date(2026, 11, 20), 17), mk(date(2026, 11, 21), 7), mk(date(2026, 11, 21), 9),
             mk(date(2026, 11, 19), 10)]
    w = F.round_window(items, date(2026, 11, 20))
    assert len(w) == 2                                                 # 당일 17시 + 다음 날 07시(08:35 전)
    ds = F.candidate_announce_dates(items, date(2026, 11, 21))
    assert date(2026, 11, 20) in ds and all(d.weekday() < 5 for d in ds)
    assert F.candidate_announce_dates(items, date(2026, 11, 30)) == []  # 4일 넘은 기사는 후보 아님


def test_blocked_host_never_fetched():
    assert F.fetch_article("https://data.krx.co.kr/whatever") == ""


def test_scan_writes_only_validated_round(tmp_path, monkeypatch):
    path = tmp_path / "kr_index_changes.csv"
    _write_csv(path)
    monkeypatch.setattr(F, "CHANGES", path)
    monkeypatch.setattr(F, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(F, "load_namebook", lambda refresh=True, exclude_round=None: _nb())
    ann = date(2026, 11, 20)
    texts = {"https://n.news.naver.com/mnews/article/001/1": YONHAP.replace("22일", "20일").replace("다음달 12일", "다음달 11일"),
             "https://n.news.naver.com/mnews/article/015/2": OTHER_OUTLET.replace("22일", "20일").replace("6월 12일", "12월 11일"),
             "https://n.news.naver.com/mnews/article/009/3": PREDICTION}
    pub = datetime(2026, 11, 20, 17, 10, tzinfo=F.KST)
    items = [{"url": u, "orig": u, "outlet": F._outlet(u, u), "pub": pub, "title": "코스피200 정기변경 결과"}
             for u in texts]
    monkeypatch.setattr(F, "search_news", lambda sort="date", pages=2: items)
    monkeypatch.setattr(F, "fetch_article", lambda u, timeout=20.0: texts[u])
    monkeypatch.setattr(F, "lookup_market", lambda c: "")
    sent: list[str] = []
    monkeypatch.setattr(F, "_telegram", lambda m: sent.append(m))
    # process_round 기본 인자 fetch=fetch_article 는 정의 시점에 묶이므로 래핑
    orig = F.process_round
    monkeypatch.setattr(F, "process_round", lambda its, ad, nb, **kw: orig(its, ad, nb, fetch=texts.__getitem__,
                                                                          market_lookup=lambda c: "", **kw))
    F.cmd_scan(now=datetime(2026, 11, 20, 18, 0, tzinfo=F.KST))
    with path.open(encoding="utf-8-sig") as fh:
        got = [r for r in csv.DictReader(fh) if r["announce_date"] == ann.isoformat()]
    adds = {(r["index"], r["ticker"]) for r in got if r["action"] == "add"}
    assert adds == {("KOSPI200", c) for c in ("267270", "000990", "483650", "456040")} | \
        {("KOSDAQ150", c) for c in ("038500", "295310", "319400")}
    assert all(r["effective_date"] == "2026-12-11" for r in got)
    assert len([m for m in sent if m.startswith("✅")]) == 2
    # 두 번째 실행은 아무것도 더 쓰지 않는다(효력일 기준 회차 중복 방지)
    n_before = len(path.read_bytes())
    F.cmd_scan(now=datetime(2026, 11, 21, 8, 0, tzinfo=F.KST))
    assert len(path.read_bytes()) == n_before
