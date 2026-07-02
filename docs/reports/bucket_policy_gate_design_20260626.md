# 버킷 정책 게이트 설계 (KR volume_surge 차단 + US momentum_now 축소) — 2026-06-26

> 목적: 측정으로 드러난 시장별 독성/약세 버킷을 ex-ante로 차단·축소하는 단일 메커니즘.
> KR volume_surge(차단)와 US momentum_now(축소-준비)를 한 게이트로 처리. 라이브 진입 경로.

## 1. 측정 근거 (실측, 비용후 net)
- **KR volume_surge**: N=204, net −1.15%, win32%, **PF0.34**, 합 −235%p = KR 최대 출혈. (양월 일관 독성.)
- **US momentum_now**: N=159, net −0.25%, win24%, PF0.81, 양월 −EV. **단 change_pct로 winner/loser 안 갈림**
  (3-5% −1.00 / 5-8% −0.73 / ≥8% −0.04) → **정밀 임계 없음, 통째 marginally −EV.**
- **US ORP(참고)**: +0.99% PF3.18 win67%, 양월 +EV. 단 공급 주3건 → 강제증량 불가.
- US momentum_now=급등(+7.9%) 고점근접 추격 → **이미 켠 fast_fill zone-cap·late-gate와 중복 가능.**

## 2. 설계 — 단일 "버킷 정책 게이트"
시장별 `{MKT}_BUCKET_POLICY` (버킷→정책 맵):
```
KR_BUCKET_POLICY = "volume_surge:block"          # block = 진입 차단(WATCH 강등)
US_BUCKET_POLICY = ""                            # 기본 off. 측정 후 "momentum_now:0.5" 후보
```
- `block` → trade_ready에서 해당 버킷 ticker를 WATCH로 강등(매수 자격 제거).
- `<float>` (예 0.5) → 진입 사이즈 ×mult (late-gate와 동형).
- 빈값 → 무동작.

### 주입점 (핵심 선결)
필요: 진입 결정 시점에 ticker→primary_bucket 해석. 현재 `selection_meta`엔 bucket 없음 →
**selection_meta 구성 시 `primary_buckets={ticker:bucket}` 적재**(후보 분류 완료분에서) 한 단계 추가.
그 후:
- **block**: `_new_buy_block_state` 또는 trade_ready 정규화에서 `_selection_meta_value(mkt,ticker,'primary_buckets')` 조회 → 정책 적용.
- **size mult**: PathB `_pathb_qty_with_context`에 bucket 전달 → 정책 mult 적용(late-gate와 합성).

### 선결 검증 (구현 전 필수)
- `classify_candidate_bucket`이 trade_ready 시점 전에 분류 완료인지(빈 bucket이면 게이트 무효).
- block 적용 시 PathB plan 등록 전에 걸리는지(등록 후면 plan만 낭비).

## 3. 롤아웃 (시장별 다름)
| 시장 | 버킷 | 정책 | 시점 |
|---|---|---|---|
| **KR** | volume_surge | **block** | shadow 1세션(월 개장) → enforce. (즉시 가치, PF0.34) |
| **US** | momentum_now | **0.5 (준비, 기본 off)** | **월요일~ late-gate·zone-cap 발효 후 momentum_now net 재측정** → 잔여 −EV 유의미하면 켠다 |

**US가 off인 이유**: momentum_now -EV가 이미 켠 게이트(zone-cap=고점추격 차단·late-gate=오후추격 축소)와 겹쳐, 따로 켜면 이중차감. **측정으로 잔여 −EV 확인 후에만 켠다.** 정밀 임계가 없어 통째 ×0.5(blunt)뿐이라 더 보수적.

## 4. 측정 게이트 (US momentum_now 켜는 조건)
월요일 이후 누적으로:
- momentum_now 실현 net이 **late-gate·zone-cap 적용 후에도** 유의하게 음수(예: net < −0.3%, N≥30) → `US_BUCKET_POLICY="momentum_now:0.5"` 켠다.
- 게이트 적용으로 momentum_now가 이미 breakeven 근처면 → 추가 조치 불필요(중복 회피).

## 5. 안 하는 것 (데이터 반증)
- momentum_now 정밀 셋업게이트(change_pct 임계) — winner/loser 안 갈림.
- ORP 강제증량 — 공급제약.
- momentum_now 통째 차단 — marginally −EV(PF0.81)라 차단은 과함, 축소가 적정.
- US near_breakout 손보기 — 봇이 풀51%→체결24%로 이미 보정.

## 6. 구현 순서 (월요일)
1. `primary_buckets` 맵을 selection_meta에 적재(공유 인프라).
2. KR volume_surge **block** 배선 → shadow 검증 → enforce.
3. US momentum_now net 재측정 → 조건 충족 시 **0.5** 켜기(같은 인프라, 토글만).
