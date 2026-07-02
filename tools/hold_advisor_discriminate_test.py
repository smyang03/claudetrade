"""prior 변별력 검증 (API): APLD(나쁜 멀티데이 HOLD→-4.94%) vs MSFT(좋은 HOLD→+9.14%).

prior OFF/ON system으로 각 종목 HOLD 케이스를 재판단해, prior가
- APLD는 SELL 쪽으로(나쁜 거 가림), MSFT는 HOLD 유지(좋은 거 안 자름) 하는지 측정.
변별력 = (APLD SELL율) - (MSFT SELL율). 높을수록 prior가 좋/나쁨을 가린다.
"""
from __future__ import annotations
import glob, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for line in open(".env.live", encoding="utf-8", errors="ignore"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

os.environ["HOLD_ADVISOR_PROFIT_GUARD_ENABLED"] = "false"  # import 시 prior 제외(=OFF baseline)
import minority_report.hold_advisor as h  # noqa: E402
from minority_report.claude_utils import response_text, thinking_extra_body  # noqa: E402

SYS_OFF = h._HOLD_ADVISOR_SYSTEM
SYS_ON = SYS_OFF + h._PROFIT_GUARD_PRIOR
client = h.client
MODEL = "claude-sonnet-4-6"
_RX_T = re.compile(r"종목:\s*(\S+)\s*\(")
_RX_P = re.compile(r"수익률:\s*([-+]?[\d.]+)%")
LIMIT = 15


def collect(ticker):
    out = []
    for f in sorted(glob.glob("logs/raw_calls/*hold_advisor_neutral*.json")):
        try: d = json.load(open(f, encoding="utf-8"))
        except: continue
        pr = d.get("prompt", "") or ""
        mt = _RX_T.search(pr)
        if not mt or mt.group(1).upper() != ticker: continue
        if str((d.get("parsed") or {}).get("action", "")).upper() != "HOLD": continue
        mp = _RX_P.search(pr)
        out.append({"prompt": pr, "pnl": mp.group(1) if mp else "?"})
        if len(out) >= LIMIT: break
    return out


def judge(prompt, system):
    try:
        r = client.messages.create(model=MODEL, max_tokens=600,
            system=[{"type": "text", "text": system}],
            messages=[{"role": "user", "content": prompt}],
            extra_body=thinking_extra_body("hold_advisor_discriminate_test"))
        m = re.search(r'"action"\s*:\s*"(HOLD|SELL)"', response_text(r))
        return m.group(1) if m else "?"
    except Exception as e:
        return f"ERR"


def main():
    for tk, tag in [("APLD", "나쁨 -4.94%"), ("MSFT", "좋음 +9.14%")]:
        cases = collect(tk)
        off = [judge(c["prompt"], SYS_OFF) for c in cases]
        on = [judge(c["prompt"], SYS_ON) for c in cases]
        n = len(cases)
        off_sell = off.count("SELL"); on_sell = on.count("SELL")
        print(f"=== {tk} ({tag}) n={n} ===")
        print(f"  prior OFF: SELL {off_sell}/{n} ({off_sell/n*100:.0f}%)")
        print(f"  prior ON : SELL {on_sell}/{n} ({on_sell/n*100:.0f}%)")
        globals()[f"{tk}_on_sell"] = on_sell / n * 100 if n else 0
    print(f"\n변별력 = APLD SELL율(ON) - MSFT SELL율(ON) = "
          f"{globals().get('APLD_on_sell',0) - globals().get('MSFT_on_sell',0):+.0f}%p")
    print("해석: 양수 클수록 prior가 나쁜(APLD)은 팔고 좋은(MSFT)은 들어 — 변별 작동.")


if __name__ == "__main__":
    main()
