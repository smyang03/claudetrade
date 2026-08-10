# 라이브 스택 수동 복구 런북 (2026-08-05, P2)

수익이 소수의 급락 달에 몰리는 전략에서 가동률은 엣지 보존 문제다.
이 문서의 모든 명령은 2026-08-04~05 실제 장애에서 검증된 것이다.

## 0. 상태 한눈에

```powershell
# 스택 프로세스 5종 (trading_bot / live_guardian / broker_truth / market_context / integrity)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'trading_bot|guardian|broker_truth|refresh_market|integrity' } |
  Select-Object ProcessId, CreationDate, CommandLine
```
```bash
python tools/integrity_check.py            # 정합성 일괄 (신선도 게이트·계약 위반 포함)
tail -20 logs/system/live_trading_$(date +%Y%m%d).log
```

## 1. 봇이 죽었고 안 올라온다

가디언(watch, 300초 주기)이 자동 재기동한다. 5분 넘게 안 올라오면:

```bash
python tools/live_guardian.py --mode live --ensure-bot --skip-dashboard
```

- `gate=BLOCK_START`여도 **broker stale만이 사유면 기동된다**(2026-08-05 예외 로직).
  리포트에 `bot_launch_stale_exception` 액션이 찍히는지 확인.
- `[중복 실행 차단]` 에러는 정상 방어다 — 이미 봇이 살아 있다는 뜻.
- 기동 후 확인: `logs/config/` 최신 effective-config 생성 + `API Health Check result OK=11`.

## 2. 브로커 스냅샷 stale로 게이트가 잠겼다 (장 사이 시간대)

증상: `broker_truth.*_stale_state` hard_fail, 게이트 BLOCK_START.
원인: 스케줄러는 개장 20분 전(--preopen-min 20)부터만 갱신한다 — 장 밖 stale은 정상.
봇 기동은 stale 예외로 뚫리지만, 즉시 갱신이 필요하면:

```bash
# 기존 스케줄러가 락을 잡고 있으면 그대로 두고 --once는 실패한다(정상).
# 스케줄러가 죽어 있을 때만:
python tools/broker_truth_scheduler.py --mode live --markets KR,US --once --force --json
```

주의(08-04 실수 기록): 스케줄러가 `outside_refresh_window`로 조용한 것은 멈춘 게
아니다. 죽었는지 확인 후에만 재기동한다.

## 3. WS 틱이 죽었다

증상: `[WS silence] {시장} 마지막 틱 N초 전` 경고(10분 주기).
**조치 불요** — 청산 판정은 5분 주기 `[holding price refresh]`가 이어받는다
(08-05 KR WS 사망 실전 방어 확인). 재연결을 원하면 봇 재시작(§1)뿐이다.
경고가 뜨는데 `[holding price refresh]`도 안 찍히면 §1로.

## 4. 봇 재시작 안전 창

- KR 장중(09:00~15:30) · US 장중(22:30~05:00) 재시작은 피한다(포지션 보호 공백).
- 안전 창: **15:35~22:20** (KR 마감 후 ~ US 개장 전), 05:05~08:45.
- 재시작 체크리스트: 종료 → 가디언 단발(§1) → effective-config 실측
  (`mcp get_effective_config` 또는 `logs/config/` 최신 파일) → 첫 사이클에서
  `[holding price refresh]` 로그 확인.

## 4-1. env(.env.live / start-config)를 바꿨다 — 재시작 대상 판정

**규칙: 재시작 대상 = 그 env를 읽는 프로세스 + 그것을 스폰하는 부모 전부.**

2026-08-10 실측 사고: 절대 허들 폐지(C안) 후 봇은 재시작했지만, shadow runner를
스폰하는 `preopen_scheduler`(08-06부터 상주)가 옛 env를 들고 있어 그날 밤 shadow가
구 계약으로 기록됐다 — 판정 코호트가 조용히 실주문과 갈라졌다. 코드 변경은 서브프로세스가
매번 새로 읽지만, **env는 부모 프로세스의 환경을 상속**한다.

env 변경 후 재시작 체크리스트:
- [ ] `trading_bot.py --live` (봇 본체)
- [ ] `tools/preopen_scheduler.py` (US swing runner·KR 스캔 스폰 부모)
- [ ] 그 env를 읽는 다른 상주 사이드카(broker_truth_scheduler 등, 해당 키를 쓸 때만)
- [ ] 확인: `python tools/integrity_check.py | grep drift` → 계약 지문 일치(FAIL이면 미반영)

```powershell
# preopen_scheduler 재기동 (종료 후 새 env로)
Stop-Process -Id <PID> -Force -Confirm:$false
Start-Process "C:\Users\Unknown\anaconda3\envs\upbit\python.exe" `
  -ArgumentList @('tools\preopen_scheduler.py','--mode','live','--markets','KR,US','--loop','--interval-sec','60') `
  -WorkingDirectory "E:\code\claudetrade" -WindowStyle Hidden
```

## 5. 사이드카가 구코드로 돈다 (코드 배포 후)

봇만 재시작하면 가디언·integrity_check는 구코드다. 각각 종료하면 watchdog이
새 코드로 되살리거나, 직접:

```powershell
Start-Process "C:\Users\Unknown\anaconda3\envs\upbit\python.exe" `
  -ArgumentList @('tools\live_guardian.py','--mode','live','--watch','--ensure-bot','--interval-sec','300','--telegram-alert') `
  -WorkingDirectory "E:\code\claudetrade" -WindowStyle Hidden
```
(integrity_check / refresh_market_context_daily 도 동일 패턴, 인자만 교체)

## 6. 급락 달(수확기) 대비 점검 — 월 1회

- [ ] `python tools/integrity_check.py` FAIL 0 확인
- [ ] 텔레그램 알림 실수신 확인 (`[commander] 폴링 오류`는 무해, 발신이 되는지 볼 것)
- [ ] 디스크 여유·윈도우 업데이트 예약 확인 (강제 재부팅이 US 장중에 오지 않게)
- [ ] 이 런북대로 봇 1회 재시작 리허설 (안전 창에서)

## 알려진 무해 로그

- `[commander] 폴링 오류: ... Read timed out` — 텔레그램 폴링, 무해
- `[중복 실행 차단]` — PID lock 정상 방어
- `[WS silence]` — §3, 안전망 가동 중 표시
- `[sleeve TP] ...` 10분마다 반복 — 주문 보류 중 트리거 유지 표시(스로틀 정상)
