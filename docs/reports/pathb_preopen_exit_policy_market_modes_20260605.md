# PathB Preopen Exit Policy Market Modes - 2026-06-05

## 운영 결론

live 운영에서 PathB preopen exit policy의 source of truth는 `.env.live`가 아니라 `config/v2_start_config.json`의 `env_overrides`다.

현재 정책:

| Market | Key | Value | Meaning |
|---|---|---|---|
| US | `US_PATHB_PREOPEN_EXIT_POLICY_MODE` | `enforce` | US PathB 장전 얕은 stop을 개장 후 재확인으로 defer |
| KR | `KR_PATHB_PREOPEN_EXIT_POLICY_MODE` | `off` | KR PathB 장전 stop defer 비활성 |
| US fallback | `PATHB_PREOPEN_EXIT_POLICY_MODE` | `enforce` | market key가 없을 때 US에만 fallback |
| US expected | `PATHB_PREOPEN_EXIT_POLICY_EXPECTED_US` | `enforce` | preflight가 기대하는 US 정책 |
| KR expected | `PATHB_PREOPEN_EXIT_POLICY_EXPECTED_KR` | `off` | preflight가 기대하는 KR 정책 |

## .env.live 중복 금지

`.env.live`에는 market별 `US_PATHB_PREOPEN_EXIT_POLICY_MODE`, `KR_PATHB_PREOPEN_EXIT_POLICY_MODE` 값을 중복 추가하지 않는다.

이유:

- live 시작 시 `.env.live`를 먼저 로드한 뒤 `config/v2_start_config.json`의 `env_overrides`가 덮어쓴다.
- 같은 값을 두 파일에 두면 한쪽만 수정되는 drift/conflict가 생길 수 있다.
- market별 정책은 운영 승인값이므로 `config/v2_start_config.json` 한 곳에서 관리한다.

## 코드 해석 규칙

`runtime/pathb_runtime.py::_pathb_preopen_exit_policy_mode(market)`는 다음 순서로 mode를 결정한다.

1. `{MARKET}_PATHB_PREOPEN_EXIT_POLICY_MODE`가 있으면 그 값을 사용한다.
2. US는 market key가 없을 때만 `PATHB_PREOPEN_EXIT_POLICY_MODE`를 fallback으로 사용한다.
3. KR은 market key가 없으면 `off`로 처리한다.

따라서 현재 전역 `PATHB_PREOPEN_EXIT_POLICY_MODE=enforce`가 있어도 KR은 자동으로 enforce를 상속하지 않는다.

## 검증

`tools/live_preflight.py`는 `config.pathb_preopen_exit_policy` 체크에서 현재 effective mode, expected mode, source를 표시한다.

기대값:

- `effective_modes.US == enforce`
- `effective_modes.KR == off`
- `current_policy.US == enforce`
- `current_policy.KR == off`
- `source_of_truth == config/v2_start_config.json env_overrides for live mode`

KR을 향후 관찰하려면 별도 승인 후 아래 두 값을 함께 바꾼다.

```text
KR_PATHB_PREOPEN_EXIT_POLICY_MODE=shadow
PATHB_PREOPEN_EXIT_POLICY_EXPECTED_KR=shadow
```

`enforce`는 KR shadow 표본과 운영 승인 전까지 사용하지 않는다.
