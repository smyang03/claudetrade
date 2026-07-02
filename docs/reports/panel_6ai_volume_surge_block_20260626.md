# 6인 AI 패널 — volume_surge 차단 최종 방안 (2026-06-26)

> 대상: KR volume_surge 버킷 진입 차단의 최종 구현안(B/C·shadow/enforce·범위) 압박 검증.
> read-only 분석. 모든 주장 실측 앵커.

## 0. 공유 데이터
- KR volume_surge 실거래: **N=204, gross −1.15%, win32%, PF0.34, 합 −235%p** = KR 최대 단일 출혈.
- US volume_surge 실거래: **0건** → 글로벌 차단해도 US no-op(안전).
- 차단 시: KR 실현 net **−0.94% → −0.76%**(Δ+0.18%p, 204건 제거). **여전히 음수.**
- volume_surge→strategy "momentum"(near_breakout 등과 공유) + 거래 185/204가 strategy 빈값 → **strategy 차단 불가, bucket으로만**.
- KR 타 독성: `momentum`(별개) −3.75% N23 PF0.16, gap_pullback −0.63%.
- 후보 주입안: A(프롬프트 제외=Claude 선정 왜곡, 기각) / B(trade_ready→WATCH 강등) / C(buy-gate BUCKET_DENYLIST).

---

## 1차 — 최종안 테제
- **트레이너:** volume_surge=급등주 추격. win32%·PF0.34는 패턴 자체가 무엣지. 204건 중 살 만한 승자 거의 없음(win32%면 1/3만 +, 그것도 작음). **차단 정당.** 단 "급등 후 눌림"과 "급등 추격"이 같은 버킷에 섞였는지 확인 필요.
- **개발자:** B가 정답. trade_ready에서 갈라지니 Path A·PathB 한 곳 차단. 단 **bucket이 그 시점에 분류돼 있나**가 관건 — classify_candidate_bucket이 prompt 단계 후 실행되면 ticker→bucket 맵 적재 타이밍 검증해야. C는 안전망이나 PathB plan은 이미 등록됐다 주문서 막혀 낭비.
- **투자자(PM):** 204건=KR 거래의 큰 몫. 차단 후 **빈 슬롯을 뭐가 채우나?** KR 잔존 버킷도 다 음수(gap_pullback −0.63, ORP −0.32). 즉 **volume_surge 빼도 KR은 −0.76%**. 기회비용보다 출혈제거가 크면 OK지만, **KR을 살리는 게 아니라 덜 죽이는 것**.
- **리스크:** PF0.34는 구조적 손실=꼬리 제거 확실. −235%p는 census(부분표본 아님). **enforce 즉시 정당.** 단 버킷 재라벨 리스크(volume_surge가 near_breakout로 분류 바뀌면 우회) 모니터 필요.
- **회의론자:** 차단해도 **KR net 음수(−0.76%)**. 이건 "KR 스크리너 무엣지"의 한 증상일 뿐. volume_surge만 막는 건 **두더지잡기** — gap_pullback·ORP도 음수다. 진짜 질문: **KR 자체를 줄이거나 패시브로 가야 하지 않나?** 그리고 −235%p가 미래에도 반복된다는 보장은?
- **혁신가:** 이진 차단 말고 **size×0.3**으로 두면? 또는 **비-BULL에서만 차단**(BULL volume_surge는 다를 수). 아니면 volume_surge를 **shadow 신호로 전환**(막되 would-net 누적). 정보를 버리지 말자.

---

## 2차 — 반박·검증
- **개발자→혁신가:** "size×0.3"은 −1.15%를 −0.35%로 줄일 뿐 여전히 음수·자본 점유. **PF0.34에 부분배팅=여전히 −EV.** 차단이 맞음. "비-BULL만"은 표본 쪼개기(volume_surge×BULL N 소수)→ 통계 무의미. **그냥 막아라.**
- **회의론자→트레이너(강화):** "급등눌림 vs 추격 혼재" 의심이 핵심. 만약 volume_surge 안에 **소수 좋은 셋업**이 섞였다면 통째 차단은 그걸 버림. 단 — win32%·PF0.34면 섞인 좋은 것도 나쁜 것에 압도됨. **통째 차단의 손실 < 유지의 손실.** 차단 지지로 선회.
- **PM→회의론자(부분수용):** "KR 두더지잡기" 인정. 하지만 **−235%p는 단일 최대**라 한 번에 잡을 가치. "KR 패시브"는 더 큰 결정(운영자)이고, 그 전 단계로 **최대 출혈부터 막는 게 순서**. 두 개 안 충돌 안 함.
- **리스크→전원:** 재라벨 우회 차단법 = **차단을 bucket 단일이 아니라 "volume_surge 신호 자체"(vol_ratio 임계)로도 보강**. 단 1차는 bucket로 충분, 재라벨 빈도 모니터 후 보강.
- **트레이너 최종:** volume_surge는 KR에서 **급등 추격이 지배적**(KR 개미 특성). 통째 차단 OK. 단 **momentum(−3.75%) 버킷도 같이 후보** — 더 작지만 더 독함.
- **혁신가 철회:** size 부분배팅·비-BULL 분할 철회(표본·−EV). **shadow 1세션만 would-block 확인 후 enforce** 제안으로 후퇴(정보 보존 ≠ 거래 유지).

---

## 3차 — 결판: 최종 구현안

| 결정 | 최종 | 근거 |
|---|---|---|
| **차단 여부** | **차단(enforce)** | PF0.34·−235%p census, 부분배팅/비-BULL분할은 표본·−EV로 기각 |
| **범위** | **volume_surge (1차)**, momentum(−3.75%) 2차 후보 | 최대 출혈부터. 한 번에 하나 |
| **주입점** | **B(trade_ready→WATCH 강등)** | Path A·PathB 한 곳 차단·측정가능·선정 무왜곡. C는 안전망 옵션(필수 아님) |
| **시장** | 글로벌(US no-op) but **KR만 실효** | US 0건 |
| **롤아웃** | **shadow 1세션(월요일) → enforce** | KR 장 월요일, 주말 여유. would-block 로깅으로 bucket 분류 타이밍·재라벨 검증 후 켠다 |
| **선결 검증** | classify_candidate_bucket이 trade_ready 시점에 분류 완료인지 | B의 ticker→bucket 맵 신뢰성 |
| ❌ | A(프롬프트 제외)·size 부분배팅·비-BULL 분할·strategy 차단 | 선정왜곡·−EV·표본·버킷공유 |

### 만장일치 결론
**B안 + shadow 1세션 → enforce.** volume_surge는 KR 최대 출혈(−235%p, PF0.34)이고 부분배팅 여지 없음. trade_ready 강등이 가장 외과적. 단 **차단해도 KR은 −0.76%로 여전히 음수** — 이건 KR 스크리너 무엣지의 한 증상이라, volume_surge 차단은 **"덜 죽이기"이지 "살리기"가 아님**을 명확히 인지. KR 살리기는 별도(스크리너 역할축소·국면게이트·패시브 검토)로 간다.

### 냉정한 한 줄
가장 큰 출혈 구멍 하나를 막는 확실한 행동. 단 **KR 흑자 전환과 혼동 금지** — 구멍 막아도 배는 천천히 가라앉는 중(−0.76%). 다음은 KR 스크리너 무엣지 자체.
