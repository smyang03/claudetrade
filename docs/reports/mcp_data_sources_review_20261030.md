# 금융·주식·뉴스 MCP / 데이터 공급원 검토 (2026-10-29~30)

운영자 요청: "우리 시스템에 적용할 만한 금융·주식·뉴스 MCP 서치 → 놓친 것·실제 필요한 데이터(무료/유료) 상세 분석 → 무료 중 필요한 것 → 시스템 영향."

## 결론

> 10-30 검토 메모: MCP 설치는 실익이 작다(연구 병목은 조회 속도가 아니라 데이터 존재 여부). 쓸 만한 것은 korean-dart-mcp를 연구용 별도 키로 쓰는 경우뿐이다. 주문 기능 MCP 금지는 그대로 유지한다.

- MCP 자체는 엣지가 아니다. Claude 세션에 데이터 API를 도구로 붙이는 통로이고, 라이브 봇은 이미 Python으로 API를 직접 호출한다. **MCP의 쓸모는 연구 세션 속도**다. 봇 주문 경로에는 붙이지 않는다.
- 우리 문서의 데이터 공백 12개를 대조한 결과: **이미 있음 2 · 무료인데 미연결 5 · 유료로만 메움 3 · 공급원 못 찾음 2**.
- 가장 급한 것은 MCP가 아니라 **Q4 사전등록 KR 배당증가 가설의 데이터**(DART `alotMatter`·KIS 예탁원 배당일정, 둘 다 grep 0건)다. 12-31까지 수집률이 90%에 못 미치면 "검정 불가"다.
- 6개 무료 항목 모두 라이브 주문 경로에 영향이 없다. 수익 영향은 판정 결과로만 생긴다.
- 현재 자본 기준 월 기여는 **산출 불가**다. 우리 데이터로 잰 효과값이 없다.

---

## 1. MCP 후보 (1차 서치)

### A. 미국 공시·실적

| 서버 | 성격 | 키/비용 | 우리 용도 |
|---|---|---|---|
| danishashko/edgar-mcp | OSS. 10-K/10-Q/8-K, XBRL, 전문검색 | 키 없음 | T1-rev 매출 대조 점검, 공시 원문 확인 |
| sareegpt/edgartools-mcp | edgartools 기반. Form 4·8-K item 파싱 | 무료 | 대안 |
| Apify EDGAR 계열(seibs.co, jungle_synthesizer 등) | 호스팅. 8-K 분류, 실적 transcript | 1천 건당 $5 수준 | transcript 축이 필요할 때만 |

`sec.gov` 코드가 이미 15곳에 있다. 새 데이터가 아니라 조회 속도가 이득이다.

### B. 미국 시세·펀더멘털·실적

| 서버 | 공식 여부 | 키/비용 | 비고 |
|---|---|---|---|
| Alpha Vantage MCP | 공식(호스팅) | 키 보유. 무료 25회/일, 유료 $49.99/월~ | 실적·transcript·뉴스 감성. 소량 교차검증용 |
| FMP MCP(imbenrabi, 253+ 도구) | 커뮤니티 | 키 보유. 무료 250회/일(EOD) | 매출 서프라이즈. 라이선스 확인 필요(비상업 표기 사례). **추정치 개정 이력 없음** |
| Massive(구 Polygon) `mcp_massive` | 공식, MIT | 유료 | 틱·분봉 |
| EODHD MCP | 공식, 읽기 전용 도구 72개 | 유료 | 상폐 포함 글로벌 EOD |
| Finnhub | 공식 없음(커뮤니티 `financial-hub-mcp`) | 키 보유 | 이미 직접 연동 |
| FRED | 커뮤니티 | 키 보유 | 국면 게이트는 종결 축 |

### C. 한국

| 서버 | 성격 | 비고 |
|---|---|---|
| korean-dart-mcp(chrisryugj) | OpenDART API 83개 → 도구 15개 | 키 보유, 무료 |
| jjlabsio/korea-stock-mcp | DART + KRX | KRX 경로 약관 확인 필요 |
| ez2sarang/kstock-mcp | 읽기 전용. KIS 시세·잔고 + DART + IPO 균등배정 | KIS 키 공유 위험 |
| KIS 공식 MCP(open-trading-api/MCP) | ① Code Assistant(문서·샘플 검색) ② Trading(시세·계좌·**주문**) | ①만 권장, ②는 실계좌 금지 |

### D. 뉴스·감성

Finlight(공식, 감성·엔티티 태깅), MarketNewsAPI·Sell The News(목록 서버, 미검증). 뉴스 감성은 "예측축 8전8패"와 "PEAD prompt 노출 금지" 판정과 겹친다.

### E. 기관급 (Claude for Financial Services 커넥터)

S&P Capital IQ, FactSet, LSEG, Morningstar, PitchBook, Daloopa, Moody's, MSCI. 기업 구독이 필요하다. 현재 자본 규모에서는 제외.

### 1차 리포트 정정

| 1차 리포트 내용 | 실제 |
|---|---|
| "Unusual Whales MCP 확인 안 됨" | **공식 호스팅 MCP가 있다**(`api.unusualwhales.com/api/mcp`). 옵션 흐름, 다크풀, 공매도 잔고·FTD(`uw_shorts`), 의회 거래. 유료(플랜 예: 월 2.5만 요청), 가격 미확인 |
| "Tiingo MCP 확인 안 됨" | 커뮤니티 `tiingo-mcp`(PyPI, 도구 17개)가 있다. 무료 키로 사용 가능, 배당·분할은 유료 |
| Nasdaq Data Link 누락 | **공식 MCP가 있다**(도구 5개). Sharadar 같은 구독 데이터셋 조회 |

---

## 2. 공백별 분석 (우리 문서 기준)

근거 문서: `preregistration_q4_2026_20261022.md`, `data_inventory_for_models_20260927.md`, `model_research_program_20260927.md`, `T1kr_consensus_20260930.md`.
PIT 등급: A=시각 있음 · B=날짜만(T+1 시가 규칙) · C=조회 시점 스냅샷.

| # | 공백 | 걸린 가설·한계 | 실측 상태 | 최선 공급원 | 전달 방식 | 비용 | PIT | 약관 |
|---|---|---|---|---|---|---|---|---|
| 1 | KR 전년 DPS·배당 공시 | Q4 사전등록 KR 배당증가 | **미연결**(`alotMatter`·KIS 배당일정 grep 0) | DART `alotMatter.json` + `list.json`/`document.xml`, KIS 예탁원 배당일정(교차검증) | 직접 API, 연구 세션은 korean-dart-mcp | 무료(키 보유) | B | 깨끗 |
| 2 | KR 지수 정기변경 이력 | Q4 사전등록 KR 지수편입 | 수작업 CSV 예정 | **API·MCP 못 찾음.** KRX 보도자료 수작업 | 수작업 | 무료 | A | KRX 자동수집 금지 |
| 3 | US 분기 매출(SUE) | Q4 사전등록 T1-rev | 있음(게이트 일치율 I1 97.5%) — **10-30 정정: 일치율이지 커버리지가 아니다. 전체 I1 커버리지 73.8%, 국내 발행인 89.8%·외국 1.1%(10-Q 미제출, 무료 공급원 없음). 사전등록 개정 1 참조** | SEC companyfacts, 점검은 edgar-mcp `get_concept` | 직접 + MCP | 무료 | B+ (최초 filed) | 깨끗 |
| 4 | US eps_est 과거 시점값 | T1 한계 "eps_est 시점 불확실" | **forward는 수집 중**: `data/shadow/earnings_pit_ledger.jsonl` 7,379행, `earnings_calendar_snapshots/` 09-01~10-29 59일. **과거분 없음** | Estimize(Premium Plus $249/월, 히스토리 깊이 미확인), Zacks(가격 미확인). FMP는 개정 이력 없음 | 직접 | 유료 | A | 라이선스 |
| 5 | US 생존편향(상폐 가격) | T1 <$10 프리미엄 t 3.06→1.76, T1·T3·T4 한계 | 현재 상장 종목만 있음 | **Sharadar 직접 구독 $29(5년)/$49(10년)/$69(전체)/월** — 확인된 유일한 유료 가격. 상폐 가격·PIT 펀더멘털·내부자. Nasdaq Data Link 경로·Norgate·EODHD 가격 미확인 | 벌크/API(+Nasdaq Data Link MCP) | 유료 | A/B | 라이선스(1개월 구독 후 보관 가능 여부 미확인) |
| 6 | T1 동시호가 체결 전제 | T1 "동시호가 체결 전제" | 미검증 | 먼저 Alpaca SIP 1분봉 무료 백필(인벤토리 #5, 미실행). 경매 imbalance는 Databento(가격 미확인) | 직접 | 무료 → 유료 | A | 깨끗 |
| 7 | KR 분기 컨센서스 과거 PIT | T1-KR | 무료·약관 허용 소스 없음(09-30 실측) | FnGuide FnSpace(코인 과금: 일별 0.01·분기 1코인, 원화 미확인), DataGuide/QuantiWise | 직접 | 유료 | A | 라이선스 |
| 7b | 대체: KR 목표가 개정 | 미등록 가설 | KIS `invest-opinion` 2018-04~ PIT 백테스트 가능 판정(`T1kr_consensus_20260930.md`) | KIS | 직접 | 무료 | B | 깨끗 |
| 8 | KR 분기 재무제표 | 펀더멘털 축(수집기 0) | **미연결**(`fnlttSinglAcntAll` grep 0) | DART `fnlttSinglAcntAll`, korean-dart-mcp `get_financials`/`get_xbrl` | 직접 + MCP | 무료 | B | 깨끗 |
| 9 | US Form 4 | US 내부자 군집 후보 1 | **대부분 정상**: v2 `tools/us_form4_daily.py` → `data/insider/form4.db` 07-01~10-28 86일(done 83·no_index 3), 거래 73,624건. 구 `edgar_form4_daily.py`가 05-22에 멈춘 건(휴장일 403 처리 결함) v2로 대체됨. **남은 구멍 05-23~06-30** | SEC `form345` 2026q2 zip | 벌크 | 무료 | A- | 깨끗 |
| 10 | KR 수급 원본 | KR 기관축(기각), 회피 규칙 성격 | 원본 파일 없음, KRX Open API 키 401 | KRX Open API(종목별 투자자 EOD 제공 확인), 승인 신청 필요 | 직접 | 무료 | B | 승인 필요 |
| 11 | US 공매도 잔고·옵션·IV | 등록 가설 없음 | 없음(grep 0) | FINRA 격주 공매도 잔고(무료, 공표일 결합), Unusual Whales 공식 MCP(유료). ORATS 미검색 | 직접/MCP | 무료/유료 | B | 깨끗 |
| 12 | 뉴스 본문 | Jev 판정 = 추출기로만 유효 | 제목·스니펫뿐 | BigKinds(키 보유), Finlight(공식 MCP), Tiingo News | 직접/MCP | 무료~유료 | A | 각사 약관 |
| — | KR 과거 분봉·KR 상폐 가격 | 출구 시뮬, KR 생존편향 하한 3.3% | 전향 축적만, KIS 상폐 0행 | **못 찾음**(코스콤·DataGuide 가능성, 상품·가격 미확인) | — | — | — | — |

### 유료 판단 (자본 대비)

- **Sharadar $69/월(약 10만 원):** US 생존편향을 직접 메운다. T1(실운영 100만 원)·T3 판정의 최대 한계를 재검증한다. 상시 구독보다 1개월 재검정 방식이 비용에 맞는다(라이선스 확인 필요).
- **Estimize $249/월(약 34만 원):** T1 자본의 34%/월이다. forward PIT는 이미 무료로 쌓고 있어 비용이 맞지 않는다.
- **FnGuide FnSpace:** T1-KR 과거 검정의 유일한 경로다(forward만으로는 약 7년, 09-30 산출). 원화 견적이 먼저다.
- **Unusual Whales·Databento·기관급 커넥터:** 걸린 가설이 없거나 무료 경로가 먼저라 보류한다.

### MCP 설치 후보 (README 검증)

| 서버 | 도구 | 라이선스·요건 | 주의 |
|---|---|---|---|
| edgar-mcp(danishashko) | 6개: search_company, get_recent_filings, get_latest_filing, get_company_facts, get_concept, full_text_search | MIT, 키 없음, 최근 커밋 2026-08-06(v1.0.3), `npx -y edgar-mcp` | **`SEC_USER_AGENT` 미설정 시 개발자 이메일 UA로 요청.** 클라이언트 스로틀 없음(429 재시도 3회). Form 4 전용 도구 없음 |
| korean-dart-mcp(chrisryugj) | 15개: resolve_corp_code, search_disclosures, get_company, get_financials, download_document, get_xbrl, get_periodic_report, get_shareholders, get_executive_compensation, get_major_holdings, get_corporate_event, insider_signal, disclosure_anomaly, buffett_quality_snapshot, get_attachments | MIT, `DART_API_KEY`, Node 20.19+ | **DART 일 한도 2만 건을 봇 공시 레인(`kr_event_lane`)과 공유** → 연구용 별도 키 권장 |

### 금지 사항

- 주문 기능이 있는 MCP(KIS Trading MCP, Alpaca)는 실계좌에 연결하지 않는다. 프롬프트 인젝션(뉴스·공시 본문 속 지시문)으로 의도하지 않은 주문이 나갈 수 있다.
- 봇과 KIS 앱키를 공유하지 않는다(토큰 발급·호출 한도 충돌, 장중 KIS 시세 루프 금지와 같은 계열).
- 대부분의 MCP는 조회 시점 스냅샷을 준다. 백테스트에는 filed/rcept 시각이 있는 원천만 쓴다.
- 커뮤니티 서버는 코드를 검수하고, 버전을 고정하고, 라이선스를 확인한 뒤에 쓴다.

---

## 3. 무료이면서 필요한 것

| 순위 | 데이터 | 어디서 | 왜 | 상태 |
|---|---|---|---|---|
| 1 | KR 배당(전년 DPS·배당결정 공시) | DART `alotMatter.json` + 배당결정 공시 원문, KIS 예탁원 배당일정 | Q4 사전등록 KR 배당증가. 12-31까지 수집률 90% 미만이면 검정 불가 | 미연결, 키 보유 |
| 2 | US Form 4 2026q2 분기 zip | SEC `form345` | US 내부자 군집 표본 구멍 05-23~06-30 | 이 구간만 비어 있음 |
| 3 | US 1분봉 과거 | Alpaca SIP(15분 지연) | T1 "동시호가 체결" 전제를 개장 1분 거래량으로 검증 | 미실행 |
| 4 | KR 목표가·투자의견 이력 | KIS `invest-opinion` | 2018-04~ PIT. T1-KR 대체 가설(목표가 개정)을 과거 검정 | 가능 판정, 사전등록 전 |
| 5 | KR 분기 재무제표 | DART `fnlttSinglAcntAll` | 펀더멘털 수집기 0. 배당증가 보조 변수 | 미연결 |
| 6 | 연구용 MCP 2종 | edgar-mcp, korean-dart-mcp | 연구 세션 조회 속도 | 미설치 |

무료지만 우선순위가 낮은 것: KRX Open API(승인 필요, 수급 축 기각), FINRA 공매도 잔고(걸린 가설 없음), BigKinds·Tiingo 뉴스(추출기 용도), US 예상 EPS 스냅샷(09-01부터 이미 수집 중).

---

## 4. 적용 시 시스템 영향

| # | 들어가면 | 수익 경로 | 안 넣으면 | 운영 부하·위험 |
|---|---|---|---|---|
| 1 KR 배당 | Q4 KR 배당증가 판정 가능 | 통과하면 KR 신규 레인 후보(쉐도우 → 승인 → 실주문). 기각되면 슬롯을 다음 가설에 씀 | 12-31 "검정 불가" → Q4 슬롯 1개 버림 | DART 일 한도 2만을 공시 레인과 공유. 2015~ 백필은 여러 날, 장 마감 후·야간에 |
| 2 Form 4 zip | US 내부자 군집 표본 구멍 해소 | US 유일 신규 후보 판정의 연속성 | 6주 영구 결측, 판정에 한계 표기 | 분기 zip 1개. 부하 거의 없음 |
| 3 Alpaca 1분봉 | **실운영 T1(100만 원)**의 체결 전제 검증 | 거래량 부족이면 +1.50%/건이 과대 → 유니버스 축소로 실자금 보호. 충분하면 확대 근거 | 실제 체결이 백테스트와 어긋나도 원인을 가를 수 없음 | 1,500종목 × 수년 → 디스크·백필 시간 큼. 장외 실행 |
| 4 KIS 목표가 | 목표가 개정 가설의 과거 검정 | 통과하면 KR 후보 추가(사전등록 먼저) | 막힌 T1-KR의 KR 실적 계열 대안 없음 | **봇과 같은 KIS 키.** 16:05 이후, 캐시 토큰만 재사용하고 새로 발급 금지(09-30 probe 규칙) |
| 5 KR 재무 | 펀더멘털 변수 기반 | 직접 효과 없음. 1번 보조, 이후 가설 재료 | 펀더멘털 축 공백 지속 | DART 한도를 1번과 공유 |
| 6 MCP 2종 | 연구 세션에서 공시·재무 즉시 확인 | 간접(검증 속도) | 매번 스크립트 | SEC UA 미설정 시 타인 이메일 UA. DART 키 공유 시 한도 다툼 |

- 실주문·리스크·출구 계약: 변화 없음. 6개 모두 수집기·원장·연구 경로다.
- PC(16GB): 수집기는 가볍다. 3번만 디스크·시간이 크고, 모두 Windows 예약 작업으로 장외에 실행한다.
- 수익 관점 순서: **1(마감) → 3(실자금) → 2 → 4 → 5 → 6.**
- 1·2·3·5는 수집기 구현, 6은 `.mcp.json` 변경이라 운영자 지시 후 진행한다. KRX 승인 신청·유료 구독은 운영자 결정이다.

---

## Sources

- [DEV: 7 Best MCP Servers for Stock Market Data (2026)](https://dev.to/kevin_menesesgonzlez/the-7-best-mcp-servers-for-stock-market-data-2026-389l)
- [MarketXLS: 7 Best Financial Data MCP Servers](https://marketxls.com/blog/best-financial-data-mcp-servers-ai-market-data)
- [Quiver: Best MCP Servers for Stock Data in 2026](https://www.quiverquant.com/news/Best+MCP+Servers+for+Stock+Data+in+2026)
- [Lambda Finance: 11 Servers Ranked](https://www.lambdafin.com/articles/mcp-server-stock-market-data)
- [koreainvestment/open-trading-api MCP README](https://github.com/koreainvestment/open-trading-api/blob/main/MCP/README.MD)
- [GeekNews: KIS MCP 논의](https://news.hada.io/topic?id=23084)
- [ez2sarang/kstock-mcp](https://github.com/ez2sarang/kstock-mcp) · [jjlabsio/korea-stock-mcp](https://github.com/jjlabsio/korea-stock-mcp) · [awesome-mcp-korea](https://github.com/darjeeling/awesome-mcp-korea)
- [danishashko/edgar-mcp](https://github.com/danishashko/edgar-mcp) · [chrisryugj/korean-dart-mcp](https://github.com/chrisryugj/korean-dart-mcp) · [sareegpt/edgartools-mcp](https://github.com/sareegpt/edgartools-mcp)
- [Apify SEC EDGAR Intel](https://apify.com/seibs.co/sec-edgar-intel/api/mcp)
- [Alpaca official MCP](https://mcp.so/server/alpaca's-official-mcp-server/alpacahq) · [Massive MCP](https://glama.ai/mcp/servers/massive-com/mcp_massive/schema) · [EODHD MCP](https://eodhd.com/financial-apis/mcp-server-for-financial-data)
- [FMP MCP (imbenrabi)](https://glama.ai/mcp/servers/@imbenrabi/Financial-Modeling-Prep-MCP-Server) · [financial-hub-mcp](https://www.pkgstats.com/pkg:financial-hub-mcp)
- [Finlight MCP](https://github.com/callbk/finlight-mcp) · [MarketNewsAPI MCP](https://glama.ai/mcp/connectors/com.marketnewsapi/market-news)
- [Unusual Whales 공식 MCP](https://unusualwhales.com/public-api/mcp) · [GitHub](https://github.com/unusual-whales/unusual-whales-official-mcp)
- [tiingo-mcp (PyPI)](https://pypi.org/project/tiingo-mcp/) · [Nasdaq Data Link MCP](https://www.nasdaq.com/docs/data/nasdaq-data-link/mcp)
- [Anthropic: Advancing Claude for Financial Services](https://www.anthropic.com/news/advancing-claude-for-financial-services) · [Agents for financial services](https://www.anthropic.com/news/finance-agents)
- [Sharadar](https://sharadar.com/) · [Sharadar 대안 가격](https://tradevodata.com/alternatives/sharadar) · [QuantRocket Sharadar pricing](https://www.quantrocket.com/pricing/data/sharadar/)
- [Norgate 생존편향 DB](https://concretumgroup.com/how-to-construct-a-survivorship-bias-free-database-in-norgate-using-python/)
- [Estimize API](https://www.estimize.com/api_docs/v1) · [Estimize 기관용](https://estimize.com/sales) · [Zacks 컨센서스](https://zacksdata.com/datasets/consensus-data/)
- [FMP 추정치 개정 이력 없음](https://site.financialmodelingprep.com/education/calendar/build-an-earnings-revision-pressure-signal-estimate-drift-monitor)
- [Databento imbalance 스키마](https://databento.com/docs/schemas/imbalance) · [NYSE imbalance](https://databento.com/blog/NYSE-imbalance-feeds)
- [KRX 데이터 분배상품](https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA002.jsp)
- [FnGuide 스마트컨센서스](https://corp.fnguide.com/fng/serviceinfo_quantiwiseConsensus) · [FnSpace 데이터마트](https://www.fnspace.com/DataMart/MartList)
- [KIS Developers](https://apiportal.koreainvestment.com/apiservice) · [KIS Go 패키지(estimate-perform)](https://pkg.go.dev/github.com/kenshin579/korea-investment-stock)
- [SEC EDGAR 접근 규정](https://www.sec.gov/os/accessing-edgar-data)
