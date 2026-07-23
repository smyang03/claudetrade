# judge 프롬프트 개선 토론 — Claude 관점 + 외부 사례 + 데이터 (2026-07-23)

운영자 지시: judge 답변이 매수를 결정하니 그 프롬프트를 다양한 형태로 개선하자.
외부 사례 + Claude(나) 관점 토론 + 데이터로.

## 0. ★ 즉시 고칠 버그 — "strong regime" 문구가 방금 한 국면 변경을 무력화한다

방금 BUY_READY 게이트에 CAUTIOUS/NEUTRAL을 추가했다. 그런데 프롬프트 guide는 여전히:
> "Use BUY_READY ... when a good candidate shows strong momentum **in a strong regime**"

입력엔 `market_regime: CAUTIOUS`가 온다. **CAUTIOUS는 이름부터 "strong regime"이 아니다.**
즉 게이트는 열어줬는데 프롬프트 문구가 "강한 국면에서만 사라"고 해서, 나(judge)는
CAUTIOUS/NEUTRAL에서 BUY_READY를 **여전히 안 고를** 것이다. 게이트와 프롬프트가 모순.

→ guide에서 "in a strong regime"을 빼거나 "even in cautious/neutral regimes when momentum
is strong"으로 바꿔야 국면 변경이 실제로 작동한다. **이게 #1 우선순위.**

## 1. Claude 관점 — 이 프롬프트가 나를 "덜 사게" 만드는 이유 7가지

나는 이 프롬프트를 받아 판정하는 모델이다. 무엇이 나를 WAIT 쪽으로 미는가:

1. **유일한 예시가 PULLBACK_WAIT다.** JSON schema 예시가 `"action":"PULLBACK_WAIT"` 하나뿐,
   buy_zone·structural_basis·zone_basis가 채워져 있다. one-shot 예시는 강한 앵커다 —
   BUY_READY가 허용돼도 예시가 나를 눌림 쪽으로 당긴다. **BUY_READY 예시가 없다.**

2. **경고문이 매수 유도문보다 압도적으로 많다.** "missing/stale면 WAIT" · "noisy면 WAIT" ·
   "faded면 REJECT" · "reward/risk weak면 WAIT" — WAIT/REJECT 사유 4+개 vs BUY_READY 1문장.
   프롬프트 전체 톤이 "조심할 이유가 많고, 살 옵션은 하나"다. 신중이 기본값이 된다.

3. **"strong regime" 모순** (§0).

4. **확신 등급이 없다(이분법).** BUY_READY냐 WAIT냐뿐. "약하게 사기"가 없어서, 애매하게
   좋은 셋업은 → WAIT로 떨어진다. 외부 시스템은 position sizing 단계로 이걸 푼다.

5. **기다림의 기회비용이 없다.** 매수 위험(chase·fade)은 나열되나, 안 샀을 때 놓치는
   러너의 비용은 프롬프트 어디에도 없다. 손실회피 프레이밍 → 과소매수.

6. **base-rate/볼록성 앵커가 없다.** "대부분 소폭승, 가끔 +17% 대박"이라는 구조를 안 주니,
   나는 각 건을 "이게 이길까?"(예측 불가)로 보게 된다. "좋은 비대칭 베팅인가?"(실제 게임)로
   보게 하려면 그 컨텍스트를 줘야 한다.

7. **"reward/risk weak → WAIT".** 데이터는 볼록성이 소수 대박에서 오고, 사전 reward/risk를
   요구하면 그 대박을 거른다(오늘 실측). 이 조항이 과확인의 핵심.

## 2. 외부 사례 — 다른 LLM 트레이딩 시스템

(QuantAgent, ATLAS, Multi-Agent REITs 등 arxiv)
- **다중 horizon 확신도**: up/down/side 방향 + 확신을 T+1/T+5/T+20 별로. 보유기간별 불확실성 표현.
- **CoT 구조화**: 뉴스반영→정보통합→확신도→최종결정 단계.
- **명시적 position sizing**: 고정액 스텝·단계적 진입·자산별 노출 상한.
- **강모멘텀 선호**: "Favour strong momentum and decisive price action." 과매수는 차단이 아니라
  손절조임·사이즈축소.
- **신호 일관성**: 여러 신호가 합치할 때 진입(단 우리 데이터는 과확인이 해로우니 이건 취사).

## 3. 개선 후보 (형태별) — 무엇/왜/테스트가능성/위험

| # | 개선 | 왜 | 코드테스트 | 위험 |
|---|---|---|---|---|
| A | **"strong regime" 문구 수정** | 게이트-프롬프트 모순 해소(§0). 국면변경 완성 | 빌드 diff 가능 | 낮음(문구) |
| B | **BUY_READY 예시를 schema에 추가/교체** | one-shot 앵커를 매수 쪽으로 | 빌드 diff 가능 | 낮음 |
| C | **확신도→사이즈 등급** (high=full, med=half) | 이분법 해소, 약한 셋업도 소액 매수. 약점 "안 고름" 직격 | 부분(스키마) · 효과는 API | 중(사이징은 리스크계층) |
| D | **볼록성 컨텍스트 1줄** ("most small, rare large; judge asymmetry not win-prob") | base-rate 앵커, 예측 아닌 비대칭으로 프레이밍 | 불가(API 효과) | 낮음(문구) |
| E | **경고문↔유도문 균형** (매수 유도 1→2, WAIT 사유 압축) | 신중 편향 완화 | 불가(API 효과) | 중(과매수 유발 가능) |
| F | **기회비용 1줄** ("waiting on a strong runner has real cost") | 손실회피 상쇄 | 불가 | 중 |
| G | **reward/risk 사전요구 완화**(강모멘텀엔 면제) | 과확인이 대박 거름(데이터) | 부분 | 중(운영자 승인 필요) |

## 4. 판정 — 무엇을 어떻게

**즉시(문구, 저위험, 게이트변경 완성):** A(strong regime 수정) + B(BUY_READY 예시).
이 둘은 방금 한 국면변경이 실제로 작동하게 하는 **완성 조각**이다. 없으면 게이트만 열고
프롬프트가 계속 눌림으로 끌어 매수가 안 는다.

**설계·검토(효과는 API로만 확증 가능):** C(확신도 사이징)·D(볼록성 앵커)·F(기회비용).
이들은 빌드 diff로 "무엇이 바뀌는지"는 코드로 보이나, "판정이 달라지는지"는 실제 judge
호출(라이브 관측)로만 확인된다. → 배포 후 세션 단위 BUY_READY 발동율·우리net 추적.

**운영자 판단:** G(reward/risk 완화)는 리스크 계약이라 별도 승인.

## 5. Claude의 한 줄 — 나를 잘 쓰려면

나는 "이게 이길까?"엔 답을 못 한다(예측 무효, 오늘 재확인). 하지만 **"이게 좋은 비대칭
베팅인가?"(작은 손절/큰 러너, 강모멘텀, 나쁜국면 회피)엔 답할 수 있다.** 현 프롬프트는
전자를 묻는 톤(reward/risk·확인·strong regime)이고, 후자를 묻게 바꾸면(볼록성 앵커·사이즈
등급·기회비용) 내 판정이 매수 쪽으로 열릴 여지가 크다. 단 이건 가설이고 라이브 관측이
확증한다 — 오늘 배운 "코드로 구조는 보되 효과는 실측" 그대로.

## 6. 다음 (제안)
A·B를 프롬프트에 반영(빌드 diff로 격리 확인 후) → 배포된 국면변경과 함께 관측.
C~F는 변형안을 문서화해두고 A·B 효과를 본 뒤 순차. 전부 우리net·세션단위로 판정.
