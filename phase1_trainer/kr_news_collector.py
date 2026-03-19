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

import os
import json
import time
import requests
from pathlib import Path
from datetime import datetime, date, timedelta
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import get_collector_logger, log_retry, log_call, ProgressLogger

load_dotenv()

log = get_collector_logger()

# ── 설정 ──────────────────────────────────────────────────────────────────────

DART_KEY     = os.getenv("DART_API_KEY", "")
BIGKINDS_KEY = os.getenv("BIGKINDS_KEY", "")

NEWS_DIR = Path(__file__).parent.parent / "data" / "news" / "kr"
NEWS_DIR.mkdir(parents=True, exist_ok=True)

# 수집 대상 종목 (종목코드: 회사명)
TARGET_CORPS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "035420": "NAVER",
    "005380": "현대차",
    "051910": "LG화학",
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
            title_tag = item.select_one("a")
            if not title_tag:
                continue
            title = title_tag.text.strip()
            # 시황 관련 키워드 필터
            if any(kw in title for kw in ["코스피", "코스닥", "증시", "주가", "외국인", "반도체"]):
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
def collect_day(target_date: str, fetch_content: bool = False) -> dict:
    """
    특정 날짜 뉴스/공시 전체 수집
    target_date: 'YYYY-MM-DD'
    fetch_content: True면 본문도 수집 (시간 증가)
    반환: {날짜, 종목별 뉴스, 시황 뉴스, 공시}
    """
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
    for code, name in TARGET_CORPS.items():
        corp_items = []

        # 네이버 금융 뉴스
        try:
            news = fetch_naver_news(code, target_date)
            if fetch_content:
                for item in news[:5]:  # 상위 5개만 본문 수집
                    try:
                        item["content"] = fetch_naver_news_content(item["url"])
                        time.sleep(0.3)
                    except Exception as e:
                        log.debug(f"본문 수집 실패 [{name}]: {e}")
            corp_items.extend(news)
            time.sleep(0.5)
        except Exception as e:
            log.error(f"네이버 뉴스 수집 실패 [{name}]: {e}")

        # BigKinds
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

    # 저장
    save_path = NEWS_DIR / f"{target_date}.json"
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
