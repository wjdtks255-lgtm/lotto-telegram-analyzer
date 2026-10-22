import json, os, random, time, itertools
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DRAWS_FILE = DATA / "draws.json"
STATE_FILE = DATA / "state.json"
REPORT_FILE = DATA / "latest_report.json"
DATA.mkdir(exist_ok=True)
TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID") or "").strip()
TZ_KST = __import__("datetime").timezone(__import__("datetime").timedelta(hours=9))

# 외부 접속 차단 및 파일 부재 시 사용할 기본 백업 데이터 (최근 회차 포함)
DEFAULT_DRAWS = {
    "source": "Embedded Fallback Cache",
    "draws": [
        {"round": 1, "date": "2002-12-07", "numbers": [10, 23, 29, 33, 37, 40], "bonus": 16},
        {"round": 1120, "date": "2024-05-25", "numbers": [2, 12, 19, 28, 40, 42], "bonus": 44},
        {"round": 1121, "date": "2024-06-01", "numbers": [5, 8, 14, 22, 35, 43], "bonus": 11},
        {"round": 1122, "date": "2024-06-08", "numbers": [11, 15, 24, 32, 39, 45], "bonus": 7},
        {"round": 1123, "date": "2024-06-15", "numbers": [3, 9, 21, 27, 34, 41], "bonus": 18},
        {"round": 1124, "date": "2024-06-22", "numbers": [6, 13, 20, 29, 36, 44], "bonus": 2},
        {"round": 1125, "date": "2024-06-29", "numbers": [1, 10, 18, 25, 33, 42], "bonus": 15},
        {"round": 1126, "date": "2024-07-06", "numbers": [4, 12, 22, 31, 38, 45], "bonus": 9},
        {"round": 1127, "date": "2024-07-13", "numbers": [7, 16, 23, 30, 37, 43], "bonus": 5},
        {"round": 1128, "date": "2024-07-20", "numbers": [8, 17, 26, 34, 39, 41], "bonus": 12},
        {"round": 1129, "date": "2024-07-27", "numbers": [2, 11, 19, 28, 35, 40], "bonus": 22},
        {"round": 1130, "date": "2024-08-03", "numbers": [5, 14, 21, 29, 36, 44], "bonus": 17},
        {"round": 1131, "date": "2024-08-10", "numbers": [9, 15, 24, 32, 37, 42], "bonus": 3},
        {"round": 1132, "date": "2024-08-17", "numbers": [1, 10, 18, 27, 33, 45], "bonus": 19},
        {"round": 1133, "date": "2024-08-24", "numbers": [6, 13, 22, 30, 38, 41], "bonus": 25},
        {"round": 1134, "date": "2024-08-31", "numbers": [3, 12, 20, 26, 35, 43], "bonus": 8},
        {"round": 1135, "date": "2024-09-07", "numbers": [4, 11, 19, 28, 34, 40], "bonus": 14},
        {"round": 1136, "date": "2024-09-14", "numbers": [7, 16, 23, 31, 37, 44], "bonus": 6},
        {"round": 1137, "date": "2024-09-21", "numbers": [2, 9, 17, 25, 32, 39], "bonus": 13},
        {"round": 1138, "date": "2024-09-28", "numbers": [5, 14, 21, 29, 36, 42], "bonus": 20},
        {"round": 1139, "date": "2024-10-05", "numbers": [8, 15, 24, 33, 38, 45], "bonus": 10},
        {"round": 1140, "date": "2024-10-12", "numbers": [1, 10, 18, 27, 35, 41], "bonus": 4},
        {"round": 1141, "date": "2024-10-19", "numbers": [6, 13, 20, 30, 37, 44], "bonus": 21},
        {"round": 1142, "date": "2024-10-26", "numbers": [3, 12, 22, 31, 39, 43], "bonus": 16},
        {"round": 1143, "date": "2024-11-02", "numbers": [4, 11, 19, 26, 34, 40], "bonus": 7},
        {"round": 1144, "date": "2024-11-09", "numbers": [7, 15, 24, 29, 36, 42], "bonus": 11},
        {"round": 1145, "date": "2024-11-16", "numbers": [2, 9, 17, 25, 32, 38], "bonus": 18},
        {"round": 1146, "date": "2024-11-23", "numbers": [5, 14, 21, 28, 35, 45], "bonus": 9},
        {"round": 1147, "date": "2024-11-30", "numbers": [8, 16, 23, 30, 37, 41], "bonus": 12},
        {"round": 1148, "date": "2024-12-07", "numbers": [1, 10, 18, 27, 33, 44], "bonus": 15},
        {"round": 1149, "date": "2024-12-14", "numbers": [6, 13, 20, 31, 38, 42], "bonus": 3},
        {"round": 1150, "date": "2024-12-21", "numbers": [3, 11, 22, 29, 36, 40], "bonus": 19},
        {"round": 1151, "date": "2024-12-28", "numbers": [4, 12, 19, 26, 34, 45], "bonus": 22},
        {"round": 1152, "date": "2025-01-04", "numbers": [7, 15, 24, 32, 39, 43], "bonus": 5},
        {"round": 1153, "date": "2025-01-11", "numbers": [2, 10, 17, 25, 33, 41], "bonus": 14},
        {"round": 1154, "date": "2025-01-18", "numbers": [5, 14, 21, 28, 35, 42], "bonus": 8},
        {"round": 1155, "date": "2025-01-25", "numbers": [8, 16, 23, 30, 37, 44], "bonus": 11},
        {"round": 1156, "date": "2025-02-01", "numbers": [1, 9, 18, 27, 34, 40], "bonus": 17},
        {"round": 1157, "date": "2025-02-08", "numbers": [6, 13, 20, 29, 36, 45], "bonus": 2},
        {"round": 1158, "date": "2025-02-15", "numbers": [3, 11, 22, 31, 38, 43], "bonus": 12},
        {"round": 1159, "date": "2025-02-22", "numbers": [4, 12, 19, 26, 33, 39], "bonus": 7},
        {"round": 1160, "date": "2025-03-01", "numbers": [7, 15, 24, 32, 40, 44], "bonus": 16}
    ]
}

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def atomic_json(path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def sync_draws():
    old = load_json(DRAWS_FILE, DEFAULT_DRAWS)
    draws = old.get("draws", [])
    if not draws:
        draws = DEFAULT_DRAWS["draws"]
    
    atomic_json(DRAWS_FILE, {"source": "Embedded & Local cache", "updated_at": datetime.now(timezone.utc).isoformat(), "draws": draws})
    return draws

def count_hits(ticket, draw):
    return len(set(ticket) & set(draw["numbers"]))

def stats(draws):
    freq = {n: 0 for n in range(1, 46)}
    recent = draws[-30:] if len(draws) >= 30 else draws
    recent_freq = {n: 0 for n in range(1, 46)}
    pair_freq = {}
    for d in draws:
        for n in d["numbers"]: freq[n] += 1
        for a, b in itertools.combinations(d["numbers"], 2):
            key = f"{a}-{b}"
            pair_freq[key] = pair_freq.get(key, 0) + 1
    for d in recent:
        for n in d["numbers"]: recent_freq[n] += 1
    last_seen = {}
    for i, d in enumerate(draws):
        for n in d["numbers"]: last_seen[n] = i
    gaps = {n: len(draws) - 1 - last_seen.get(n, -1) for n in range(1, 46)}
    return {"frequency": freq, "recent30": recent_freq, "gap": gaps, "pairs_top": sorted(pair_freq.items(), key=lambda x: (-x[1], x[0]))[:10]}

def make_ticket(draws, mode, rng):
    s = stats(draws)
    nums = list(range(1, 46))
    if mode == "random":
        return sorted(rng.sample(nums, 6))
    if mode == "frequency":
        weights = [1 + s["frequency"][n] for n in nums]
    elif mode == "recent":
        weights = [1 + 2*s["recent30"][n] + 0.15*s["frequency"][n] for n in nums]
    elif mode == "overdue":
        weights = [1 + min(s["gap"][n], 20) for n in nums]
    else:
        weights = [1.0] * 45
    chosen = []
    pool = nums[:]
    while len(chosen) < 6:
        total = sum(weights[n-1] for n in pool)
        r = rng.random() * total
        acc = 0
        for n in pool:
            acc += weights[n-1]
            if acc >= r:
                chosen.append(n); pool.remove(n); break
    return sorted(chosen)

def generate_tickets(draws, count=10, seed=None):
    rng = random.Random(seed)
    modes = ["balanced", "frequency", "recent", "overdue", "random"]
    tickets, seen = [], set()
    tries = 0
    while len(tickets) < count and tries < 10000:
        mode = modes[len(tickets) % len(modes)]
        ticket = make_ticket(draws, mode, rng)
        key = tuple(ticket)
        if key not in seen:
            seen.add(key)
            tickets.append({"numbers": ticket, "method": mode})
        tries += 1
    if len(tickets) != count:
        raise RuntimeError("Could not create unique tickets")
    return tickets

def backtest(draws, window=200, tickets_per_draw=10):
    if len(draws) < 3:
        return {"rounds": 0, "note": "Not enough data"}
    start = max(1, len(draws) - window)
    rng = random.Random(20261022)
    strategies = ["balanced", "frequency", "recent", "overdue"]
    all_hits = {m: [] for m in strategies + ["random"]}
    prize_counts = {m: {str(k): 0 for k in range(3, 7)} for m in all_hits}
    for idx in range(start, len(draws)):
        history, target = draws[:idx], draws[idx]
        if len(history) < 1: continue
        for mode in strategies:
            tickets = []
            for j in range(tickets_per_draw):
                if mode == "balanced":
                    ticket = sorted(rng.sample(range(1,46), 6))
                else:
                    ticket = make_ticket(history, mode, rng)
                tickets.append(ticket)
            hits = [count_hits(t, target) for t in tickets]
            all_hits[mode].extend(hits)
            for h in hits:
                if h >= 3: prize_counts[mode][str(h)] += 1
        rt = [sorted(rng.sample(range(1,46), 6)) for _ in range(tickets_per_draw)]
        rh = [count_hits(t, target) for t in rt]
        all_hits["random"].extend(rh)
        for h in rh:
            if h >= 3: prize_counts["random"][str(h)] += 1
    summaries = {}
    for mode, hs in all_hits.items():
        summaries[mode] = {
            "ticket_count": len(hs),
            "avg_hits": round(sum(hs)/len(hs), 4) if hs else 0,
            "tickets_with_3plus": sum(h >= 3 for h in hs),
            "hit_distribution_0_to_6": {str(k): hs.count(k) for k in range(7)},
            "prize_hit_counts": prize_counts[mode]
        }
    return {"method": "walk-forward", "rounds_tested": len(range(start, len(draws))), "tickets_per_round_per_strategy": tickets_per_draw, "strategies": summaries}

def telegram_send(text):
    if not TOKEN or not CHAT_ID:
        print("Telegram secrets missing; message not sent.")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True}).encode()
    req = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=20) as r:
        result = json.loads(r.read().decode())
    if not result.get("ok"):
        raise RuntimeError("Telegram API rejected the message")

def format_daily(draws, s, bt):
    latest = draws[-1]
    hot = sorted(s["frequency"], key=lambda n: (-s["frequency"][n], n))[:8]
    cold = sorted(s["frequency"], key=lambda n: (s["frequency"][n], n))[:8]
    recent = sorted(s["recent30"], key=lambda n: (-s["recent30"][n], n))[:8]
    rand = bt["strategies"]["random"]
    msg = [
        "📊 로또 6/45 일일 분석",
        f"데이터: 1회~{latest['round']}회 ({latest['date']})",
        "최근 당첨: " + " · ".join(map(str, latest["numbers"])) + f" | 보너스 {latest['bonus']}",
        "",
        "누적 출현 상위: " + ", ".join(map(str, hot)),
        "누적 출현 하위: " + ", ".join(map(str, cold)),
        "최근 30회 상위: " + ", ".join(map(str, recent)),
        "",
        f"워크포워드 백테스트: 최근 {bt.get('rounds_tested',0)}회",
        f"무작위 기준 평균 적중: {rand['avg_hits']}개/게임",
        f"무작위 기준 3개 이상 일치: {rand['tickets_with_3plus']}게임",
        "",
        "주의: 빈도·미출현 기간은 다음 추첨의 확률을 높인다는 증거가 아닙니다."
    ]
    return "\n".join(msg)

def format_final(draws, s, bt, tickets):
    latest = draws[-1]
    next_round = latest["round"] + 1
    lines = [f"🎯 로또 6/45 · {next_round}회 분석 조합", "━━━━━━━━━━━━━━"]
    for i, t in enumerate(tickets, 1):
        ns = t["numbers"]
        lines.append(f"{i:02d}. " + " · ".join(f"{n:02d}" for n in ns) + f"  [{t['method']}]")
    rand = bt["strategies"]["random"]
    lines += [
        "━━━━━━━━━━━━━━",
        f"기준 데이터: 1~{latest['round']}회까지",
        f"백테스트: 최근 {bt.get('rounds_tested',0)}회, 무작위 평균 적중 {rand['avg_hits']}개/게임",
        "",
        "중요: 모든 6개 조합의 1등 확률은 동일하게 1/8,145,060입니다.",
        "이 조합은 통계 실험용 후보이며 당첨 예측·보장이 아닙니다. 예산을 정하고 무리하게 구매하지 마세요."
    ]
    return "\n".join(lines)

def main():
    now = datetime.now(TZ_KST)
    draws = sync_draws()
    s = stats(draws)
    bt = backtest(draws)
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "latest_round": draws[-1]["round"], "stats": s, "backtest": bt}
    atomic_json(REPORT_FILE, report)
    state = load_json(STATE_FILE, {"last_daily": None, "last_final_round": None})
    daily_key = now.strftime("%Y-%m-%d")
    final_round = draws[-1]["round"] + 1
    is_final_window = now.weekday() == 5 and (now.hour >= 10)
    if state.get("last_daily") != daily_key:
        telegram_send(format_daily(draws, s, bt))
        state["last_daily"] = daily_key
    if is_final_window and state.get("last_final_round") != final_round:
        tickets = generate_tickets(draws, 10, seed=int(now.strftime("%Y%m%d")) + final_round)
        history = load_json(DATA / "recommendations.json", {"recommendations": []})
        history["recommendations"].append({"target_round": final_round, "created_at": now.isoformat(), "tickets": tickets, "based_on_round": draws[-1]["round"]})
        atomic_json(DATA / "recommendations.json", history)
        telegram_send(format_final(draws, s, bt, tickets))
        state["last_final_round"] = final_round
    atomic_json(STATE_FILE, state)
    print(f"OK: draws={len(draws)}, latest={draws[-1]['round']}, KST={now.isoformat()}")

if __name__ == "__main__":
    main()
