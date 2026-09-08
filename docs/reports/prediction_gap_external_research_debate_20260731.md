# 부족한 예측력을 외부는 무엇으로 정의하는가 — 서치 + 찬반 토론 리포트

작성일: 2026-07-31
성격: 외부 리서치 + 우리 실측 대조 + 찬반 토론. 코드·설정 변경 없음.
판정 기준: 우리 시스템의 실제 net (KR 왕복 0.21% / US 0.337~0.536% 실측)

## 0. 결론 먼저

외부 연구가 우리 문제를 다루는 방식은 셋으로 갈린다.

1. **예측 지평을 우리가 가진 것보다 짧게 잡는다** — 주문흐름 불균형(OFI)은 **5~30분** 구간에서 예측력이 실증됐고 60~120분에는 **부호가 역전**된다. 우리가 "30분에 승패가 갈린다"고 실측한 것과 지평이 정확히 겹친다. 그런데 그 신호의 원천인 bid/ask가 우리는 **커버리지 0%**다.
2. **LLM 에이전트가 단순 baseline을 못 이긴다는 벤치마크가 나왔다** — 특히 **하락 국면에서는 전부 실패**했다. 우리 구조가 정확히 그 대상이다.
3. **소액·고비용 계정에는 장중이 아니라 긴 타임프레임을 권한다** — 비용이 기대 이동폭 대비 작아지기 때문이다. 우리 US 왕복 0.34~0.54%는 이 문제의 교과서적 사례다.

가장 값어치 있는 후보는 **A(주문흐름)** 하나이며, 그것도 "새로 사 오는 정보"가 아니라 **이미 브로커가 주는데 저장하지 않는 데이터**다.

## 1. 우리 문제의 정확한 정의

외부와 비교하려면 우리 문제를 숫자로 고정해야 한다.

| 항목 | 실측값 | 출처 |
|---|---|---|
| 승패가 갈리는 시점 | 진입 후 **30분** (승자의 96.7%가 30분에 이미 양수) | 반사실 4,333건 |
| 진입 직후 하락 비율 | KR 53.2% / US 49.0%, 하락 시 종가도 음수 67~74% | 반사실 |
| 수익의 출처 | MFE 4%+ **55건이 +183.54%p** (전체의 22.5%) | 실거래 244건 |
| 최대 손실원 | MFE<1% **91건이 −163.73%p**, 승률 5.5% | 실거래 |
| 왕복 실비용 | US **0.337~0.536%** (수수료 0.50 + FX 0.20 + 슬리피지 −0.16~−0.36) | lifecycle_events 546건 |
| 진입 예측력 | KR 신호 없음(decile 무순서), US 랭킹 엣지 있으나 비용 밑 | 07-25 판정 |

**한 문장 요약:** 진입 후 30분이면 승패가 갈리는데, 진입 **전에는** 그것을 가릴 정보가 없고, 비용은 장중 이동폭의 5~7%를 먹는다.

## 2. 외부는 이 문제를 어떻게 정의하는가

### 2.1 주문흐름 불균형(OFI) — 지평이 우리와 겹친다

[Order imbalance 연구](https://discovery.researcher.life/article/do-order-imbalances-predict-intraday-returns-new-evidence-from-the-chinese-stock-market/a91a6c2e737630e8ac4d21c4c413644d)의 핵심 발견:

> 주문 불균형은 **5~30분** 수익률을 양(+)의 방향으로 예측하나, **60~120분에는 음(−)으로 역전**된다. 유동성이 과도하면 예측력이 억제된다.

[Cross-impact of order flow imbalance](https://www.tandfonline.com/doi/full/10.1080/14697688.2023.2236159)와 [ACM AI in Finance 2026](https://dl.acm.org/doi/10.1145/3768292.3770432)은 여기에 두 가지를 더한다:

- 단일 종목 OFI는 즉각적 움직임엔 충분하나, **수 초를 넘어서는 예측에는 교차자산 OFI가 정확도를 크게 올린다**
- 교차충격 항의 예측력은 **수 분 단위로 빠르게 감쇠**한다
- [거시 뉴스 발표](https://arxiv.org/abs/2508.06788) 시 가격충격은 커지고 흐름충격은 작아진다 — 즉 뉴스 구간에서 OFI 해석이 달라진다

### 2.2 LLM 에이전트 — 벤치마크가 나왔고 결과가 나쁘다

[StockBench (arXiv 2510.02209)](https://arxiv.org/abs/2510.02209):

> 금융 QA 벤치마크에서 강한 성능을 보임에도, **대부분의 LLM 에이전트는 누적수익·위험조정수익 양쪽에서 단순 baseline을 이기지 못한다.** 하락 구간에서는 **모든** LLM 에이전트가 패시브 baseline에 뒤졌고, 상승 구간에서만 대부분이 앞섰다.

반대 증거도 있다 — [TradingAgents](https://tradingagents-ai.github.io/)는 멀티에이전트 구조로 AAPL·GOOGL·AMZN에서 baseline을 일관되게 이겼다고 보고한다. [QuantAgents](https://arxiv.org/pdf/2510.04643), [QuantAgent](https://arxiv.org/pdf/2509.09995)도 유사하다.

**정리:** 단일 모델 판단은 실패, 멀티에이전트 구조는 일부 성공. 다만 성공 보고는 대부분 저자 자체 평가이고 종목 수가 3~5개로 적다.

### 2.3 소액·고비용 — 장중을 권하지 않는다

[소액 계좌 연구 정리](https://daytradingtoolkit.com/market-insights/day-trading-small-account-playbook):

> 성과 분산의 **30~60%가 거래비용에 묶여 있고**, 장중 엣지는 비용 후에 약해진다. 숨은 비용(넓은 스프레드, 데이터 요금, 슬리피지)이 나쁜 트레이드보다 먼저 계좌를 갉는다.
> **소액 계좌에는 긴 타임프레임 추세추종이 선호된다** — 노이즈·빈도·감정 압력이 줄고, 무엇보다 **기대 이동폭 대비 비용 비중이 작아진다.**

### 2.4 한국 개인투자자 실증 — 회전율이 성과를 먹는다

[개인투자자 성과 분석(KCI)](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART001156430):

> 개인 10,000명 6년 거래자료: 총수익률 연 12.3%, 시장 13.6%. **거래비용 반영 순수익률 연 8.3%.** 연 **270% 회전율**이 성과에 부정적으로 작용.

[국제 비교](https://www.finconsult.co.kr/2026/06/29/korean-retail-investors-participation-turnover-leverage-korean/)도 높은 회전율이 비용·세금·스프레드·시장충격·타이밍 오류를 통해 수익률을 낮춘다고 정리한다.

### 2.5 종목 선정 — 우리가 사는 종목은 권고 밴드 밖이다

[Tickeron](https://tickeron.com/trading-investing-101/what-are-the-guidelines-to-follow-when-selecting-stocks-for-intraday-trading/), [Lakshmishree](https://lakshmishree.com/blog/how-to-select-stocks-for-intraday-trading/), [arXiv 자산선택 연구](https://arxiv.org/pdf/2605.02326)의 공통 권고:

| 축 | 외부 권고 | **우리 실측** |
|---|---|---|
| 일중 변동폭 | **1.5~3%** | US **7.04%** / KR **16.89%** |
| 유동성 | 평균거래량 5M+, 스프레드 필터 | 스프레드 **값 0%** — 필터 자체가 없음 |
| 시총 | 초소형주 배제(슬리피지·특이변동성) | KR 최소분위 net −2.244%로 이미 확인 |

**우리는 권고 밴드의 2~5배 변동성 종목을 사고 있다.** 그리고 유동성 필터의 핵심 축(스프레드)이 비어 있다.

## 3. 후보군 4개 — 찬반 토론

### 후보 A. 주문흐름/호가 불균형 (OFI)

**찬성**
- 예측 지평(5~30분)이 우리 승패 결정 시점(30분)과 **정확히 겹친다.** 우연이 아니라 같은 미시구조 현상을 보는 것이다.
- **새로 살 데이터가 아니다.** KIS `get_kr_orderbook_snapshot`이 이미 있고 bid/ask·잔량·불균형까지 반환한다. US는 조회 함수만 만들면 된다.
- 스프레드가 채워지면 **비용 예측**도 가능해진다. 우리 최대 미해결 축이 비용인데 그 절반이 관측 밖이다.
- 07-25 판정이 "US true cost 실측"을 유일한 미해결 변수로 지목했고, 그 선행 조건이 바로 이 데이터다.

**반대**
- 연구가 말하는 OFI는 **틱/호가 레벨 고빈도 데이터** 기준이다. 우리는 사이클 주기가 분 단위이고 스냅샷도 2초 캐시다. **감쇠 속도(수 분)를 우리 주기가 못 따라간다.**
- 교차자산 OFI가 정확도를 크게 올린다지만, 우리는 단일 종목 스냅샷만 가능하다.
- 유동성이 과도하면 예측력이 억제된다 — 우리가 사는 US 대형주(META·AMD·ORCL)가 정확히 그 구간이다.
- 무엇보다 **OFI는 진입 신호가 아니라 우리에겐 관측 인프라**다. 이걸로 진입 예측을 만들려는 순간 07-25 데드엔드를 반복한다.

**판정: 채택 — 단 "예측 신호"가 아니라 "비용·유동성 관측"으로.** 진입 게이트로 쓰는 것은 금지. 값이 쌓인 뒤 별도 검증.

### 후보 B. LLM 멀티에이전트 강화

**찬성**
- TradingAgents 등은 멀티에이전트가 단일 모델을 이겼다고 보고한다. 우리는 이미 analyst·judge·hold advisor 구조가 있어 확장 비용이 낮다.
- 우리 Claude는 매도 판단에서 실제로 작동한다 — 오늘 AMD·ORCL을 35~36분 만에 정리했다.

**반대**
- **StockBench가 정면 반박한다.** 대부분의 LLM 에이전트가 buy-and-hold를 못 이기고, **하락 국면에서는 전부 실패**했다. 우리 KR/US 적자 구간이 정확히 그 국면이다.
- 성공 보고는 저자 자체 평가이고 종목 3~5개다. 우리 표본(KR 63 / US 253)보다도 작다.
- 우리 내부 판정이 이미 여러 번 같은 방향이었다 — confidence는 예측력 없음(06-29), 프롬프트 문구 주입은 통제 불가(07-01), 교훈 14일 무효과(07-03).
- 에이전트를 늘리면 **비용(토큰)과 지연이 늘어난다.** 우리 병목은 판단 품질이 아니라 30분 안에 나타나는 가격 경로다.

**판정: 기각.** 외부 근거가 갈리고, 우리 내부 판정은 일관되게 부정적이다. 재론하려면 StockBench류 벤치마크를 우리 원장으로 재현해 반증부터 해야 한다.

### 후보 C. 타임프레임 연장 (장중 → 스윙)

**찬성**
- 소액·고비용 연구가 **직접 권고하는 방향**이다. 비용이 기대 이동폭 대비 작아진다.
- 우리 숫자가 이를 지지한다 — 수익 전부가 **MFE 4%+ 55건(+183.54%p)**에서 나온다. 4% 움직임은 장중보다 멀티데이에서 잡기 쉽다.
- 한국 개인투자자 실증도 같은 방향이다(회전율 270%가 순수익을 12.3%→8.3%로 깎음).
- 이미 자산이 있다 — TSMOM sleeve, 코어 3종목(SCHG·275280·275300)이 장기 보유 구조로 돌고 있다.

**반대**
- 07-28에 **보유기간 축은 선택편향으로 판정**됐다. 멀티데이 보유 건은 "당일 손절을 피한 건"의 집합이라 보유기간 자체가 원인이 아니다.
- 트레일링 완화는 42개 조합이 **전부 악화**로 반증됐다(07-28).
- 오버나이트 리스크(갭 하락)가 새로 생기고, 이건 우리가 측정한 적이 없다.
- 우리 코어 sleeve는 이미 벤치마크 대비 열위로 실측됐다(07-22).

**판정: 조건부 보류.** 방향은 외부 근거가 가장 강하지만, 우리 내부 반증(07-28)이 정면으로 걸린다. **"보유기간을 늘린다"가 아니라 "애초에 멀티데이 이동폭을 노리는 별도 sleeve"로 재정의**해야 재론 가능하다. 기존 장중 경로의 홀드를 늘리는 것은 금지.

### 후보 D. 종목 선정 밴드 재정의

**찬성**
- 외부 권고(일중 1.5~3%)와 우리 실제(US 7.04% / KR 16.89%)의 괴리가 **2~5배**로 가장 크다.
- 우리 실측이 부분적으로 지지한다 — KR 최소분위 거래대금 net −2.244%, 저가주 −2.042%로 소형·저가주가 명확히 나쁘다.
- 비용 대비 이동폭 관점에서, 변동성이 너무 낮으면 비용을 못 넘고 너무 높으면 손실폭이 커진다. 밴드가 존재할 개연성이 있다.

**반대**
- **오늘 이미 반증됐다.** ATR 밴드를 조합에 넣어도 US net이 +0.095 → +0.085로 기여가 없었다. dud는 잘 예측하지만 상승·하락폭을 동시에 키워 net에서 상쇄된다.
- 외부 권고 1.5~3%는 **비용 구조가 다른 시장(인도·미국 소매 브로커)** 기준이다. 우리 US 왕복 0.34~0.54%에 그대로 이식할 근거가 없다.
- 우리 후보풀은 스크리너(most_actives, day_gainers)가 만든다. 밴드를 좁히면 후보가 급감해 진입이 사멸할 수 있다 — 07-01 "A1 매수 셧다운" 사고의 재발 경로다.

**판정: 기각 (진입 게이트로서).** 단 **관측**으로는 유지 — 우리 후보의 변동성 분포가 권고 밴드 밖이라는 사실 자체는 기록할 가치가 있다.

## 4. 종합 판정

| 후보 | 외부 근거 | 우리 실측 | 판정 |
|---|---|---|---|
| **A. OFI/호가** | 강함 (5~30분 실증, 지평 일치) | 데이터 자체가 0% | **채택 — 관측 인프라로만** |
| B. LLM 멀티에이전트 | 갈림 (StockBench 부정) | 내부 판정 일관 부정 | **기각** |
| C. 타임프레임 연장 | 강함 (소액·고비용 권고) | 07-28 선택편향 반증 | **조건부 보류 — 별도 sleeve로만 재정의 가능** |
| D. 선정 밴드 | 중간 | 오늘 ATR 반증 | **기각 (게이트) / 관측 유지** |

**핵심 통찰 하나:** 서치 결과 중 우리에게 실제로 새로운 정보는 **하나도 없었다.** OFI는 브로커가 이미 주는 데이터이고, LLM 한계는 우리가 이미 실측했고, 타임프레임·회전율 문제는 우리 비용 구조가 이미 말하고 있었다. **부족한 것은 정보가 아니라 이미 들어온 데이터를 저장·연결하는 배선이다.** 이건 오늘 하루에만 네 번 확인됐다 — `spread_bps` 값 0, `rel_vol_shadow` 미전파, `US_VOL_RATIO_FROM_REL_VOL` 미설정, `weak_mfe` shadow 미기록.

## 5. 권고 순서

1. **bid/ask 수집 → `spread_bps` 채우기** (후보 A). KR은 `get_kr_orderbook_snapshot` 존재, US는 조회 함수 신규. **진입 게이트로 쓰지 않는다.**
2. 값이 3주 쌓이면 → 스프레드·불균형과 우리 30분 경로의 상관을 실측. 이때 비로소 "OFI가 우리에게도 유효한가"를 답할 수 있다.
3. 동시에 US true cost를 스프레드 포함으로 재산출. 07-25가 지목한 미해결 변수가 그때 완전히 닫힌다.
4. C(타임프레임)는 기존 장중 경로를 건드리지 말고, **별도 sleeve 설계 안건**으로만 올린다.

## 6. 한계

1. 웹 검색 결과는 초록·요약 수준이며 원논문 전문 검증을 하지 않았다. 특히 LLM 에이전트 성공 보고는 저자 자체 평가다.
2. OFI 연구는 대부분 미국·중국 시장의 틱 데이터 기준이다. 우리 분 단위 스냅샷에 그대로 적용되는지는 **미검증**이다.
3. 외부 권고 수치(변동성 1.5~3%, 거래량 5M+)는 비용 구조가 다른 환경 기준이라 임계값을 이식해서는 안 된다.
4. 이 리포트는 방향 판정이며, 어떤 항목도 우리 원장으로 검증되기 전에는 enforce 대상이 아니다.

## 참고 자료

- [StockBench: Can LLM Agents Trade Stocks Profitably In Real-world Markets? (arXiv 2510.02209)](https://arxiv.org/abs/2510.02209)
- [Returns and Order Flow Imbalances: Intraday Dynamics and Macroeconomic News Effects (arXiv 2508.06788)](https://arxiv.org/abs/2508.06788)
- [Cross-impact of order flow imbalance in equity markets (Quantitative Finance)](https://www.tandfonline.com/doi/full/10.1080/14697688.2023.2236159)
- [From Constituents to Index: Interpretable Price Movement Prediction via Cross-Asset Order Flow (ACM ICAIF 2026)](https://dl.acm.org/doi/10.1145/3768292.3770432)
- [Do order imbalances predict intraday returns?](https://discovery.researcher.life/article/do-order-imbalances-predict-intraday-returns-new-evidence-from-the-chinese-stock-market/a91a6c2e737630e8ac4d21c4c413644d)
- [TradingAgents: Multi-Agents LLM Financial Trading Framework](https://tradingagents-ai.github.io/)
- [QuantAgents: Towards Multi-agent Financial System via Simulated Trading (arXiv 2510.04643)](https://arxiv.org/pdf/2510.04643)
- [Day Trading a Small Account: The Post-PDT Playbook](https://daytradingtoolkit.com/market-insights/day-trading-small-account-playbook)
- [개인투자자의 주식투자 성과 분석 (KCI)](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART001156430)
- [한국 개인투자자는 정말 과도한가? 참여율·거래회전율·레버리지 국제 비교](https://www.finconsult.co.kr/2026/06/29/korean-retail-investors-participation-turnover-leverage-korean/)
- [Intraday Trading Stock Selection: Liquidity, Volatility (Tickeron)](https://tickeron.com/trading-investing-101/what-are-the-guidelines-to-follow-when-selecting-stocks-for-intraday-trading/)
- [Large-Scale Asset Selection via Metric Dependence with Enriched High Frequency Information (arXiv 2605.02326)](https://arxiv.org/pdf/2605.02326)
- [awesome-quant-ai (GitHub)](https://github.com/leoncuhk/awesome-quant-ai)
- [Machine Learning for Trading — Alpha Factor Research](https://stefan-jansen.github.io/machine-learning-for-trading/04_alpha_factor_research/)
