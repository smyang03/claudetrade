"""샘플 데이터 생성 (대시보드 개발/테스트용)"""
import json, random
from datetime import datetime, timedelta
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
(LOG_DIR / "daily_judgment").mkdir(exist_ok=True)

def make_sample():
    random.seed(42)
    dates = []
    d = datetime(2026, 1, 2)
    while d <= datetime(2026, 3, 19):
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)

    records = []
    cumulative = 10_000_000
    for dt in dates:
        pnl = random.uniform(-1.5, 2.5)
        win = pnl > 0
        cumulative *= (1 + pnl/100)
        modes = ["AGGRESSIVE","MODERATE_BULL","CAUTIOUS","DEFENSIVE","HALT"]
        mode = random.choices(modes, weights=[5,40,25,20,10])[0]
        bull_hit = random.random() < 0.62
        bear_hit = random.random() < 0.41
        neut_hit = random.random() < 0.61

        reasons_bull = [
            "RSI 과매도 + 낙폭 과대로 반등 기대",
            "외국인 순매수 전환 + HBM 호재",
            "이평선 정배열 + 거래량 증가",
            "실적 서프라이즈 기대감",
            "글로벌 반도체 수요 회복 신호",
        ]
        reasons_bear = [
            "외국인 3일 연속 순매도 + 환율 급등",
            "관세 리스크 + FOMC 경계심",
            "거래량 급감 + 추세 약화",
            "공매도 잔고 증가",
            "글로벌 매크로 불확실성",
        ]
        reasons_neutral = [
            "기술적 지표 혼재, 방향성 불명확",
            "뉴스 재료 소화 중, 관망 필요",
            "지지/저항 구간 진입",
            "단기 과열 조정 후 재진입 대기",
        ]

        trades_count = random.randint(0, 4)
        trades = []
        for _ in range(trades_count):
            t_pnl = random.uniform(-3, 5)
            strategies = ["모멘텀","평균회귀","갭+눌림","변동성돌파"]
            tickers_kr = ["005930","000660","035420"]
            trades.append({
                "ticker": random.choice(tickers_kr),
                "strategy": random.choice(strategies),
                "side": "buy",
                "qty": random.randint(1, 10),
                "entry": random.randint(60000, 200000),
                "exit": 0,
                "pnl_pct": t_pnl,
                "pnl_krw": int(t_pnl * random.randint(300000, 500000) / 100),
                "reason": random.choice(["익절","손절","기간청산"]),
                "hold_min": random.randint(10, 300),
            })

        record = {
            "date": dt.strftime("%Y-%m-%d"),
            "market": "KR",
            "judgments": {
                "bull": {
                    "stance": "MODERATE_BULL" if bull_hit else "CAUTIOUS",
                    "confidence": round(random.uniform(0.5, 0.85), 2),
                    "key_reason": random.choice(reasons_bull),
                    "result": "HIT" if bull_hit else "MISS",
                },
                "bear": {
                    "stance": "DEFENSIVE" if bear_hit else "CAUTIOUS",
                    "confidence": round(random.uniform(0.45, 0.80), 2),
                    "key_reason": random.choice(reasons_bear),
                    "result": "HIT" if bear_hit else "MISS",
                },
                "neutral": {
                    "stance": "CAUTIOUS",
                    "confidence": round(random.uniform(0.50, 0.75), 2),
                    "key_reason": random.choice(reasons_neutral),
                    "result": "HIT" if neut_hit else "MISS",
                },
            },
            "consensus": {"mode": mode, "position_size": random.choice([20,40,60,70,100])},
            "actual_result": {
                "pnl_pct": round(pnl, 2),
                "pnl_krw": int(pnl * cumulative / 100),
                "win": win,
                "trades": trades_count,
                "cumulative": int(cumulative),
            },
            "postmortem": {
                "bull_why": "RSI 반등 신호 맞았으나 수급이 발목" if not bull_hit else "수급+기술 동시 적중",
                "bear_why": "관세 리스크 현실화" if bear_hit else "시장이 악재 선반영 완료",
                "key_lesson": random.choice([
                    "외국인 연속 순매도 시 Bear 우선",
                    "FOMC 전날은 포지션 축소",
                    "거래량 없는 상승은 신뢰도 낮음",
                    "공시 호재는 당일 모멘텀 유효",
                ]),
            },
            "trades": trades,
        }
        records.append(record)
        path = LOG_DIR / "daily_judgment" / f"{dt.strftime('%Y%m%d')}_KR.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

    print(f"✅ 샘플 데이터 {len(records)}일치 생성 완료")
    return records

if __name__ == "__main__":
    make_sample()
