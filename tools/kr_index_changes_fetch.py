# -*- coding: utf-8 -*-
"""KR 지수 정기변경 명단 자동 수집 — L7(kr_index_inclusion_live) 입력 CSV 채우기 (2026-11-08, 운영자 지시
"너가 직접 크롤링 … 위험 부분은 너가 직접 데이터 읽어서 잡거나 개선해서 자동화하자 진행해").

경로: 네이버 검색 API(공식, 뉴스) → 기사 본문 → 결정론 파서 → 검증 게이트 → data/reference/kr_index_changes.csv 추가.
**KRX 사이트는 건드리지 않는다**(자동수집 약관 금지) — krx.co.kr 링크는 받지 않는다.

검증 게이트(전부 통과해야 쓴다, 하나라도 어긋나면 쓰지 않고 텔레그램 경고 → 다음 실행에서 재시도):
  G1 발표 기사: "한국거래소" + 심의/결정/밝혔 + "정기" + 반영(적용) 시점 문구. 예상·전망 기사는 탈락.
  G2 효력일: 기사에서 읽은 효력일 = 규칙(6·12월 둘째 목요일 다음 날)과 일치.
  G3 명단: 지수별 편입 종목이 모두 6자리 코드로 해소(기사 [코드] 우선, 없으면 종목명 사전)되고,
     시장 일치(KOSPI200=유가증권, KOSDAQ150=코스닥 — 알 수 있을 때), 기사가 밝힌 종목 수와 일치.
  G4 교차: 서로 다른 언론사 기사 2건 이상이 같은 지수의 같은 편입 명단을 낸다.
지수별로 판정한다(한쪽만 통과하면 그쪽만 쓴다). 쓰기는 원자적(임시 파일 → 교체), 중복 행은 넣지 않는다.

명령
  scan       발표 시즌 평일 저녁(17:30~23:30, 30분)·다음 날 08:00 예약 실행. 새 회차를 찾으면 CSV에 쓰고 텔레그램.
             --dry-run 이면 쓰지 않고 판정만 출력.
  backtest   기존 CSV의 출처 기사(2019~ 정기 회차)를 다시 읽어 파서가 정답 명단을 재현하는지 대조(읽기 전용).
  parse      URL 하나를 파싱해 출력(점검용).
상태: data/shadow/kr_index_changes_fetch_state.json (회차별 기록·경고 1회) / 로그 logs/system/kr_index_changes_fetch.log
"""
from __future__ import annotations

import argparse
import codecs
import csv
import html as _html
import io
import json
import os
import re
import sys
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CHANGES = ROOT / "data" / "reference" / "kr_index_changes.csv"
STATE = ROOT / "data" / "shadow" / "kr_index_changes_fetch_state.json"
NAME_CACHE = ROOT / "data" / "cache" / "kr_corpcode_names.json"
RESEARCH_NAMEMAP = ROOT / "data" / "analysis" / "kr_index_inclusion_20261030" / "name_map.json"
RESEARCH_MANUAL = ROOT / "data" / "analysis" / "kr_index_inclusion_20261030" / "manual_name_codes.json"
DART_LIST = ROOT / "data" / "analysis" / "kr_event_20260930" / "list"
FIELDS = ["index", "announce_date", "effective_date", "ticker", "name", "action", "source_url"]
KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
BLOCKED_HOSTS = ("krx.co.kr",)
QUERIES = ("코스피200 정기변경", "코스닥150 정기변경", "코스피200 편입 제외 한국거래소", "코스닥150 편입 편출 한국거래소")
INDEXES = ("KOSPI200", "KOSDAQ150")
MARKET_OF = {"KOSPI200": "Y", "KOSDAQ150": "K"}

# 지수 표기 → 정규 이름. 파싱 대상이 아닌 지수(KRX300 등)도 잡아야 그 구간 종목이 섞이지 않는다.
_INDEX_PATTERNS = [
    ("KOSPI200", re.compile(r"코스피\s?200|KOSPI\s?200", re.I)),
    ("KOSDAQ150", re.compile(r"코스닥\s?150|KOSDAQ\s?150", re.I)),
    ("OTHER", re.compile(r"KRX\s?300|KRX\s?100|코스피\s?(?:100|50)|코스닥\s?글로벌|KRX\s?K-?뉴딜|코스피\s?200\s?ESG|"
                         r"코리아\s?밸류업|밸류업\s?지수|KRX\s?(?:반도체|2차전지|바이오)|KTOP|코스피\s?(?:대형|중형|소형)주?\s?지수", re.I)),
]
_ADD_VERB = re.compile(r"신규\s?편입|편입|포함|추가|새로\s?(?:들어|진입)|들어오|들어간|진입|합류|입성|이름을\s?올")
_DEL_VERB = re.compile(r"편출|제외|빠지|빠진|빠졌|빠져|빠질|삭제|퇴출|나가|탈락|밀려")
_TOPIC_AFTER_VERB = re.compile(r"^\s*(?:되는|된|될|하는|한)?\s*(?:\d{1,3}\s?개\s?)?(?:종목|기업|곳|대상)?(?:들)?"
                               r"(?:은|는|으로는|로는|으로|에는)\s")
_CODE = re.compile(r"\[\s*([0-9A-Z]{6})\s*\]|\(\s*([0-9A-Z]{6})\s*\)")
_COUNT = re.compile(r"(\d{1,3})\s?개?\s?(?:종목|사|곳|기업)")
_PRED = re.compile(r"예상|전망|가능성|유력|점쳐|추정|관측|기대")


def _now() -> datetime:
    return datetime.now(KST)


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKC", name or "")
    s = re.sub(r"\(주\)|㈜|주식회사|\s+", "", s)
    return s.upper()


# ------------------------------------------------------------------ 효력일 규칙
def second_thursday(y: int, m: int) -> date:
    d = date(y, m, 1)
    d += timedelta(days=(3 - d.weekday()) % 7)
    return d + timedelta(days=7)


def expected_effective(announce: date) -> date | None:
    """정기변경 효력일 = 6·12월 선물 만기(둘째 목요일) 다음 날. 5월 발표→6월, 11월 발표→12월."""
    if announce.month in (5, 6):
        return second_thursday(announce.year, 6) + timedelta(days=1)
    if announce.month in (11, 12):
        return second_thursday(announce.year, 12) + timedelta(days=1)
    return None


# ------------------------------------------------------------------ 종목명 사전
@dataclass
class NameBook:
    by_name: dict[str, set[str]] = field(default_factory=dict)   # norm name -> codes
    market: dict[str, str] = field(default_factory=dict)          # code -> Y/K
    codes: set[str] = field(default_factory=set)
    names_sorted: list[str] = field(default_factory=list)          # 원문 대조용(길이 내림차순)
    lookup: object = None                                          # 시장 모르는 코드(신규 상장) 조회 함수 code -> Y/K/''

    def market_of(self, code: str) -> str | None:
        if code in self.market:
            return self.market[code]
        if callable(self.lookup):
            mk = self.lookup(code) or ""
            if mk in ("Y", "K"):
                self.market[code] = mk
                return mk
        return None

    def add(self, name: str, code: str, mkt: str = "") -> None:
        k = norm(name)
        if not k or len(code) != 6:
            return
        self.by_name.setdefault(k, set()).add(code)
        self.codes.add(code)
        if mkt in ("Y", "K"):
            self.market[code] = mkt

    def finalize(self) -> "NameBook":
        self.names_sorted = sorted(self.by_name, key=len, reverse=True)
        return self


def _dart_key() -> str:
    try:
        from dotenv import dotenv_values
        env = {**dotenv_values(ROOT / ".env"), **dotenv_values(ROOT / ".env.live")}
    except Exception:
        env = {}
    return str(env.get("DART_API_KEY") or os.getenv("DART_API_KEY") or "").strip()


def refresh_corpcode(max_age_h: float = 20.0) -> list[dict]:
    """OpenDART corpCode.xml(현재 상장사 이름·코드) — 하루 1회 캐시. 신규 상장 종목명을 잡으려고 매 시즌 갱신."""
    if NAME_CACHE.exists():
        age = (datetime.now().timestamp() - NAME_CACHE.stat().st_mtime) / 3600
        if age < max_age_h:
            return json.loads(NAME_CACHE.read_text(encoding="utf-8"))
    key = _dart_key()
    if not key:
        return json.loads(NAME_CACHE.read_text(encoding="utf-8")) if NAME_CACHE.exists() else []
    import requests
    try:
        raw = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": key}, timeout=60).content
        z = zipfile.ZipFile(io.BytesIO(raw))
        xml = z.read(z.namelist()[0]).decode("utf-8", "ignore")
    except Exception as exc:          # 키 문자열이 예외 메시지에 섞이지 않게 종류만 남긴다
        print(f"[idx-fetch] ⚠️ corpCode 갱신 실패({type(exc).__name__}) — 기존 캐시 사용")
        return json.loads(NAME_CACHE.read_text(encoding="utf-8")) if NAME_CACHE.exists() else []
    rows = []
    for blk in re.findall(r"<list>(.*?)</list>", xml, re.S):
        g = lambda t: (re.search(rf"<{t}>(.*?)</{t}>", blk, re.S) or [None, ""])[1].strip()  # noqa: E731
        sc = g("stock_code")
        if len(sc) == 6:
            rows.append({"name": g("corp_name"), "code": sc})
    NAME_CACHE.parent.mkdir(parents=True, exist_ok=True)
    NAME_CACHE.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def load_namebook(refresh: bool = True, exclude_round: str | None = None) -> NameBook:
    """종목명 사전 = DART 공시 이력(접수 당시 이름·시장) ∪ 연구 사전 ∪ OpenDART 현재명 ∪ 수동 해소
    ∪ 기존 명단 CSV의 (이름, 코드). exclude_round 는 백테스트용 — 그 회차의 CSV 이름은 빼서 정답이 새지 않게 한다."""
    nb = NameBook()
    if CHANGES.exists():
        with CHANGES.open(encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                if exclude_round and r.get("announce_date") == exclude_round:
                    continue
                nb.add(r.get("name", ""), (r.get("ticker") or "").strip().zfill(6))
    if DART_LIST.exists():                                   # 접수 당시 회사명·코드·시장(Y/K)
        for fn in sorted(DART_LIST.glob("*.jsonl")):
            with fn.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if r.get("corp_cls") in ("Y", "K"):
                        nb.add(r.get("corp_name", ""), (r.get("stock_code") or "").strip(), r.get("corp_cls"))
    if RESEARCH_NAMEMAP.exists():
        for k, codes in json.loads(RESEARCH_NAMEMAP.read_text(encoding="utf-8")).items():
            for c in codes:
                nb.add(k, c)
    for r in (refresh_corpcode() if refresh else (json.loads(NAME_CACHE.read_text(encoding="utf-8"))
                                                   if NAME_CACHE.exists() else [])):
        nb.add(r["name"], r["code"])
    if RESEARCH_MANUAL.exists():
        for k, c in json.loads(RESEARCH_MANUAL.read_text(encoding="utf-8")).items():
            if not k.startswith("_") and "@" not in k:
                nb.add(k, c)
    return nb.finalize()


# ------------------------------------------------------------------ 본문 추출
def fetch_article(url: str, timeout: float = 20.0) -> str:
    if any(h in url for h in BLOCKED_HOSTS):
        return ""
    import requests
    from bs4 import BeautifulSoup
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    soup = BeautifulSoup(r.text, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    for sel in ("#dic_area", "#newsct_article", "#articleBody", "#article-view-content-div", "#article_content",
                "div.article_body", "div.news_body", "div#textBody", "div.view_con", "article"):
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 200:
            return el.get_text("\n", strip=True)
    return soup.get_text("\n", strip=True)


def body_sentences(text: str) -> list[str]:
    """본문 문장만 — 메뉴·관련기사 제목(짧고 '다.'로 안 끝남)이 명단에 섞이지 않게 거른다."""
    t = _html.unescape(text or "").replace(" ", " ")
    t = re.sub(r"\s+", " ", t)                        # 링크 태그로 쪼개진 줄을 한 흐름으로
    t = re.sub(r"\(\s?[\d,]+원[^)]{0,30}\)", "", t)    # 시세 주석 "(116,400원 ▼11,900 -9.28%)"
    t = re.sub(r"\s+([,.])", r"\1", t)
    parts = re.split(r"(?<=다\.)\s+|(?<=다)\s+(?=[\[(△▲▶■◆●]|\S+\s?=)", t)
    return [s.strip() for s in parts if re.search(r"다\.?[\"”']?$", s.strip()) and len(s.strip()) < 2500]


# ------------------------------------------------------------------ 파서
@dataclass
class Parsed:
    adds: dict[str, dict[str, str]] = field(default_factory=dict)    # index -> {code: name}
    dels: dict[str, dict[str, str]] = field(default_factory=dict)
    counts: dict[tuple[str, str], int] = field(default_factory=dict)  # (index, add/del) -> stated count
    unmapped: list[str] = field(default_factory=list)
    market_unknown: set[str] = field(default_factory=set)
    effective: date | None = None
    is_announcement: bool = False
    notes: list[str] = field(default_factory=list)


def _index_spans(s: str) -> list[tuple[int, str]]:
    spans = []
    for name, pat in _INDEX_PATTERNS:
        for m in pat.finditer(s):
            spans.append((m.start(), name, m.end()))
    spans.sort()
    # 'OTHER' 패턴(코스피200 ESG 등)이 KOSPI200과 같은 자리면 OTHER 우선
    out: list[tuple[int, str]] = []
    for st, nm, en in spans:
        if out and out[-1][0] == st:
            if nm == "OTHER":
                out[-1] = (st, nm)
            continue
        out.append((st, nm))
    return out


_BOUND_L = r"(?:(?<=^)|(?<=[\s,·、\"'“‘(\[/]))"


def _find_names(s: str, nb: NameBook) -> list[tuple[int, int, str, str]]:
    """문장 안의 종목: (시작, 끝, 표기명, 코드). [코드] 표기 우선, 없으면 사전 최장일치."""
    found: list[tuple[int, int, str, str]] = []
    taken = [False] * len(s)
    # 1) 이름[코드] / 이름(코드)
    for m in re.finditer(r"([0-9A-Za-z가-힣&\.\-·]+?)\s?[\[\(]\s?([0-9A-Z]{6})\s?[\]\)]", s):
        name, code = m.group(1).strip("·.- "), m.group(2)
        if not re.search(r"\d", code) or not name:
            continue
        found.append((m.start(1), m.end(), name, code))
        for i in range(m.start(1), m.end()):
            taken[i] = True
    # 2) 사전 대조 — 원문 토큰(조사 제거)이 정규화된 종목명과 같을 때만
    # 원문 토큰 단위로 대조(공백·구두점 분리 후 조사 제거) — 정규화된 이름과 같으면 종목
    for m in re.finditer(r"[0-9A-Za-z가-힣&\.\-]+", s):
        st, en = m.start(), m.end()
        if any(taken[st:en]):
            continue
        tok = m.group(0)
        cands = [tok]
        for suf in ("으로는", "에서는", "이었다", "이다", "였다", "에는", "으로", "에서", "까지", "과", "와", "은", "는",
                    "이", "가", "을", "를", "도", "의", "등", "및", "로", "만", "다"):
            if tok.endswith(suf) and len(tok) > len(suf) + 1:
                cands.append(tok[: -len(suf)])
        for c in cands:
            codes = nb.by_name.get(norm(c))
            if codes:
                found.append((st, st + len(c), c, ",".join(sorted(codes))))
                for i in range(st, en):
                    taken[i] = True
                break
    return sorted(found)


def parse_text(text: str, nb: NameBook, announce_hint: date | None = None) -> Parsed:
    p = Parsed()
    sents = body_sentences(text)
    full = " ".join(sents)
    # G1 발표 기사 판정
    krx = "한국거래소" in full or "거래소는" in full
    act = re.search(r"심의|결정|확정|밝혔|발표|공지|공시", full)
    regular = "정기" in full
    p.effective = _effective_from_text(full, announce_hint)
    p.is_announcement = bool(krx and act and regular and p.effective)
    if not p.is_announcement:
        p.notes.append(f"발표기사 아님(krx={krx} act={bool(act)} 정기={regular} 효력일={p.effective})")
    cur = None
    for s in sents:
        spans = _index_spans(s)
        names = _find_names(s, nb)
        if names and _PRED.search(s):
            p.notes.append(f"예상·전망 문장 제외: {s[:60]}")      # 증권사 편입 예상 명단이 섞이지 않게
            if spans:
                cur = spans[-1][1]
            continue
        if names and _special_sentence(s, p.effective, announce_hint):
            # 분할 재상장·합병 등 특례 편출입(정기변경과 다른 날 반영) — 정기 명단에 섞지 않는다
            p.notes.append(f"특례 문장 제외: {s[:60]}")
            if spans:
                cur = spans[-1][1]
            continue
        if not names:
            if spans:
                # 여러 지수를 늘어놓는 문장("코스피200과 코스닥150, KRX300 지수를 심의")은 구간을 정하지 않는다
                cur = spans[-1][1] if len({x[1] for x in spans}) == 1 else None
            # 종목 수만 밝힌 문장도 집계
            _collect_counts(s, spans, cur, p)
            continue
        # 문장 안 이벤트: 지수 표기, 동사, 종목
        events: list[tuple[int, str, object]] = []
        for st, nm in spans:
            events.append((st, "idx", nm))
        for m in _ADD_VERB.finditer(s):
            events.append((m.start(), "verb", ("add", m.end())))
        for m in _DEL_VERB.finditer(s):
            events.append((m.start(), "verb", ("del", m.end())))
        for st, en, nm, code in names:
            events.append((st, "name", (nm, code, en)))
        events.sort(key=lambda e: (e[0], {"idx": 0, "verb": 1, "name": 2}[e[1]]))
        # 지수가 문장에 하나뿐이고 종목보다 뒤에 나오면 문장 전체에 적용
        sent_idx_only = spans[0][1] if len({x[1] for x in spans}) == 1 else None
        if sent_idx_only and spans[0][0] > names[0][0]:
            cur = sent_idx_only
        queue: list[tuple[str, str, str]] = []    # (name, code, idx)
        topic: str | None = None
        for pos, kind, val in events:
            if kind == "idx":
                cur = val
                continue
            if kind == "verb":
                act_, vend = val
                rest = s[vend:vend + 20]
                if _TOPIC_AFTER_VERB.match(rest) or re.match(r"^\s?(?:되는|된)\s?(?:종목|기업)", rest):
                    for q in queue:
                        _assign(p, q, act_, nb)
                    queue = []
                    topic = act_
                else:
                    for q in queue:
                        _assign(p, q, act_, nb)
                    queue = []
                    topic = None
                continue
            nm, code, _ = val
            item = (nm, code, cur or "")
            if topic:
                _assign(p, item, topic, nb)
            else:
                queue.append(item)
        if queue:
            if topic:
                for q in queue:
                    _assign(p, q, topic, nb)
            else:
                p.notes.append(f"동사 없이 끝난 종목 {[q[0] for q in queue]}")
        _collect_counts(s, spans, cur, p)
    return p


_SPECIAL = re.compile(r"특례|재상장|상장\s?(?:다음|직후|이후)|잔류|합병|인적\s?분할|물적\s?분할|분할\s?(?:신설|상장)|"
                      r"상장\s?폐지|거래\s?정지|관리\s?종목|수시\s?변경")
_DAY = re.compile(r"(?<![\d.])(\d{1,2})일")


def _special_sentence(s: str, effective: date | None, announce: date | None = None) -> bool:
    """특례 문장: 특례 표식이 있거나, 발표일·효력일이 아닌 날짜(N일)를 말하는 문장('오는 25일 편입' 같은 수시 편입)."""
    if _SPECIAL.search(s):
        return True
    ok_days = {d.day for d in (effective, announce) if d}
    if announce:
        ok_days.add((announce - timedelta(days=1)).day)   # '전날(○일) 주가지수운영위원회'
    if effective:
        for m in _DAY.finditer(s):
            if int(m.group(1)) not in ok_days and not re.match(r"\d{1,2}일\s?(?:간|동안|평균|기준)", s[m.start():]):
                return True
    return False


def _assign(p: Parsed, item: tuple[str, str, str], act: str, nb: NameBook | None = None) -> None:
    """지수 구간(KRX300 등 다른 지수 구간은 버림) 안의 종목만. 지수는 상장시장이 정한다 —
    KOSPI200은 유가증권 종목만, KOSDAQ150은 코스닥 종목만 담으므로 시장을 알면 문장 속 지수 표기보다 우선한다."""
    nm, code, idx = item
    if idx == "OTHER":
        return
    if idx not in INDEXES:                     # 지수 구간을 모름 — 시장으로만 정한다(모르면 버림)
        mk0 = nb.market_of(code) if nb and "," not in code else None
        if mk0 not in ("Y", "K"):
            return
        idx = "KOSPI200" if mk0 == "Y" else "KOSDAQ150"
    if "," in code:
        codes = code.split(",")
        want = MARKET_OF[idx]
        same = [c for c in codes if nb and nb.market.get(c) == want]
        if len(same) == 1:
            code = same[0]
        else:
            p.unmapped.append(f"{idx} {act} {nm}: 동명 코드 {code}")
            return
    mkt = nb.market_of(code) if nb else None
    if mkt == "Y":
        idx = "KOSPI200"
    elif mkt == "K":
        idx = "KOSDAQ150"
    else:
        p.market_unknown.add(code)
    book = p.adds if act == "add" else p.dels
    book.setdefault(idx, {})[code] = nm


def _collect_counts(s: str, spans, cur, p: Parsed) -> None:
    for m in _COUNT.finditer(s):
        n = int(m.group(1))
        tail = s[m.end(): m.end() + 25]
        head = s[max(0, m.start() - 14): m.start()]
        act = None
        hv = re.search(r"(편입|포함|추가|편출|제외|빠지|빠진)\s?(?:되는|된|될|하는)\s?$", head)
        if _ADD_VERB.search(tail) and not _DEL_VERB.search(tail[: (_ADD_VERB.search(tail).start())]):
            act = "add"
        elif _DEL_VERB.search(tail):
            act = "del"
        if hv and re.match(r"^\s?(?:개\s?)?(?:종목|사|곳|기업)?(?:은|는|으로는)", s[m.end():m.end() + 8] or ""):
            act = "add" if _ADD_VERB.match(hv.group(1)) else "del"
        # 지수: 수 바로 뒤(20자 안, "11개 종목 코스닥150 편입") 표기 우선, 없으면 앞쪽 마지막 표기, 없으면 문맥
        idx = cur
        for st, nm in spans:
            if st <= m.start():
                idx = nm
        vpos = (_ADD_VERB.search(tail) or _DEL_VERB.search(tail))
        vabs = m.end() + vpos.start() if vpos else m.end()
        after = [nm for st, nm in spans if m.end() <= st < vabs]      # 수와 동사 사이의 지수 표기만
        if after:
            idx = after[0]
        if act and idx in INDEXES and 0 < n <= 60:          # '200개 종목으로 산출' 같은 지수 정의 문구 제외
            p.counts.setdefault((idx, act), n)
    if re.search(r"같은\s?수", s) and _DEL_VERB.search(s):
        idx = cur
        for st, nm in spans:
            idx = nm
        if idx in INDEXES and (idx, "add") in p.counts:
            p.counts.setdefault((idx, "del"), p.counts[(idx, "add")])


_MONTHDAY = re.compile(r"(?:(\d{4})년\s?)?(\d{1,2})월\s?(\d{1,2})일")
_NEXTMONTH = re.compile(r"(?:다음\s?달|내달)\s?(\d{1,2})일")
_COMING = re.compile(r"오는\s?(\d{1,2})일")
_EFF_CTX = re.compile(r"([^.]{0,40}?)(?:부터|에)\s?(?:반영|적용|변경|교체|시행|효력)|"
                      r"([^.]{0,40}?)(?:효력\s?발생|정기\s?변경일|변경\s?기준일)")


def _effective_from_text(full: str, hint: date | None) -> date | None:
    """후보 효력일 중 규칙 효력일이 있으면 그것, 없으면 첫 후보."""
    cands = _effective_candidates(full, hint)
    exp = expected_effective(hint or _now().date())
    if exp and exp in cands:
        return exp
    return cands[0] if cands else None


def _effective_candidates(full: str, hint: date | None) -> list[date]:
    """반영·적용 시점 문구에서 효력일. '오는 N일'은 같은 달(이미 지난 날이면 다음 달) — 11월 기사 '오는 25일
    편입'(분할 재상장 특례)을 12월로 읽지 않게 한다. 규칙 효력일과 맞는지는 호출 쪽(G2)이 본다."""
    base = hint or _now().date()
    out: list[date] = []

    def _mk(y: int, mo: int, dd: int) -> date | None:
        try:
            return date(y, mo, dd)
        except ValueError:
            return None

    for m in _EFF_CTX.finditer(full):
        seg = m.group(1) if m.group(1) is not None else (m.group(2) or "")
        mm = list(_MONTHDAY.finditer(seg))
        if mm:
            y, mo, dd = mm[-1].groups()
            yy = int(y) if y else (base.year + (1 if int(mo) < base.month - 6 else 0))
            d = _mk(yy, int(mo), int(dd))
        elif _NEXTMONTH.search(seg):
            mo = base.month % 12 + 1
            d = _mk(base.year + (1 if base.month == 12 else 0), mo, int(_NEXTMONTH.search(seg).group(1)))
        elif _COMING.search(seg):
            dd = int(_COMING.search(seg).group(1))
            if dd >= base.day:
                d = _mk(base.year, base.month, dd)
            else:
                d = _mk(base.year + (1 if base.month == 12 else 0), base.month % 12 + 1, dd)
        else:
            d = None
        if d and d > base and d not in out:
            out.append(d)
    return out


# ------------------------------------------------------------------ 검색
def _naver_keys() -> tuple[str, str]:
    try:
        from dotenv import dotenv_values
        env = {**dotenv_values(ROOT / ".env"), **dotenv_values(ROOT / ".env.live")}
    except Exception:
        env = {}
    g = lambda k: str(env.get(k) or os.getenv(k) or "").strip()  # noqa: E731
    return (g("NAVER_SEARCH_CLIENT_ID") or g("NAVER_CLIENT_ID"), g("NAVER_SEARCH_CLIENT_SECRET") or g("NAVER_CLIENT_SECRET"))


def search_news(sort: str = "date", pages: int = 2, queries: tuple[str, ...] = QUERIES) -> list[dict]:
    """네이버 뉴스 검색 API(공식). [{url, orig, title, pub(KST), outlet}] — 링크 중복 제거."""
    import requests
    cid, sec = _naver_keys()
    if not cid or not sec:
        raise RuntimeError("네이버 검색 API 키 없음(NAVER_CLIENT_ID/SECRET)")
    out: dict[str, dict] = {}
    for q in queries:
        for pg in range(pages):
            r = requests.get("https://openapi.naver.com/v1/search/news.json",
                             headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec},
                             params={"query": q, "display": 100, "start": 1 + 100 * pg, "sort": sort}, timeout=15)
            r.raise_for_status()
            items = r.json().get("items") or []
            for it in items:
                link = it.get("link") or it.get("originallink") or ""
                orig = it.get("originallink") or link
                if not link or any(h in link + orig for h in BLOCKED_HOSTS):
                    continue
                try:
                    pub = parsedate_to_datetime(it.get("pubDate", "")).astimezone(KST)
                except Exception:
                    continue
                out.setdefault(link, {"url": link, "orig": orig, "pub": pub, "outlet": _outlet(link, orig),
                                      "title": re.sub(r"<[^>]+>", "", _html.unescape(it.get("title", "")))})
            if len(items) < 100:
                break
    return list(out.values())


def _outlet(link: str, orig: str) -> str:
    """언론사 식별 — 네이버 링크면 언론사 번호(oid), 아니면 원문 도메인. 같은 언론사 두 링크는 1곳으로 센다."""
    m = re.search(r"n\.news\.naver\.com/(?:mnews/)?article/(\d{3})/", link)
    if m:
        return f"oid{m.group(1)}"
    m = re.search(r"https?://(?:www\.|m\.)?([^/]+)", orig or link)
    return m.group(1) if m else link


def round_window(items: list[dict], announce: date) -> list[dict]:
    """발표일 KST 12:00 ~ 다음 날 08:35(매수 08:41 직전) 기사만."""
    st = datetime.combine(announce, datetime.min.time(), KST) + timedelta(hours=12)
    en = datetime.combine(announce + timedelta(days=1), datetime.min.time(), KST) + timedelta(hours=8, minutes=35)
    return [x for x in items if st <= x["pub"] <= en]


# ------------------------------------------------------------------ 판정
@dataclass
class IndexDecision:
    index: str
    ok: bool
    adds: dict[str, str] = field(default_factory=dict)
    dels: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    reason: str = ""


def decide(parsed: list[tuple[dict, Parsed]], announce: date, nb: NameBook,
           market_lookup=None) -> dict[str, IndexDecision]:
    """G1~G4. parsed = [(기사 메타, 파싱 결과)]. 지수별로 독립 판정한다."""
    market_lookup = market_lookup or lookup_market
    exp = expected_effective(announce)
    good = [(m, p) for m, p in parsed if p.is_announcement and exp and p.effective == exp]
    out: dict[str, IndexDecision] = {}
    for idx in INDEXES:
        groups: dict[frozenset, dict] = {}
        stated: list[int] = []
        for m, p in good:
            adds = p.adds.get(idx) or {}
            c = p.counts.get((idx, "add"))
            if c:
                stated.append(c)
            if not adds:
                continue
            g = groups.setdefault(frozenset(adds), {"outlets": set(), "count_match": False, "names": {},
                                                    "urls": [], "dels": []})
            g["outlets"].add(m["outlet"])
            g["urls"].append(m.get("orig") or m["url"])
            g["dels"].append(frozenset(p.dels.get(idx) or {}))
            g["names"].update(adds)
            if c and c == len(adds):
                g["count_match"] = True
        mode_cnt = max(sorted(set(stated)), key=stated.count) if stated else None   # 기사들이 밝힌 편입 수의 최빈값
        # 후보 = 언론사 2곳 이상이 같은 명단 + 그중 1건 이상은 스스로 밝힌 수와 명단 크기가 같음.
        # 다른 수를 밝힌 기사(오타·파싱 누락)는 소수(≤ max(1, 밝힌 기사의 1/3))일 때만 허용 — 엇갈리면 보류.
        cands = []
        for k, g in groups.items():
            if len(g["outlets"]) < 2 or not g["count_match"]:
                continue
            dissent = [c for c in stated if c != len(k)]
            if len(dissent) <= max(1, len(stated) // 3) and stated.count(len(k)) >= len(dissent):
                cands.append((k, g))
        if not cands and not stated:
            # 어느 기사도 수를 밝히지 않은 회차(편입 1~2종목일 때 흔함): 언론사 5곳 이상 + 명단 있는 기사의 2/3 이상이
            # 같은 명단일 때만
            n_with = sum(len(g["outlets"]) for g in groups.values())
            for k, g in groups.items():
                if len(g["outlets"]) >= 5 and len(g["outlets"]) * 3 >= n_with * 2:
                    cands.append((k, g))
        if len(cands) != 1:
            why = "후보 없음" if not cands else f"서로 다른 명단 {len(cands)}개"
            seen = sorted(((len(g["outlets"]), len(k), g["count_match"]) for k, g in groups.items()), reverse=True)[:4]
            out[idx] = IndexDecision(idx, False, reason=f"{why} — 발표기사 {len(good)}건, 명단(언론사수,크기,수일치) "
                                                         f"{seen}, 밝힌 수 {sorted(set(stated))}")
            continue
        k, g = cands[0]
        # G3 시장: 파서가 시장으로 지수를 정하므로 사전에 시장이 없는 종목(신규 상장)만 따로 확인한다
        bad = []
        for c in sorted(k):
            if c in nb.market:
                continue
            mk = market_lookup(c)
            if mk != MARKET_OF[idx]:
                bad.append(f"{c}:{mk or '확인불가'}")
        if bad:
            out[idx] = IndexDecision(idx, False, reason=f"시장 불일치/확인 불가 {bad}")
            continue
        dels_all = [d for d in g["dels"] if d]
        dmode = max(sorted(set(dels_all), key=sorted), key=dels_all.count) if dels_all else frozenset()
        dnames: dict[str, str] = {}
        for _m, p in good:
            if frozenset(p.dels.get(idx) or {}) == dmode:
                dnames.update(p.dels.get(idx) or {})
        dels_ok = bool(dmode) and dels_all.count(dmode) >= 2
        out[idx] = IndexDecision(idx, True, adds={c: g["names"][c] for c in sorted(k)},
                                 dels=({c: dnames[c] for c in sorted(dmode)} if dels_ok else {}),
                                 sources=sorted(set(g["urls"]))[:3],
                                 reason=(f"언론사 {len(g['outlets'])}곳 같은 명단·밝힌 수 {mode_cnt}" if stated
                                         else f"언론사 {len(g['outlets'])}곳 같은 명단(수 언급 없음)"))
    return out


def looks_like_announcement(parsed: list[tuple[dict, Parsed]], announce: date) -> bool:
    """보류 경고를 보낼 만큼 '오늘 발표가 났다'는 증거가 있나 — 효력일이 규칙과 맞는 발표형 기사가 언론사 3곳 이상이고,
    그중 1건 이상은 스스로 밝힌 수와 명단 크기가 같다. 예상·전망 기사만 있는 날 경고가 나가지 않게 한다."""
    exp = expected_effective(announce)
    good = [(m, p) for m, p in parsed if p.is_announcement and exp and p.effective == exp]
    if len({m["outlet"] for m, _ in good}) < 3:
        return False
    return any(p.counts.get((i, "add")) == len(p.adds.get(i) or {}) and p.adds.get(i)
               for _, p in good for i in INDEXES)


_MARKET_CACHE: dict[str, str] = {}


def lookup_market(code: str) -> str:
    """사전에 시장 정보가 없는 종목(신규 상장)만 네이버 모바일 종목 API로 확인. 'Y'/'K'/''."""
    if code in _MARKET_CACHE:
        return _MARKET_CACHE[code]
    mk = ""
    try:
        import requests
        r = requests.get(f"https://m.stock.naver.com/api/stock/{code}/basic", headers={"User-Agent": UA}, timeout=10)
        if r.ok:
            t = ((r.json() or {}).get("stockExchangeType") or {}).get("code")
            mk = {"KS": "Y", "KQ": "K"}.get(str(t), "")
    except Exception:
        mk = ""
    _MARKET_CACHE[code] = mk
    return mk


# ------------------------------------------------------------------ CSV 쓰기
def existing_rounds(path: Path | None = None) -> set[tuple[str, str]]:
    """이미 들어간 정기 회차 = (지수, 효력일). 발표일이 아니라 효력일로 본다 — 발표 며칠 뒤 후속 기사가 같은 회차를
    다른 발표일로 다시 쓰지 않게(그러면 매수일이 밀린다). 정기 회차는 편출(delete) 행이 있는 것으로 가린다 —
    특례편입 행(편입 1건, 편출 없음)이 같은 효력일에 먼저 들어가 있어도 정기 명단 수집을 막지 않게."""
    path = path or CHANGES
    if not path.exists():
        return set()
    with path.open(encoding="utf-8-sig") as fh:
        return {((r.get("index") or "").strip(), (r.get("effective_date") or "").strip()) for r in csv.DictReader(fh)
                if (r.get("action") or "").strip() == "delete"}


def build_rows(dec: IndexDecision, announce: date) -> list[dict]:
    eff = expected_effective(announce).isoformat()
    src = " | ".join(dec.sources)
    base = {"index": dec.index, "announce_date": announce.isoformat(), "effective_date": eff, "source_url": src}
    return ([{**base, "ticker": c, "name": n, "action": "add"} for c, n in dec.adds.items()]
            + [{**base, "ticker": c, "name": n, "action": "delete"} for c, n in dec.dels.items()])


def append_rows(rows: list[dict], path: Path | None = None) -> int:
    """원자적 추가 — 기존 내용 + 새 행을 임시 파일에 쓰고 교체(08:41 매수 작업이 반쯤 쓴 파일을 읽지 않게).
    인코딩·BOM·줄바꿈은 기존 파일을 따른다. 같은 (지수, 발표일, 코드, 구분) 행은 넣지 않는다."""
    path = path or CHANGES
    raw = path.read_bytes() if path.exists() else b""
    bom = raw.startswith(codecs.BOM_UTF8)
    text = raw.decode("utf-8-sig") if raw else ""
    nl = "\r\n" if "\r\n" in text else "\n"
    have = set()
    if text:
        for r in csv.DictReader(io.StringIO(text)):
            have.add((r.get("index"), r.get("announce_date"), (r.get("ticker") or "").zfill(6), r.get("action")))
    new = [r for r in rows if (r["index"], r["announce_date"], r["ticker"], r["action"]) not in have]
    if not new:
        return 0
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator=nl)
    if not text:
        w.writeheader()
    for r in new:
        w.writerow({k: r.get(k, "") for k in FIELDS})
    if text and not text.endswith(("\n", "\r")):
        text += nl
    data = (("﻿" if bom else "") + text + buf.getvalue()).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".kr_index_changes.", suffix=".tmp")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return len(new)


# ------------------------------------------------------------------ 상태·알림
def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def _telegram(msg: str) -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(str(ROOT / ".env.live"), override=False)
        import telegram_reporter as tg
        tg.send(msg, parse_mode=None, critical=True)
    except Exception as exc:  # noqa: BLE001 — 알림 실패가 수집을 막지 않는다
        print(f"[idx-fetch] 텔레그램 실패: {type(exc).__name__}")


def in_season(d: date) -> bool:
    """정기변경 발표 시즌(5·11월 10일~월말) — 과거 발표일 5-18~5-27 / 11-16~11-27."""
    return d.month in (5, 11) and d.day >= 10


# ------------------------------------------------------------------ 회차 처리
def process_round(items: list[dict], announce: date, nb: NameBook, *, fetch=fetch_article, max_articles: int = 25,
                  market_lookup=None) -> tuple[dict[str, IndexDecision], list[tuple[dict, Parsed]]]:
    win = sorted(round_window(items, announce), key=lambda x: x["pub"])
    parsed: list[tuple[dict, Parsed]] = []
    for it in win:
        if len(parsed) >= max_articles:
            break
        title = it.get("title", "")
        if not re.search(r"코스피\s?200|코스닥\s?150|정기\s?변경", title) or _PRED.search(title):
            continue                                      # 지수 없는 제목·'예상·전망' 제목은 읽지 않는다
        try:
            text = fetch(it["url"])
        except Exception as exc:
            print(f"[idx-fetch]   본문 실패 {it['url'][:70]} ({type(exc).__name__})")
            continue
        parsed.append((it, parse_text(text, nb, announce)))
    return decide(parsed, announce, nb, market_lookup=market_lookup), parsed


def candidate_announce_dates(items: list[dict], today: date) -> list[date]:
    """발표일 후보 — 최근 4일 안의 시즌 평일. 오전 기사는 전날 발표분일 수 있어 전날도 넣는다."""
    ds = set()
    for x in items:
        d = x["pub"].date()
        ds.add(d)
        if x["pub"].hour < 12:
            ds.add(d - timedelta(days=1))
    return sorted(d for d in ds if 0 <= (today - d).days <= 4 and d.weekday() < 5 and in_season(d))


def cmd_scan(dry_run: bool = False, now: datetime | None = None) -> int:
    now = now or _now()
    today = now.date()
    if not in_season(today) and not (today.month in (5, 11) or today.month in (6, 12) and today.day <= 2):
        print(f"[idx-fetch] {today} 발표 시즌 아님 — 종료")
        return 0
    st = _load_state()
    nb = load_namebook(refresh=True)
    nb.lookup = lookup_market
    items = search_news(sort="date", pages=2)
    have = existing_rounds() | {tuple(x) for x in st.get("_written_rounds", [])}
    wrote = 0
    for ad in candidate_announce_dates(items, today):
        key = ad.isoformat()
        eff = expected_effective(ad)
        if not eff:
            continue
        todo = [i for i in INDEXES if (i, eff.isoformat()) not in have]
        if not todo:
            continue
        decs, parsed = process_round(items, ad, nb)
        announced = looks_like_announcement(parsed, ad)
        if not announced and not any(d.ok for d in decs.values()):
            continue                                      # 그날 발표 증거 없음(예상 기사뿐) — 조용히 넘어간다
        rs = st.setdefault(key, {})
        for idx in todo:
            d = decs[idx]
            line = f"[idx-fetch] {key} {idx}: {'통과' if d.ok else '보류'} — {d.reason}"
            if d.ok:
                line += " | 편입 " + ", ".join(f"{n}({c})" for c, n in d.adds.items())
            print(line)
            if dry_run:
                continue
            if d.ok:
                n = append_rows(build_rows(d, ad))
                wrote += n
                have.add((idx, eff.isoformat()))
                st.setdefault("_written_rounds", []).append([idx, eff.isoformat()])
                rs[idx] = {"written": n, "at": now.isoformat(timespec="seconds"), "adds": d.adds, "sources": d.sources}
                _telegram(f"✅ L7 지수 정기변경 명단 자동 입력 — {idx} 발표 {key} · 효력 {expected_effective(ad)}\n"
                          f"편입 {len(d.adds)}종목: " + ", ".join(f"{nm}({c})" for c, nm in d.adds.items())
                          + f"\n제외 {len(d.dels)}종목 · 근거: {d.reason}\n다음 거래일 08:41 장전 매수 예정")
            else:
                slot = "morning" if now.hour < 12 else "evening"
                warn_key = f"warn_{idx}_{today.isoformat()}_{slot}"
                if not rs.get(warn_key):                   # 경고는 회차·지수당 저녁 1회 + 아침 1회
                    rs[warn_key] = now.isoformat(timespec="seconds")
                    _telegram(f"⚠️ L7 명단 자동 입력 보류 — {idx} 발표 {key}\n사유: {d.reason}\n"
                              f"다음 실행에서 재시도한다. 08:41 매수 전까지 통과 못 하면 이번 회차는 매수 없음 — "
                              f"운영자가 data/reference/kr_index_changes.csv 에 직접 넣으면 매수된다.")
    if not dry_run:
        _save_state(st)
    print(f"[idx-fetch] 완료 — 새 행 {wrote}")
    return 0


# ------------------------------------------------------------------ 백테스트(읽기 전용)
def truth_rounds(path: Path | None = None) -> dict[str, dict]:
    path = path or CHANGES
    out: dict[str, dict] = {}
    with path.open(encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            g = out.setdefault(r["announce_date"], {"eff": set()})
            g["eff"].add(r["effective_date"])
            g.setdefault((r["index"], r["action"]), set()).add(r["ticker"])
    return out


def cmd_backtest(since: str = "2019-01-01", pages: int = 10, market_lookup=None) -> int:
    """정기 회차마다 실전과 같은 경로(검색 → 발표 창 → 본문 → 파싱 → 판정)를 돌려 정답 CSV와 대조. 쓰기 없음.
    검색은 sim 정렬(과거 기사), 본문은 data/cache/kr_index_fetch_bt 캐시. 판정: exact / hold(보류) / wrong(틀린 명단 통과).
    실전 투입 조건 = wrong 0."""
    import hashlib
    truth = truth_rounds()
    cache = ROOT / "data" / "cache" / "kr_index_fetch_bt"
    cache.mkdir(parents=True, exist_ok=True)
    items = search_news(sort="sim", pages=pages)

    def cached_fetch(url: str) -> str:
        f = cache / (hashlib.md5(url.encode()).hexdigest() + ".txt")
        if f.exists():
            return f.read_text(encoding="utf-8")
        t = fetch_article(url)
        f.write_text(t, encoding="utf-8")
        return t

    tally = {"exact": 0, "hold": 0, "wrong": 0}
    for a in sorted(truth):
        if a < since:
            continue
        ad = date.fromisoformat(a)
        exp = expected_effective(ad)
        if not exp or truth[a]["eff"] != {exp.isoformat()}:
            continue                                      # 수시(특례) 변경은 대상 아님
        nb = load_namebook(refresh=False, exclude_round=a)
        nb.lookup = market_lookup or lookup_market
        decs, parsed = process_round(items, ad, nb, fetch=cached_fetch, max_articles=30, market_lookup=market_lookup)
        n_ann = sum(1 for _, p in parsed if p.is_announcement)
        for idx in INDEXES:
            t_add = truth[a].get((idx, "add"), set())
            if not t_add:
                continue
            d = decs[idx]
            v = "hold" if not d.ok else ("exact" if set(d.adds) == t_add else "wrong")
            tally[v] += 1
            tail = (f" | 초과 {sorted(set(d.adds) - t_add)} 누락 {sorted(t_add - set(d.adds))}" if v == "wrong" else "")
            print(f"{a} {idx:9s} {v:5s} 기사 {len(parsed)}·발표 {n_ann} | {d.reason[:160]}{tail}")
    print(f"[backtest] {tally}")
    return 0 if tally["wrong"] == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="KR 지수 정기변경 명단 자동 수집(L7 입력)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s1 = sub.add_parser("scan")
    s1.add_argument("--dry-run", action="store_true")
    s2 = sub.add_parser("backtest")
    s2.add_argument("--since", default="2019-01-01")
    s2.add_argument("--pages", type=int, default=10)
    s3 = sub.add_parser("parse")
    s3.add_argument("url")
    s3.add_argument("--announce", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "scan":
        return cmd_scan(dry_run=a.dry_run)
    if a.cmd == "backtest":
        return cmd_backtest(since=a.since, pages=a.pages)
    nb = load_namebook(refresh=False)
    p = parse_text(fetch_article(a.url), nb, date.fromisoformat(a.announce))
    print(json.dumps({"is_announcement": p.is_announcement, "effective": str(p.effective), "adds": p.adds,
                      "dels": p.dels, "counts": {f"{k[0]}:{k[1]}": v for k, v in p.counts.items()},
                      "unmapped": p.unmapped, "notes": p.notes[:5]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
