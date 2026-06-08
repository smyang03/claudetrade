"""
kr_news_collector.py - 국내 뉴스/공시 수집

소스:
  1. DART 공시 API    (무료, dart.fss.or.kr)
  2. BigKinds API     (무료 회원가입, bigkinds.or.kr)
  3. 네이버 금융 뉴스  (크롤링)

수집 데이터:
  - 날짜별 종목 관련 뉴스 헤드라인 + 본문 요약
  - 공시 (실적, 계약, 인수합병 등)
  - 저장: data/news/kr/YYYYMMDD.json

사전 준비:
  pip install requests beautifulsoup4 python-dotenv

.env:
  DART_API_KEY=...     ← opendart.fss.or.kr 무료 발급
  BIGKINDS_KEY=...     ← bigkinds.or.kr 무료 발급 (선택)
"""

from __future__ import annotations

import os
import json
import time
import requests
import html
import re
from pathlib import Path
from datetime import datetime, date, timedelta
from email.utils import parsedate_to_datetime
from bs4 import BeautifulSoup
from dotenv import dotenv_values, load_dotenv
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import get_collector_logger, log_retry, log_call, ProgressLogger
from bot.session_date import KST

load_dotenv()

_LIVE_ENV_PATH = Path(__file__).parent.parent / ".env.live"
if _LIVE_ENV_PATH.exists():
    _live_env_values = dotenv_values(_LIVE_ENV_PATH)
    for _env_key in (
        "DART_API_KEY",
        "BIGKINDS_KEY",
        "KIS_APP_KEY",
        "KIS_APP_SECRET",
        "KIS_ACCOUNT_NO",
        "KIS_IS_PAPER",
        "KIS_BASE_URL",
        "NAVER_CLIENT_ID",
        "NAVER_CLIENT_SECRET",
        "NAVER_SEARCH_CLIENT_ID",
        "NAVER_SEARCH_CLIENT_SECRET",
    ):
        if not os.getenv(_env_key):
            _env_value = _live_env_values.get(_env_key)
            if _env_value:
                os.environ[_env_key] = _env_value

from kis_api import _headers, _kis_get, get_access_token, get_kis_market_profile

log = get_collector_logger()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except Exception:
        return default

# ── 설정 ──────────────────────────────────────────────────────────────────────

DART_KEY     = os.getenv("DART_API_KEY", "")
BIGKINDS_KEY = os.getenv("BIGKINDS_KEY", "")
NAVER_CLIENT_ID = (
    os.getenv("NAVER_CLIENT_ID", "").strip()
    or os.getenv("NAVER_SEARCH_CLIENT_ID", "").strip()
    or os.getenv("NAVER_API_CLIENT_ID", "").strip()
)
NAVER_CLIENT_SECRET = (
    os.getenv("NAVER_CLIENT_SECRET", "").strip()
    or os.getenv("NAVER_SEARCH_CLIENT_SECRET", "").strip()
    or os.getenv("NAVER_API_CLIENT_SECRET", "").strip()
)
ENABLE_NAVER_API = os.getenv("KR_NEWS_ENABLE_NAVER_API", "true").lower() in {"1", "true", "yes", "on"}
NAVER_API_MIN_ITEMS = _env_int("KR_NEWS_NAVER_API_MIN_ITEMS", 3)
NAVER_API_MAX_RESULTS = _env_int("KR_NEWS_NAVER_API_MAX_RESULTS", 8)
ENABLE_NAVER_LEGACY = os.getenv("KR_NEWS_ENABLE_NAVER_LEGACY", "false").lower() == "true"
ENABLE_PREOPEN_BIGKINDS = os.getenv("PREOPEN_NEWS_ENABLE_BIGKINDS", "false").lower() in {"1", "true", "yes", "on"}

NEWS_DIR = Path(__file__).parent.parent / "data" / "news" / "kr"
NEWS_DIR.mkdir(parents=True, exist_ok=True)

# 수집 대상 종목 (종목코드: 회사명)
TARGET_CORPS = {
    "005930": "삼성전자",
    "068270": "셀트리온",
    "035420": "NAVER",
    "035720": "카카오",
    "005380": "현대차",
    "051910": "LG화학",
}


def _normalize_kr_targets(targets: dict[str, str] | None) -> dict[str, str]:
    raw_targets = targets if targets is not None else TARGET_CORPS
    normalized: dict[str, str] = {}
    for code, name in raw_targets.items():
        digits = "".join(ch for ch in str(code or "") if ch.isdigit())
        if not digits:
            continue
        ticker = digits.zfill(6)
        if ticker not in normalized:
            normalized[ticker] = str(name or ticker).strip() or ticker
    return normalized


def _read_news_file(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_existing_if_reusable(path: Path, target_tickers: list[str], force: bool) -> dict | None:
    if force or not path.exists():
        return None
    data = _read_news_file(path)
    if not data:
        return None
    existing_tickers = data.get("target_tickers") or list((data.get("corp_news") or {}).keys())
    existing_tickers = [str(t) for t in existing_tickers]
    if not target_tickers or existing_tickers == target_tickers:
        market_news_count = len(data.get("market_news") or [])
        corp_total = sum(
            int((item or {}).get("count", len((item or {}).get("items", []))) or 0)
            for item in (data.get("corp_news") or {}).values()
            if isinstance(item, dict)
        )
        if market_news_count <= 0 and corp_total <= 0 and os.getenv("KR_NEWS_ALLOW_EMPTY_REUSE", "").lower() not in {"1", "true", "yes", "on"}:
            log.warning(f"KR news file is empty; recollecting instead of reusing: {path.name}")
            return None
        log.info(f"[SKIP] KR news file exists and force=False: {path.name}")
        return data
    log.warning(
        f"KR news file target mismatch; recollecting {path.name} "
        f"existing={len(existing_tickers)} requested={len(target_tickers)}"
    )
    return None


def _provider_key(source: str) -> str:
    text = str(source or "").strip()
    if text.startswith("BigKinds"):
        return "BigKinds"
    if text.startswith("KIS"):
        return "KIS"
    if "Naver" in text or "네이버" in text:
        return "Naver"
    return text or "unknown"


def _attach_collection_metadata(result: dict, targets: dict[str, str], target_source: str) -> None:
    corp_news = result.get("corp_news") or {}
    disclosures = result.get("disclosures") or {}
    provider_counts: dict[str, int] = {}
    for corp in corp_news.values():
        for item in corp.get("items", []) or []:
            key = _provider_key(item.get("source", ""))
            provider_counts[key] = provider_counts.get(key, 0) + 1
    dart_count = sum(len(items or []) for items in disclosures.values())
    if dart_count:
        provider_counts["DART"] = dart_count
    target_tickers = list(targets.keys())
    missing = [
        ticker for ticker in target_tickers
        if int((corp_news.get(ticker) or {}).get("count", 0) or 0) <= 0
    ]
    covered = len(target_tickers) - len(missing)
    result["target_source"] = target_source
    result["target_count"] = len(target_tickers)
    result["target_tickers"] = target_tickers
    result["provider_counts"] = provider_counts
    result["news_coverage"] = {
        "covered_ticker_count": covered,
        "missing_tickers": missing,
        "coverage_ratio": round(covered / len(target_tickers), 4) if target_tickers else 0.0,
    }

# DART 공시 유형 코드
DART_REPORT_TYPES = {
    "A": "정기공시",      # 사업보고서, 분기보고서
    "B": "주요사항보고",  # 실적발표, 계약, 인수합병
    "C": "발행공시",      # 유상증자 등
    "D": "지분공시",      # 대량보유, 임원주식
}

# 코스피/코스닥 지수 관련 주요 키워드 (뉴스 필터용)
INDEX_KEYWORDS = [
    "코스피", "코스닥", "외국인", "기관", "개인",
    "금리", "환율", "달러", "반도체", "AI", "인공지능",
    "실적", "영업이익", "매출", "수출", "무역"
]


# ── DART 공시 수집 ────────────────────────────────────────────────────────────

@log_retry(max_retries=3, delay=2.0, logger=log)
def fetch_dart_disclosures(corp_code: str, start_dt: str, end_dt: str) -> list[dict]:
    """
    DART 공시 목록 조회
    corp_code: DART 고유번호 (종목코드와 다름)
    start_dt/end_dt: 'YYYYMMDD'
    """
    if not DART_KEY:
        log.warning("DART_API_KEY 없음 → 건너뜀")
        return []

    url = "https://opendart.fss.or.kr/api/list.json"
    params = {
        "crtfc_key": DART_KEY,
        "corp_code": corp_code,
        "bgn_de":    start_dt,
        "end_de":    end_dt,
        "page_no":   "1",
        "page_count": "40",
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "000":
        log.warning(f"DART 응답 오류: {data.get('message', '')}")
        return []

    items = data.get("list", [])
    result = []
    for item in items:
        result.append({
            "source":    "DART",
            "date":      item.get("rcept_dt", ""),
            "title":     item.get("report_nm", ""),
            "corp_name": item.get("corp_name", ""),
            "rcept_no":  item.get("rcept_no", ""),
            "url":       f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={item.get('rcept_no','')}",
            "type":      item.get("rm", ""),
        })
    log.debug(f"DART [{corp_code}] {len(result)}건 수집")
    return result


def get_dart_corp_code(stock_code: str) -> str:
    """
    종목코드 → DART 고유번호 변환
    DART 전체 기업 코드 파일 다운로드 후 매핑
    """
    import zipfile
    import io

    cache_path = Path(__file__).parent.parent / "data" / "dart_corp_codes.json"

    # 캐시 있으면 바로 반환
    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            mapping = json.load(f)
        code = mapping.get(stock_code)
        if code:
            return code

    if not DART_KEY:
        return ""

    log.info("DART 기업코드 파일 다운로드 중...")
    url  = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={DART_KEY}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    # ZIP 압축 해제
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_data = zf.read("CORPCODE.xml").decode("utf-8")

    soup    = BeautifulSoup(xml_data, "xml")
    mapping = {}
    for corp in soup.find_all("list"):
        s_code = corp.find("stock_code")
        c_code = corp.find("corp_code")
        if s_code and c_code and s_code.text.strip():
            mapping[s_code.text.strip()] = c_code.text.strip()

    # 캐시 저장
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False)
    log.info(f"DART 기업코드 {len(mapping)}개 캐시 저장")

    return mapping.get(stock_code, "")


def _kis_date(value: str, fallback: str) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return fallback


def _kis_related_values(item: dict, prefix: str) -> list[str]:
    values = []
    for idx in range(1, 11):
        value = str(item.get(f"{prefix}{idx}") or "").strip()
        if value:
            values.append(value)
    return values


@log_retry(max_retries=2, delay=1.0, logger=log)
def fetch_kis_news(stock_code: str, target_date: str, max_results: int = 20) -> list[dict]:
    """
    KIS domestic stock news-title.
    Endpoint: /uapi/domestic-stock/v1/quotations/news-title
    TR ID: FHKST01011800
    """
    target_ymd = target_date.replace("-", "")
    profile = get_kis_market_profile("KR")
    token = get_access_token(market="KR")
    resp = _kis_get(
        f"{profile.base_url}/uapi/domestic-stock/v1/quotations/news-title",
        headers=_headers(token, "FHKST01011800", market="KR"),
        params={
            "FID_NEWS_OFER_ENTP_CODE": "",
            "FID_COND_MRKT_CLS_CODE": "00",
            "FID_INPUT_ISCD": stock_code,
            "FID_TITL_CNTT": "",
            "FID_INPUT_DATE_1": target_ymd,
            "FID_INPUT_HOUR_1": "",
            "FID_RANK_SORT_CLS_CODE": "01",
            "FID_INPUT_SRNO": "",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("rt_cd") != "0":
        log.warning(f"KIS news response [{stock_code}]: {data.get('msg1') or data.get('msg_cd')}")
        return []

    raw_items = data.get("output") or data.get("outblock1") or []
    if isinstance(raw_items, dict):
        raw_items = [raw_items]

    results = []
    seen = set()
    for item in raw_items:
        published_ymd = str(item.get("data_dt") or "").strip()
        if published_ymd and published_ymd != target_ymd:
            continue

        related_tickers = _kis_related_values(item, "iscd")
        if related_tickers and stock_code not in related_tickers:
            continue

        title = str(item.get("hts_pbnt_titl_cntt") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)

        published_time = str(item.get("data_tm") or "").strip()
        published_date = _kis_date(published_ymd, target_date)
        published_at = published_date
        if len(published_time) == 6 and published_time.isdigit():
            published_at = (
                f"{published_date}T{published_time[:2]}:"
                f"{published_time[2:4]}:{published_time[4:6]}+09:00"
            )

        results.append({
            "source": "KIS",
            "provider": item.get("dorg", "") or "KIS",
            "date": published_date,
            "published_at": published_at,
            "title": title,
            "content": "",
            "url": "",
            "ticker": stock_code,
            "related_tickers": related_tickers,
            "related_names": _kis_related_values(item, "kor_isnm"),
            "news_id": item.get("cntt_usiq_srno", ""),
            "matched_by": "kis_iscd",
        })
        if len(results) >= max_results:
            break

    log.debug(f"KIS news [{stock_code}] {target_date}: {len(results)}건")
    return results


# ── 네이버 금융 뉴스 크롤링 ──────────────────────────────────────────────────

@log_retry(max_retries=3, delay=3.0, logger=log)
def fetch_naver_news(stock_code: str, target_date: str, max_pages: int = 3) -> list[dict]:
    """
    네이버 금융 종목 뉴스 크롤링
    target_date: 'YYYY-MM-DD'
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    results = []
    target_dt = datetime.strptime(target_date, "%Y-%m-%d").date()

    for page in range(1, max_pages + 1):
        url = (
            f"https://finance.naver.com/item/news_news.naver"
            f"?code={stock_code}&page={page}"
        )
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "html.parser")

            rows = soup.select("table.type5 tr")
            found_older = False

            for row in rows:
                title_tag = row.select_one("td.title a")
                date_tag  = row.select_one("td.date")
                if not title_tag or not date_tag:
                    continue

                date_str = date_tag.text.strip()
                title    = title_tag.text.strip()
                link     = "https://finance.naver.com" + title_tag.get("href", "")

                try:
                    # 날짜 파싱 (형식: YYYY.MM.DD HH:MM)
                    news_dt = datetime.strptime(
                        date_str[:10], "%Y.%m.%d"
                    ).date()
                except ValueError:
                    continue

                if news_dt == target_dt:
                    results.append({
                        "source":  "네이버금융",
                        "date":    target_date,
                        "title":   title,
                        "url":     link,
                        "content": "",  # 본문은 별도 fetch
                    })
                elif news_dt < target_dt:
                    found_older = True
                    break

            if found_older:
                break
            time.sleep(0.5)

        except Exception as e:
            log.error(f"네이버 뉴스 크롤링 오류 [{stock_code}] p{page}: {e}")
            break

    log.debug(f"네이버금융 [{stock_code}] {target_date}: {len(results)}건")
    return results


@log_retry(max_retries=2, delay=2.0, logger=log)
def fetch_naver_news_content(url: str) -> str:
    """네이버 뉴스 본문 가져오기"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.encoding = "euc-kr"
    soup = BeautifulSoup(resp.text, "html.parser")

    # 본문 영역
    content_area = (
        soup.select_one("div#newsct_article") or
        soup.select_one("div.news_cnt_detail_wrap") or
        soup.select_one("div#content")
    )
    if not content_area:
        return ""

    text = content_area.get_text(separator=" ", strip=True)
    return text[:500]  # 500자 제한 (토큰 절약)


# ── BigKinds API ──────────────────────────────────────────────────────────────

def _clean_naver_search_text(value: str) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _naver_search_pub_date(value: str) -> tuple[str, str]:
    try:
        parsed = parsedate_to_datetime(str(value or ""))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(KST)
        return parsed.date().isoformat(), parsed.isoformat(timespec="seconds")
    except Exception:
        return "", ""


def _naver_search_matches_target(
    *,
    title: str,
    description: str,
    stock_code: str,
    name: str,
) -> bool:
    haystack = f"{title} {description}".lower()
    code = "".join(ch for ch in str(stock_code or "") if ch.isdigit())
    clean_name = str(name or "").strip()
    if clean_name and clean_name.lower() in haystack:
        return True
    if code and code in haystack:
        return True
    return False


@log_retry(max_retries=2, delay=2.0, logger=log)
def fetch_naver_api_news(
    stock_code: str,
    name: str,
    target_date: str,
    max_results: int | None = None,
) -> list[dict]:
    if not ENABLE_NAVER_API or not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        log.debug("Naver Search API key missing or disabled")
        return []

    display = max(1, min(int(max_results or NAVER_API_MAX_RESULTS or 8), 100))
    query_name = str(name or stock_code or "").strip()
    if not query_name:
        return []

    response = requests.get(
        "https://openapi.naver.com/v1/search/news.json",
        headers={
            "X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        },
        params={
            "query": query_name,
            "display": display,
            "start": 1,
            "sort": "date",
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()

    results: list[dict] = []
    seen_titles: set[str] = set()
    for item in payload.get("items", []) or []:
        title = _clean_naver_search_text(item.get("title", ""))
        description = _clean_naver_search_text(item.get("description", ""))
        if not title:
            continue
        published_date, published_at = _naver_search_pub_date(item.get("pubDate", ""))
        if published_date != target_date:
            continue
        if not _naver_search_matches_target(
            title=title,
            description=description,
            stock_code=stock_code,
            name=name,
        ):
            continue
        if title in seen_titles:
            continue
        seen_titles.add(title)
        results.append({
            "source": "Naver Search API",
            "provider": "Naver",
            "date": published_date,
            "published_at": published_at or published_date,
            "title": title,
            "content": description[:500],
            "url": item.get("originallink") or item.get("link") or "",
            "naver_url": item.get("link") or "",
            "ticker": stock_code,
            "matched_by": "naver_search_name",
        })

    log.debug(f"Naver Search API [{stock_code}] {target_date}: {len(results)} items")
    return results


@log_retry(max_retries=3, delay=2.0, logger=log)
def fetch_bigkinds_news(keyword: str, target_date: str, max_results: int = 10) -> list[dict]:
    """
    BigKinds API 뉴스 검색
    keyword: 검색어 (종목명 또는 이슈)
    target_date: 'YYYY-MM-DD'
    """
    if not BIGKINDS_KEY:
        log.debug("BIGKINDS_KEY 없음 → 건너뜀")
        return []

    url = "https://tools.kinds.or.kr/search/news"
    payload = {
        "access_key":       BIGKINDS_KEY,
        "query":            keyword,
        "published_at": {
            "from": f"{target_date}T00:00:00",
            "until": f"{target_date}T23:59:59",
        },
        "fields": ["title", "content", "provider", "published_at"],
        "sort": {"date": "desc"},
        "return_from": 0,
        "return_size":  max_results,
    }
    resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    hits    = data.get("return_object", {}).get("documents", [])
    results = []
    for hit in hits:
        results.append({
            "source":  f"BigKinds({hit.get('provider','언론사')})",
            "date":    target_date,
            "title":   hit.get("title", ""),
            "content": hit.get("content", "")[:500],
            "url":     hit.get("url", ""),
        })
    log.debug(f"BigKinds [{keyword}] {target_date}: {len(results)}건")
    return results


# ── 시황 뉴스 수집 (지수 전반) ───────────────────────────────────────────────

@log_retry(max_retries=3, delay=2.0, logger=log)
def fetch_market_news(target_date: str) -> list[dict]:
    """
    당일 시장 전반 뉴스 (코스피/코스닥 시황)
    네이버 증권 메인 크롤링
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    results = []

    # 네이버 증권 뉴스
    url = "https://finance.naver.com/news/mainnews.naver"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")

        for item in soup.select("ul.newsList li")[:10]:
            title_tag = next(
                (a for a in item.select("a") if a.get_text(" ", strip=True)),
                None,
            )
            if not title_tag:
                continue
            title = title_tag.get_text(" ", strip=True)
            # 시황 관련 키워드 필터
            if any(kw in title for kw in INDEX_KEYWORDS):
                results.append({
                    "source":  "네이버증권_시황",
                    "date":    target_date,
                    "title":   title,
                    "content": "",
                    "url":     "https://finance.naver.com" + title_tag.get("href", ""),
                })
    except Exception as e:
        log.warning(f"시황 뉴스 수집 실패: {e}")

    log.debug(f"시황 뉴스 {target_date}: {len(results)}건")
    return results


# ── 하루치 전체 수집 ──────────────────────────────────────────────────────────

@log_call(logger=log, level="INFO")
def collect_day(
    target_date: str,
    targets: dict[str, str] | None = None,
    fetch_content: bool = False,
    *,
    force: bool = True,
    target_source: str | None = None,
) -> dict:
    """
    특정 날짜 뉴스/공시 전체 수집
    target_date: 'YYYY-MM-DD'
    fetch_content: True면 본문도 수집 (시간 증가)
    반환: {날짜, 종목별 뉴스, 시황 뉴스, 공시}
    """
    if isinstance(targets, bool):
        fetch_content = bool(targets)
        targets = None
    normalized_targets = _normalize_kr_targets(targets)
    target_source = target_source or ("explicit_targets" if targets is not None else "fallback_target_corps")
    save_path = NEWS_DIR / f"{target_date}.json"
    existing = _load_existing_if_reusable(save_path, list(normalized_targets.keys()), force)
    if existing is not None:
        return existing

    log.info(f"━━━ {target_date} 뉴스 수집 시작 ━━━")
    result = {
        "date":        target_date,
        "market_news": [],
        "corp_news":   {},
        "disclosures": {},
        "collected_at": datetime.now().isoformat(),
    }

    # 1. 시황 뉴스
    result["market_news"] = fetch_market_news(target_date)
    time.sleep(0.5)

    # 2. 종목별 뉴스 + 공시
    dart_date = target_date.replace("-", "")
    use_bigkinds = bool(BIGKINDS_KEY) and (targets is None or ENABLE_PREOPEN_BIGKINDS)
    for code, name in normalized_targets.items():
        corp_items = []

        # KIS domestic news-title
        try:
            news = fetch_kis_news(code, target_date)
            corp_items.extend(news)
            time.sleep(0.3)
        except Exception as e:
            log.error(f"KIS news collect failed [{name}]: {e}")

        if len(corp_items) < max(1, NAVER_API_MIN_ITEMS):
            try:
                existing_titles = {str(item.get("title") or "").strip() for item in corp_items}
                news = fetch_naver_api_news(
                    code,
                    name,
                    target_date,
                    max_results=NAVER_API_MAX_RESULTS,
                )
                for item in news:
                    title = str(item.get("title") or "").strip()
                    if title and title not in existing_titles:
                        corp_items.append(item)
                        existing_titles.add(title)
                if news:
                    time.sleep(0.2)
            except Exception as e:
                log.warning(f"Naver Search API collect failed [{name}]: {e}")

        # Legacy Naver PC scraper is fragile; keep it opt-in only.
        if ENABLE_NAVER_LEGACY and not corp_items:
            try:
                news = fetch_naver_news(code, target_date)
                if fetch_content:
                    for item in news[:5]:
                        try:
                            item["content"] = fetch_naver_news_content(item["url"])
                            time.sleep(0.3)
                        except Exception as e:
                            log.debug(f"Naver content collect failed [{name}]: {e}")
                corp_items.extend(news)
                time.sleep(0.5)
            except Exception as e:
                log.error(f"Naver legacy news collect failed [{name}]: {e}")

        # BigKinds
        if use_bigkinds:
            try:
                bk_news = fetch_bigkinds_news(name, target_date)
                corp_items.extend(bk_news)
                time.sleep(0.3)
            except Exception as e:
                log.debug(f"BigKinds 수집 실패 [{name}]: {e}")

        # DART 공시
        try:
            corp_code = get_dart_corp_code(code)
            if corp_code:
                disclosures = fetch_dart_disclosures(
                    corp_code, dart_date, dart_date
                )
                result["disclosures"][code] = disclosures
                time.sleep(0.5)
        except Exception as e:
            log.error(f"DART 수집 실패 [{name}]: {e}")

        result["corp_news"][code] = {
            "name":  name,
            "items": corp_items,
            "count": len(corp_items),
        }
        log.info(f"  [{name}] 뉴스 {len(corp_items)}건, "
                 f"공시 {len(result['disclosures'].get(code,[]))}건")

    _attach_collection_metadata(result, normalized_targets, target_source)

    # 저장
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    total_news = sum(v["count"] for v in result["corp_news"].values())
    log.info(f"━━━ {target_date} 완료 | 시황 {len(result['market_news'])}건 "
             f"| 종목뉴스 {total_news}건 | 저장: {save_path.name} ━━━")
    return result


# ── 기간 전체 수집 ────────────────────────────────────────────────────────────

def collect_range(start: str, end: str, delay_sec: float = 2.0):
    """
    기간 전체 뉴스 수집
    start/end: 'YYYY-MM-DD'
    delay_sec: 날짜 간 대기 (서버 부하 방지)
    """
    log.info(f"{'='*55}")
    log.info(f"  국내 뉴스 기간 수집: {start} ~ {end}")
    log.info(f"{'='*55}")

    start_dt = datetime.strptime(start, "%Y-%m-%d").date()
    end_dt   = datetime.strptime(end,   "%Y-%m-%d").date()
    current  = start_dt

    # 영업일만 수집 (토/일 제외)
    biz_days = []
    while current <= end_dt:
        if current.weekday() < 5:  # 월~금
            biz_days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    log.info(f"총 {len(biz_days)}개 영업일 수집 예정")
    prog = ProgressLogger(
        total=len(biz_days), name="국내뉴스수집",
        logger=log, interval=5
    )

    success = 0
    for day_str in biz_days:
        # 이미 수집된 날짜 건너뜀
        save_path = NEWS_DIR / f"{day_str}.json"
        if save_path.exists():
            log.debug(f"[SKIP] {day_str} (이미 수집됨)")
            prog.step(day_str, success=True)
            success += 1
            continue

        try:
            collect_day(day_str)
            success += 1
            prog.step(day_str, success=True)
        except Exception as e:
            log.error(f"[FAIL] {day_str}: {e}")
            prog.step(day_str, success=False)

        time.sleep(delay_sec)

    prog.done()
    log.info(f"수집 완료: {success}/{len(biz_days)}일 성공")


# ── 로드 유틸 ─────────────────────────────────────────────────────────────────

def load_day(target_date: str) -> dict:
    """특정 날짜 뉴스 데이터 로드"""
    path = NEWS_DIR / f"{target_date}.json"
    if not path.exists():
        log.warning(f"뉴스 데이터 없음: {target_date}")
        return {"date": target_date, "market_news": [], "corp_news": {}, "disclosures": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_day_summary(target_date: str) -> str:
    """
    Claude에게 전달할 형태로 뉴스 요약 텍스트 생성
    """
    data   = load_day(target_date)
    lines  = [f"[{target_date} 뉴스 요약]"]

    # 시황
    if data["market_news"]:
        lines.append("\n▶ 시장 시황")
        for n in data["market_news"][:5]:
            lines.append(f"  • {n['title']}")

    # 공시 (중요도 높음)
    all_disclosures = []
    for code, items in data.get("disclosures", {}).items():
        name = TARGET_CORPS.get(code, code)
        for d in items:
            all_disclosures.append(f"  • [{name}] {d['title']}")
    if all_disclosures:
        lines.append("\n▶ 주요 공시")
        lines.extend(all_disclosures[:10])

    # 종목 뉴스
    lines.append("\n▶ 종목별 주요 뉴스")
    for code, corp in data.get("corp_news", {}).items():
        if corp["count"] > 0:
            lines.append(f"  [{corp['name']}]")
            for item in corp["items"][:3]:
                content = f" - {item['content'][:80]}" if item.get("content") else ""
                lines.append(f"    • {item['title']}{content}")

    return "\n".join(lines)


# ── API 키 확인 ───────────────────────────────────────────────────────────────

def check_api_keys():
    log.info("API 키 상태 확인")
    log.info(f"  DART_API_KEY:  {'✅ 있음' if DART_KEY else '❌ 없음 (opendart.fss.or.kr 발급)'}")
    log.info(f"  BIGKINDS_KEY:  {'✅ 있음' if BIGKINDS_KEY else '⚠️  없음 (bigkinds.or.kr 발급, 선택사항)'}")
    log.info(f"  저장 경로:     {NEWS_DIR}")


if __name__ == "__main__":
    check_api_keys()

    # 테스트: 어제 날짜 수집
    test_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    log.info(f"\n테스트 수집: {test_date}")
    collect_day(test_date)

    log.info("\n요약 텍스트:")
    print(get_day_summary(test_date))
