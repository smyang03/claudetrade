# US Yahoo 수익 근거 탐색 결과 (2026-07-10)

## 결론

수익 근거를 찾을 방법은 있다. 기존 실패 원인은 데이터 부재가 아니라 서로 다른 목적의 DB를 한 모델에 섞고, 60분 라벨로만 미국 후보를 평가한 데 있었다.

이번 분석으로 5거래일 US swing lane에 반복적인 양의 평균과 PF가 확인됐지만, 세션 단위 block-bootstrap LCB는 아직 음수다. 따라서 `forward shadow challenger`로 개발할 근거는 충분하지만 `enforce` 근거는 아직 부족하다.

## 기존 US 데이터 감사

| 데이터 | US 행 | 세션 | 기간 | 핵심 용도 |
|---|---:|---:|---|---|
| candidate audit | 136,604 | 57 | 2026-04-20~07-09 | 최근 후보/Claude 입력 |
| counterfactual path | 182,930 | 36 | 2026-05-18~07-09 | 장중 30/60분 경로 |
| ticker selection | 14,138 | 69 | 2026-04-07~07-09 | 1/3/5일 forward |
| decisions | 62,679 | 483 | 2024-06-27~2026-07-09 | 장기 가설 생성 |
| actual learning ledger | 750 | 51 | 2026-04-27~07-09 | 실제 체결·비용·FX |
| Yahoo MFE backfill | 246 | - | 2026-06-26 sync | 실제 진입 경로 보조 |

최근 후보 DB의 `volume_ratio` 136,604행 중 136,595행이 1.0 placeholder였다. live decisions 46,974행 중 기술지표가 실제 채워진 행은 약 2,143행뿐이었다. 기존 장중 모델이 가격·거래량 분별력을 배우기 어려운 구조였다.

## Yahoo 원천 감사와 수집

기존 `data/price/us`:

- 파일 1,378개
- 총 458,251 일봉
- 후보 ticker 1,262개 전부 커버
- OHLC 오류 0행
- 기간 2024-07-11~2026-07-09

별도 research cache:

- `data/analysis/us_yahoo_2y`
- anchor ticker + SPY/QQQ/IWM 169개 전부 수집 성공
- 80,327 일봉
- 중앙값 501거래일
- production 가격 파일은 수정하지 않음

Yahoo intraday는 최근 60일 제한이 있으므로 장기 검증은 일봉, 60분 진입 경로는 최근 분봉으로 분리한다.

## point-in-time 클린 DB

도구: `tools/build_us_yahoo_point_in_time.py`

출력: `data/analysis/us_yahoo_point_in_time.db`

- 과거 decisions의 `(session_date,ticker)`만 anchor로 사용
- feature는 D일 종가까지 계산
- 진입은 다음 거래일 시가
- 청산은 1/3/5 거래일 종가
- `KRW=X` 변화를 곱해 KRW 수익으로 변환
- 비용 0.50% 차감
- live eligibility: 가격 ≥ $5, 20일 평균 dollar volume ≥ $15M, 당일 절대변동 ≤ 25%
- 최종 13,672행, 426세션, 128종목
- 기간 2024-07-23~2026-04-02

lookahead sentinel 테스트로 미래 가격을 바꿔도 과거 feature가 변하지 않는 것을 확인했다.

## 오염 검출

기존 DB에서 `SVRN` 3일 수익 +3,485%가 발견됐다. DB 가격 $0.205와 Yahoo repair 가격 $4.10 사이에 1:20 reverse split 기준 불일치가 있었다. 이를 통해 기존 forward 숫자를 그대로 모델 라벨로 쓰면 안 된다는 점을 확인했다.

## 클린 walk-forward

방법:

- expanding train
- horizon+2 세션 purge
- 다음 월 OOS test
- 회귀 예상 net rank + 성공확률 rank 결합
- 비용·환율 차감
- 일별 포트폴리오 수익에 5일 moving-block bootstrap

결과 요약:

| Horizon/lane | 평균 net | PF | block LCB | 판정 |
|---|---:|---:|---:|---|
| 1일 top3 | 음수 | <1 | 음수 | 폐기 |
| 3일 top3 | +0.282% | 1.13 | -0.488% | 연구 |
| 5일 top3 | 약 +1.0~1.3% | 1.39~1.49 | -0.27~-0.06% | 유력 challenger |
| 5일 top5 | 약 +0.89% | 1.42 | -0.17~-0.13% | 유력 challenger |

2026 test 구간의 5일 top5는 평균 +3.45%, PF 3.25, LCB +0.37%였지만 2025 안정성이 약했다. 전체 enforce 근거로 사용하지 않는다.

## 단순 규칙 외부 시간 검증

발견 규칙:

```text
QQQ 20일 momentum > 0
AND ticker relative strength vs QQQ 5일 > 0
AND ticker relative strength vs QQQ 20일 > 0
```

발견 기간에서는 평균 +0.888%, PF 1.77, LCB +0.165%였으나, 그 규칙을 찾을 때 사용하지 않았던 이전 107세션에서는 평균 -0.027%, PF 0.99, LCB -1.276%였다. 영구 규칙으로 채택하지 않는다.

24개 사전 제한 규칙을 앞 60% discovery에서 선택하고 뒤 40% untouched validation에 고정한 결과:

- frozen rule: `QQQ20>0 AND RS20>0`
- discovery 평균 +0.479%, PF 1.30
- validation 평균 +0.389%, PF 1.32
- validation LCB -0.405%

방향은 반복되지만 변동성 통제가 부족하다.

## 실제 체결 5일 counterfactual

도구: `tools/us_hold_counterfactual.py`

실제 US filled 270건 중 Yahoo 당일 고저와 실제 진입가가 일치하고 5일 forward가 성숙한 265건을 사용했다.

| 정책 | 평균 trade | 일별 PF | block LCB | 상위 3일 제외 |
|---|---:|---:|---:|---:|
| 5일 no-stop | +1.812% | 1.85 | -0.995% | +0.780% |
| 2.5% stop | +1.080% | 1.52 | -0.472% | -0.020% |
| 4% stop | +1.240% | 1.46 | -0.793% | -0.060% |
| 6% stop | +1.550% | 1.63 | -0.795% | +0.256% |

trade-level 평균은 강하지만 실제 세션이 41개뿐이고 날짜 군집 LCB는 음수다. 단순 hold5 enforce는 금지한다.

## 전략 설계 결론

현재 데이터가 지지하는 방향은 다음이다.

1. US 60분/1일 진입 확대가 아니라 5거래일 swing challenger를 별도 운영한다.
2. 후보 선택은 절대확률보다 cross-sectional rank를 사용한다.
3. QQQ/상대강도는 영구 hard gate가 아니라 모델 feature와 regime attribution으로 유지한다.
4. 5일 no-stop은 MAE가 크므로 실전 정책이 될 수 없다. 2.5% stop은 tail을 너무 많이 자른다.
5. 초기 shadow 정책은 top3~5, 최대 15~20 slot, 작은 고정 위험으로 기록한다.
6. 최소 60 forward 세션, 세션 block-LCB 양수, PF>1.2, ex-top3 양수, 2025/2026 양쪽 양수 전에는 enforce하지 않는다.

## 재현 명령

```powershell
python tools/collect_us_yahoo_research.py
python tools/build_us_yahoo_point_in_time.py --price-dir data/analysis/us_yahoo_2y
python tools/us_daily_alpha_walkforward.py --source yahoo --horizon 5
python tools/us_hold_counterfactual.py
```

