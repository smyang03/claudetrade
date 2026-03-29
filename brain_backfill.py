"""
brain_backfill.py — 기존 daily_judgment JSON으로 brain.json 재구성

실제 봇 운영 데이터(2026-03-xx)를 읽어 postmortem을 다시 실행하고
brain.json을 올바르게 채웁니다.

사용:
  python brain_backfill.py
  python brain_backfill.py --dry-run   # brain 업데이트 없이 결과만 출력
"""
import os, sys, json, argparse
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from minority_report.postmortem import run as run_postmortem

JUDGMENT_DIR = Path(__file__).parent / "logs" / "daily_judgment"

# 실제 봇 운영 파일 (2026-xx 전체 — Claude 분석가 판단이 있는 파일만 유효)
TARGET_FILES = sorted([
    f for f in JUDGMENT_DIR.glob("2026*.json")
])


def load_record(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(dry_run: bool = False):
    print(f"=== brain 백필 시작 (dry_run={dry_run}) ===")
    print(f"대상 파일: {len(TARGET_FILES)}개\n")

    for path in TARGET_FILES:
        rec = load_record(path)
        market   = rec.get("market", "KR")
        date_str = rec.get("date", path.stem[:8])
        judgments = rec.get("judgments", {})
        consensus = rec.get("consensus", {})
        actual    = rec.get("actual_result", {})
        digest    = rec.get("digest_prompt", "")
        trades    = rec.get("trades", [])

        # 판단이 없거나 actual_result 없으면 스킵
        if not judgments or not actual:
            print(f"  [SKIP] {path.name} — 판단 또는 실제결과 없음")
            continue

        # HALT 모드 스킵
        if consensus.get("mode") == "HALT":
            print(f"  [SKIP] {path.name} — HALT 모드")
            continue

        mode = consensus.get("mode", "?")
        pnl  = actual.get("pnl_pct", 0)
        n_t  = len(trades)
        print(f"  [{date_str} {market}] mode={mode}  pnl={pnl:+.2f}%  trades={n_t}")

        if dry_run:
            print(f"    → dry-run: postmortem 스킵")
            continue

        try:
            pm = run_postmortem(
                market      = market,
                date        = date_str,
                today_judgment = rec,
                actual_result  = actual,
                digest_prompt  = digest,
                trade_log      = trades,
            )
            print(f"    ✅ lesson: {pm.get('key_lesson','')[:60]}")
            print(f"       Bull:{pm.get('bull_result')}  Bear:{pm.get('bear_result')}  Neutral:{pm.get('neutral_result')}")
        except Exception as e:
            print(f"    ❌ 실패: {e}")

    if not dry_run:
        print("\n=== brain.json 최종 상태 ===")
        import claude_memory.brain as BrainDB
        brain = BrainDB.load()
        for mkt in ("KR", "US"):
            m = brain["markets"][mkt]
            print(f"\n[{mkt}]")
            print(f"  trained_days: {m['trained_days']}")
            print(f"  learned_lessons ({len(m['current_beliefs'].get('learned_lessons',[]))}개):")
            for l in m["current_beliefs"].get("learned_lessons", []):
                print(f"    • {l}")
            print(f"  market_regime: {m['current_beliefs'].get('market_regime','?')}")
            print(f"  correction_guide: {json.dumps(brain.get('correction_guide',{}).get(mkt,{}), ensure_ascii=False)[:120]}")
            print(f"  analyst_performance:")
            for a, p in m["analyst_performance"].items():
                print(f"    {a}: {p['rate']*100:.0f}% ({p['total']}일)")
            if m["recent_days"]:
                print(f"  recent_days 최신: {m['recent_days'][-1].get('date')} key_lesson={m['recent_days'][-1].get('key_lesson','')[:40]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
