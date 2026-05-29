# 개인 중장기 투자 뉴스/공시 대시보드 개발 요구서

작성일: 2026-05-29
대상 작업: 자동매매와 분리된 개인 투자 정보 수집, 요약, 텔레그램 알림, 웹 대시보드

## 1. 목표

한국장과 미국장에 대해 중장기 투자 판단에 필요한 뉴스, 공시, 경제지표, 정책 이벤트, 섹터/테마 변화를 자동 수집하고, 사람이 빠르게 볼 수 있도록 중요도별로 요약한다.

이 시스템은 매매 자동화가 아니라 정보 큐레이션 도구다. 사용자는 텔레그램으로 핵심 알림을 받고, 웹 대시보드에서 전체 이력과 링크를 검색한다.

## 2. 명시적 비목표

- 주문, 주문 수량, 주문 금액, 손절, 익절, 포지션 관리 기능을 만들지 않는다.
- 자동매매 런타임, 브로커 API truth, 리스크 매니저, PathA/PathB, selection pipeline과 연결하지 않는다.
- 뉴스 중요도 점수를 자동 매수/매도 신호로 사용하지 않는다.
- 원문 기사 전문을 DB에 저장하지 않는다.
- `state/brain.json` 또는 기존 정책 메모리를 수정하지 않는다.
- 운영 `.env.live`, `.env.paper`, `config/v2_start_config.json`의 매매 관련 설정을 변경하지 않는다.

## 3. 보호 경계

이번 신규 기능은 기존 자동매매 시스템의 sidecar가 아니라 독립 도구로 구현한다.

금지 의존성:

- `trading_bot.py`
- `risk_manager.py`
- `execution/`
- `runtime/pathb_runtime.py`
- `runtime/action_routing.py`
- 브로커 주문 제출 함수
- broker truth reconcile 함수
- PathB live gate, sell review, sizing, hard stop 관련 함수

허용 가능한 공통 의존성:

- `runtime_paths.py`: 저장 경로 통일이 필요한 경우
- `logger.py`: 별도 investment news logger를 추가하는 경우
- `telegram_reporter.send`: 텔레그램 전송 유틸만 재사용 가능
- `dashboard/dashboard_server.py`: 읽기 전용 API와 UI 탭 추가

## 4. 사용자 경험 요구사항

### 4.1 텔레그램

텔레그램은 전체 목록이 아니라 즉시 확인해야 하는 요약만 보낸다.

정기 발송:

- KR 장전 요약
- KR 장후 요약
- US 장전 요약
- US 장후 요약
- 일간 통합 요약

즉시 발송:

- `S` 등급 이벤트만 기본 즉시 발송
- 설정으로 `A` 등급까지 즉시 발송 가능해야 한다.

메시지 포맷:

```text
[US S급 | CPI/Fed | 2026-05-29 21:30 KST]

미국 CPI가 예상치를 상회했다.
의미: 금리 인하 기대 후퇴, 성장주/AI/반도체 밸류에이션 부담 가능성.
관련: QQQ, SOXX, NVDA, MSFT
원문: https://...
```

제약:

- Telegram `sendMessage`의 4096자 제한을 넘지 않도록 분할한다.
- 한 메시지에는 최대 5개 항목만 포함한다.
- 중복 발송 방지를 위해 `news_items.sent_telegram_at`을 기록한다.
- 메시지에는 원문 전문을 넣지 않고 링크만 제공한다.

### 4.2 웹 대시보드

대시보드는 누적 검색과 비교를 위한 주 화면이다.

필수 화면:

- 오늘의 핵심
- 중요도별 뉴스
- 테마별 뉴스
- 종목별 뉴스 히스토리
- 매크로 캘린더
- 텔레그램 발송 이력
- 수집/요약 상태

필수 필터:

- 시장: `KR`, `US`, `GLOBAL`
- 중요도: `S`, `A`, `B`, `C`
- 정보 유형: `disclosure`, `macro`, `policy`, `earnings`, `industry`, `company_news`, `market_data`
- 기간: 오늘, 7일, 30일, 90일
- 종목
- 섹터
- 테마
- 출처

대시보드에는 원문 링크 버튼을 제공한다. 원문 내용 전문을 화면에 재게시하지 않는다.

## 5. 정보 등급 정책

### 5.1 중요도

`S`: 즉시 확인 필요

- FOMC, CPI, 고용, GDP 등 시장 전체를 흔드는 이벤트
- 대형 실적 쇼크 또는 가이던스 급변
- 대형 M&A, 대규모 CAPEX, 핵심 공급계약
- 회계 이슈, 규제 조사, 거래정지, 상장폐지 위험
- 대규모 증자, CB/BW, 유상증자, 대주주 지분 변동

`A`: 중장기 투자 thesis 영향

- 구조적 산업 변화
- AI, 반도체, 전력, 원전, 방산, 조선, 바이오, 2차전지 등 핵심 테마 변화
- 정책 지원 또는 규제 완화/강화
- 주요 기업 실적 방향성 변화
- 공급망 재편

`B`: 참고 가치 있음

- 섹터 수급 변화
- 애널리스트 리포트
- 반복되는 산업 전망
- ETF/기관 자금 흐름
- 단기 가격 변동 설명 기사

`C`: 보관만

- 중복 기사
- 단순 주가 해설
- 출처 신뢰도 낮음
- 투자 판단 영향이 약한 일반 기사

### 5.2 투자 시계열

- `short`: 1주 이내 참고
- `medium`: 1~6개월 투자 판단 참고
- `long`: 6개월 이상 thesis 참고
- `event`: 특정 발표일/공시일 이벤트

### 5.3 신뢰도

- `official`: DART, SEC, Fed, BLS, BEA, FRED, KRX 등 공식 원천
- `high`: 주요 언론, 거래소/기업 IR, 검증된 데이터 제공자
- `medium`: 일반 언론, 뉴스 API
- `low`: 블로그성/반복성/확인 부족 자료

공식 원천은 동일 이슈의 일반 기사보다 우선한다.

## 6. 데이터 소스 요구사항

### 6.1 한국

공식/우선 원천:

- DART 공시
- KRX 지수/주식/거래/수급 데이터
- 한국은행 ECOS 지표
- 금융위원회/금감원 보도자료
- 한국거래소 공지

보조 원천:

- 네이버 뉴스 API
- KIS 국내 뉴스
- BigKinds, 사용 가능 시

필수 추적 항목:

- KOSPI, KOSDAQ
- VKOSPI
- USD/KRW
- 외국인/기관 수급
- 금리, 물가, 수출입
- 반도체 수출
- DART 주요 공시

### 6.2 미국

공식/우선 원천:

- SEC EDGAR submissions, XBRL
- Federal Reserve FOMC calendar/statements/minutes
- FRED
- BLS
- BEA

보조 원천:

- Alpha Vantage News Sentiment
- Finnhub company news
- KIS 해외 뉴스
- 기업 IR RSS 또는 SEC 8-K

필수 추적 항목:

- S&P500, Nasdaq, Dow
- VIX
- DXY
- US 10Y yield
- WTI
- CPI, PPI, NFP, GDP, retail sales, PMI
- FOMC statement/minutes/SEP
- SEC 8-K, 10-Q, 10-K, 6-K

## 7. 저장소 구조 요구사항

신규 코드는 기존 자동매매 도메인과 분리한다.

권장 신규 구조:

```text
investment_news/
  __init__.py
  models.py
  store.py
  dedupe.py
  scoring.py
  summarizer.py
  digest.py
  telegram.py
  scheduler.py
  sources/
    __init__.py
    dart.py
    sec.py
    fed.py
    fred.py
    bls.py
    bea.py
    krx.py
    naver.py
    alphavantage.py
    finnhub.py
  dashboard_api.py

tools/
  investment_news_collect.py
  investment_news_digest.py
  investment_news_scheduler.py

tests/
  test_investment_news_store.py
  test_investment_news_dedupe.py
  test_investment_news_scoring.py
  test_investment_news_digest.py
  test_investment_news_telegram.py
  test_dashboard_investment_news.py
```

DB 위치:

```text
data/investment_news/investment_news.db
data/investment_news/raw_snapshots/
data/investment_news/digests/
```

`raw_snapshots`에는 원문 전문을 저장하지 않는다. API 응답 원본을 저장해야 할 경우에도 제목, URL, 발행시각, 출처, 식별자, 짧은 설명까지만 보존한다.

## 8. DB 스키마 요구사항

초기 DB는 SQLite로 구현한다.

### 8.1 `news_items`

```sql
CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    market TEXT NOT NULL,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    published_at TEXT NOT NULL DEFAULT '',
    collected_at TEXT NOT NULL,
    importance TEXT NOT NULL DEFAULT 'C',
    horizon TEXT NOT NULL DEFAULT 'short',
    confidence TEXT NOT NULL DEFAULT 'medium',
    sentiment TEXT NOT NULL DEFAULT 'neutral',
    tickers_json TEXT NOT NULL DEFAULT '[]',
    sectors_json TEXT NOT NULL DEFAULT '[]',
    themes_json TEXT NOT NULL DEFAULT '[]',
    entities_json TEXT NOT NULL DEFAULT '[]',
    score_json TEXT NOT NULL DEFAULT '{}',
    reason TEXT NOT NULL DEFAULT '',
    sent_telegram_at TEXT NOT NULL DEFAULT '',
    pinned_dashboard INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

인덱스:

```sql
CREATE INDEX IF NOT EXISTS idx_news_items_market_published
ON news_items(market, published_at);

CREATE INDEX IF NOT EXISTS idx_news_items_importance_published
ON news_items(importance, published_at);

CREATE INDEX IF NOT EXISTS idx_news_items_source_type_published
ON news_items(source_type, published_at);
```

### 8.2 `daily_digests`

```sql
CREATE TABLE IF NOT EXISTS daily_digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    digest_date TEXT NOT NULL,
    market TEXT NOT NULL,
    window_name TEXT NOT NULL,
    summary TEXT NOT NULL,
    top_items_json TEXT NOT NULL DEFAULT '[]',
    market_context_json TEXT NOT NULL DEFAULT '{}',
    telegram_message TEXT NOT NULL DEFAULT '',
    telegram_sent_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(digest_date, market, window_name)
);
```

### 8.3 `collector_runs`

```sql
CREATE TABLE IF NOT EXISTS collector_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    market TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    meta_json TEXT NOT NULL DEFAULT '{}'
);
```

## 9. 모델/인터페이스 요구사항

### 9.1 `InvestmentNewsItem`

`investment_news/models.py`

```python
@dataclass(frozen=True)
class InvestmentNewsItem:
    market: str
    source: str
    source_type: str
    title: str
    url: str
    published_at: str = ""
    source_id: str = ""
    summary: str = ""
    tickers: tuple[str, ...] = ()
    sectors: tuple[str, ...] = ()
    themes: tuple[str, ...] = ()
    sentiment: str = "neutral"
    confidence: str = "medium"
    importance: str = "C"
    horizon: str = "short"
    reason: str = ""
```

### 9.2 Collector 인터페이스

각 수집기는 같은 시그니처를 따른다.

```python
def collect(
    *,
    market: str,
    since: datetime,
    until: datetime,
    watchlist: Watchlist,
    limit: int = 100,
) -> list[InvestmentNewsItem]:
    ...
```

수집기는 외부 API 예외를 삼키지 말고 `collector_runs.error`에 기록할 수 있게 상위로 전달하거나 구조화된 실패 결과를 반환한다.

### 9.3 Store 인터페이스

`investment_news/store.py`

```python
class InvestmentNewsStore:
    def upsert_items(self, items: Iterable[InvestmentNewsItem]) -> StoreResult: ...
    def list_items(self, filters: NewsFilters) -> list[dict[str, Any]]: ...
    def mark_telegram_sent(self, item_ids: Iterable[int], sent_at: str) -> None: ...
    def save_daily_digest(self, digest: DailyDigest) -> None: ...
    def latest_digest(self, market: str, window_name: str) -> dict[str, Any] | None: ...
```

## 10. 중복 제거 요구사항

`dedupe_key` 우선순위:

1. 공식 원천 고유 ID
   - DART `rcept_no`
   - SEC accession number
   - Fed release URL
2. URL canonical form
3. `source + normalized_title + published_date`

정규화:

- 제목의 HTML 태그 제거
- 공백 압축
- 대소문자 통일
- 언론사 접두/반복 문구 제거
- URL query 중 tracking parameter 제거

같은 이슈가 공식 공시와 뉴스 기사로 동시에 들어오면 공식 공시를 primary로 둔다. 뉴스 기사는 related link로 연결하는 확장을 허용하되 초기 버전에서는 같은 `dedupe_key`로 collapse해도 된다.

## 11. 점수화 요구사항

점수는 설명 가능해야 한다.

```python
score = (
    source_weight
    + event_weight
    + market_impact_weight
    + watchlist_weight
    + theme_weight
    + novelty_weight
    - noise_penalty
)
```

초기 가중치:

- 공식 공시/정부/중앙은행: +4
- 실적/가이던스/대형 계약/M&A/CAPEX: +3
- CPI/FOMC/NFP/GDP 등 매크로 핵심 이벤트: +4
- 관심 종목 직접 언급: +2
- 핵심 테마 직접 언급: +2
- 중복/단순 주가 해설: -3
- 출처 낮음: -2

등급 매핑:

- `S`: 8점 이상 또는 강제 S 키워드
- `A`: 5~7점
- `B`: 2~4점
- `C`: 1점 이하

강제 S 후보:

- `FOMC`, `CPI`, `NFP`, `guidance cut`, `accounting`, `investigation`, `bankruptcy`
- `유상증자`, `거래정지`, `상장폐지`, `횡령`, `배임`, `감사의견`, `대규모 공급계약`

## 12. 요약 요구사항

요약은 1~2문장으로 제한한다.

요약 필드:

- `summary`: 사람이 읽는 핵심 요약
- `reason`: 왜 중요한지
- `themes_json`: 관련 테마
- `tickers_json`: 관련 종목

LLM 사용은 선택 사항이다. 초기 버전은 rule-based summary를 먼저 구현하고, 이후 환경변수로 LLM summary를 켤 수 있게 한다.

LLM 사용 시 필수 제약:

- 원문 전문을 장기 저장하지 않는다.
- 프롬프트에는 제목, 짧은 설명, 공식 메타데이터, URL만 넘긴다.
- 결과는 투자 조언 문구가 아니라 정보 요약이어야 한다.
- "매수", "매도", "비중 확대" 같은 직접 행동 지시를 생성하지 않는다.

## 13. Watchlist 요구사항

초기 watchlist는 JSON 파일로 관리한다.

권장 경로:

```text
config/investment_news_watchlist.json
```

형식:

```json
{
  "KR": {
    "tickers": {
      "005930": {"name": "삼성전자", "themes": ["semiconductor", "ai"]},
      "000660": {"name": "SK하이닉스", "themes": ["semiconductor", "hbm"]}
    },
    "themes": ["semiconductor", "battery", "defense", "shipbuilding", "bio", "power"]
  },
  "US": {
    "tickers": {
      "NVDA": {"name": "NVIDIA", "themes": ["ai", "semiconductor"]},
      "MSFT": {"name": "Microsoft", "themes": ["ai", "cloud"]}
    },
    "themes": ["ai", "semiconductor", "cloud", "energy", "healthcare", "defense"]
  },
  "GLOBAL": {
    "macro": ["FOMC", "CPI", "PPI", "NFP", "GDP", "USD/KRW", "US10Y", "WTI"]
  }
}
```

Watchlist 변경은 자동매매 설정과 분리한다.

## 14. CLI 요구사항

### 14.1 수집

```powershell
python tools/investment_news_collect.py --market KR --window 24h
python tools/investment_news_collect.py --market US --window 24h
python tools/investment_news_collect.py --market GLOBAL --window 24h
```

옵션:

- `--market KR|US|GLOBAL|ALL`
- `--source dart,sec,fed,naver`
- `--since YYYY-MM-DDTHH:MM:SS`
- `--until YYYY-MM-DDTHH:MM:SS`
- `--window 6h|24h|7d`
- `--dry-run`
- `--json`

### 14.2 요약

```powershell
python tools/investment_news_digest.py --market KR --window-name preopen --send-telegram
```

옵션:

- `--market KR|US|GLOBAL|ALL`
- `--window-name preopen|postclose|daily|urgent`
- `--min-importance S|A|B|C`
- `--send-telegram`
- `--dry-run`
- `--json`

### 14.3 스케줄러

```powershell
python tools/investment_news_scheduler.py --loop --interval-sec 300
```

스케줄러는 Windows Task Scheduler 없이도 동작해야 하며, 별도 `.bat` 등록은 후속 작업으로 둔다.

## 15. 환경변수 요구사항

신규 환경변수만 사용한다.

```text
INVEST_NEWS_DB_PATH=data/investment_news/investment_news.db
INVEST_NEWS_TELEGRAM_ENABLED=false
INVEST_NEWS_TELEGRAM_MIN_IMPORTANCE=S
INVEST_NEWS_LLM_SUMMARY_ENABLED=false
INVEST_NEWS_MAX_ITEMS_PER_MESSAGE=5
INVEST_NEWS_COLLECTOR_TIMEOUT_SEC=20
INVEST_NEWS_RETENTION_DAYS=365
```

외부 API 키:

```text
DART_API_KEY=
NAVER_CLIENT_ID=
NAVER_CLIENT_SECRET=
FRED_API_KEY=
BLS_API_KEY=
BEA_API_KEY=
ALPHA_VANTAGE_KEY=
FINNHUB_API_KEY=
```

기존 `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`는 재사용 가능하다. 단, 발송 여부는 `INVEST_NEWS_TELEGRAM_ENABLED`로 별도 제어한다.

## 16. Dashboard API 요구사항

`dashboard/dashboard_server.py`에 읽기 전용 API를 추가한다. 구현 시 기존 자동매매 API와 상태를 섞지 않는다.

엔드포인트:

```text
GET /api/investment-news/items
GET /api/investment-news/digest
GET /api/investment-news/themes
GET /api/investment-news/tickers/<ticker>
GET /api/investment-news/collector-runs
```

`GET /api/investment-news/items` query:

- `market`
- `importance`
- `source_type`
- `theme`
- `ticker`
- `days`
- `limit`

응답 예:

```json
{
  "items": [
    {
      "id": 1,
      "market": "US",
      "importance": "S",
      "source_type": "macro",
      "title": "FOMC statement released",
      "summary": "연준 성명에서 인플레이션 경계가 강화됐다.",
      "reason": "성장주 밸류에이션과 달러/금리 경로에 영향.",
      "url": "https://...",
      "published_at": "2026-05-29T18:00:00Z",
      "tickers": ["QQQ", "SOXX"],
      "themes": ["rates", "growth"]
    }
  ],
  "count": 1
}
```

## 17. 대시보드 UI 요구사항

기존 대시보드에 `투자뉴스` 탭을 추가한다.

화면 구성:

- 상단 요약: 오늘 S/A 개수, 마지막 수집 시간, 수집 오류 수
- 좌측 필터: 시장, 중요도, 기간, 테마, 종목
- 본문 리스트: 중요도 badge, 제목, 요약, 이유, 태그, 원문 링크
- 우측 패널: 오늘의 매크로 이벤트, 예정 일정
- 하단: collector run 상태

UI 원칙:

- 자동매매 상태와 같은 카드에 섞지 않는다.
- 뉴스 항목은 카드 또는 행 단위로 표시한다.
- 원문 보기 버튼은 새 탭 링크다.
- S/A 항목은 먼저 보이게 정렬한다.
- C 항목은 기본 숨김이다.

## 18. 텔레그램 구현 요구사항

`investment_news/telegram.py`

```python
def build_telegram_digest_message(
    *,
    market: str,
    window_name: str,
    items: list[dict[str, Any]],
    max_items: int = 5,
) -> list[str]:
    ...
```

요구사항:

- HTML parse mode에서 깨지지 않도록 title, summary, URL을 escape한다.
- 4096자 제한 이전에 안전하게 분할한다.
- `S`와 `A`를 먼저 정렬한다.
- 각 항목은 링크 1개만 포함한다.
- 발송 성공 후에만 `sent_telegram_at`을 기록한다.

## 19. 수집 실패/품질 요구사항

수집 실패는 조용히 무시하지 않는다.

필수 기록:

- source
- market
- started_at
- finished_at
- status
- fetched_count
- inserted_count
- error

대시보드에는 최근 실패를 표시한다.

품질 플래그:

- `source_unavailable`
- `api_key_missing`
- `rate_limited`
- `empty_result`
- `low_coverage`
- `summary_failed`
- `telegram_failed`

## 20. 보안/저작권 요구사항

- API 키와 토큰은 DB, 로그, 대시보드 응답에 노출하지 않는다.
- 기사 전문을 저장하거나 재게시하지 않는다.
- 저작권 있는 본문은 요약과 링크만 저장한다.
- 공식 공시/공공 API도 원문 전체 복제 대신 식별자와 링크 중심으로 저장한다.
- 로그에는 URL, 제목, source ID까지만 남긴다.

## 21. 테스트 요구사항

단위 테스트:

- DB 생성 및 마이그레이션
- `dedupe_key` 안정성
- 공식 원천 우선 중복 제거
- 중요도 점수 매핑
- 텔레그램 메시지 길이 제한
- HTML escaping
- dashboard API filter

권장 테스트 명령:

```powershell
python -m pytest tests/test_investment_news_store.py -q
python -m pytest tests/test_investment_news_dedupe.py -q
python -m pytest tests/test_investment_news_scoring.py -q
python -m pytest tests/test_investment_news_telegram.py -q
python -m pytest tests/test_dashboard_investment_news.py -q
python -m py_compile investment_news/*.py tools/investment_news_collect.py tools/investment_news_digest.py tools/investment_news_scheduler.py dashboard/dashboard_server.py
```

문서/한글 변경 시:

```powershell
python tools/check_mojibake.py --all --max-findings 20
```

## 22. 단계별 구현 계획

### Phase 1: 로컬 DB와 수동 수집

- `investment_news/models.py`
- `investment_news/store.py`
- `investment_news/dedupe.py`
- `investment_news/scoring.py`
- `tools/investment_news_collect.py`
- SQLite schema 생성
- mock collector 기반 테스트

완료 기준:

- CLI로 sample item을 수집하고 DB에 중복 없이 저장한다.
- 중요도/테마/종목 필터 조회가 가능하다.

### Phase 2: 텔레그램 다이제스트

- `investment_news/digest.py`
- `investment_news/telegram.py`
- `tools/investment_news_digest.py`
- `S/A` 요약 메시지 생성
- `INVEST_NEWS_TELEGRAM_ENABLED` 분리 제어

완료 기준:

- dry-run으로 메시지를 확인할 수 있다.
- 실제 발송은 환경변수로만 켜진다.
- 발송 성공 후 중복 발송이 방지된다.

### Phase 3: 웹 대시보드

- dashboard 읽기 전용 API
- 투자뉴스 탭 UI
- 필터/검색/원문 링크
- collector run 상태 표시

완료 기준:

- 대시보드에서 오늘 S/A 항목과 전체 이력을 조회할 수 있다.
- 자동매매 상태와 데이터가 섞이지 않는다.

### Phase 4: 공식 원천 연동

- DART
- SEC EDGAR
- Fed calendar/statements
- FRED/BLS/BEA 주요 지표
- KRX 지수/수급

완료 기준:

- 공식 원천 이벤트가 일반 뉴스보다 높은 신뢰도로 저장된다.
- 공식 source ID 기반 dedupe가 동작한다.

### Phase 5: 보조 뉴스 연동과 스케줄러

- Naver News API
- Alpha Vantage/Finnhub
- KIS 뉴스는 주문/브로커 기능과 분리된 조회만 허용
- scheduler loop
- Windows Task Scheduler 등록 스크립트는 선택

완료 기준:

- KR/US/GLOBAL 정기 수집이 가능하다.
- 수집 실패가 collector run에 기록된다.
- 텔레그램 정기 요약이 중복 없이 발송된다.

## 23. 완료 판정

초기 개발 완료 조건:

- 신규 기능은 자동매매 런타임을 import하지 않는다.
- SQLite DB에 링크 중심 뉴스 item이 저장된다.
- S/A/B/C 중요도와 reason이 저장된다.
- 텔레그램 dry-run과 실제 발송이 분리된다.
- 대시보드에서 필터링과 원문 링크 확인이 가능하다.
- 수집 실패/요약 실패/텔레그램 실패가 관측 가능하다.
- 관련 테스트와 py_compile이 통과한다.

## 24. PR/커밋 체크리스트

- 변경 파일 목록
- 자동매매 경로 미연결 확인
- DB schema 변경 내용
- 신규 환경변수
- 실행한 테스트
- 텔레그램 발송 dry-run 결과
- 대시보드 API 응답 예시
- 저작권/원문 저장 정책 준수 확인

## 25. 후속 검토 질문

- 초기 관심 종목 KR/US 목록
- 즉시 텔레그램 발송을 `S`만 할지 `A`까지 할지
- 매크로 지표 중 필수 항목
- 대시보드 첫 화면 우선순위
- LLM 요약을 초기에 켤지, rule-based summary부터 갈지
