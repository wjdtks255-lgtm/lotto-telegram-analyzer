#!/usr/bin/env python3
"""Lotto 6/45 Telegram Analyzer & Notifier

Schedule (KST):
- Mon 18:00 : 실제 데이터(data/draws.json) 기반 통계 리포트
- Fri 18:00 : 필터 적용 추천 10조합 생성 + data/picks.json 에 저장
- Sun 13:00 : 최신 회차 당첨번호 조회 -> 금요일에 저장한 조합과 대조 -> 누적 전적

※ 로또는 매 회차 독립 추첨이라 어떤 통계/필터도 당첨 확률을 바꾸지 못합니다.
"""

import os
import json
import random
import urllib.request
from collections import Counter
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
TOKEN = os.getenv("LOTTO_TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("LOTTO_TELEGRAM_CHAT_ID", "").strip()
MODE = os.getenv("LOTTO_MODE", "").strip().lower()  # monday / friday / sunday (수동 실행용)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DRAWS_PATH = os.path.join(DATA_DIR, "draws.json")
PICKS_PATH = os.path.join(DATA_DIR, "picks.json")

# 기준점: 2026-10-31(토) = 1248회
BASE_DRW_NO = 1248
BASE_DATE = datetime(2026, 10, 31, tzinfo=KST).date()

# ==========================================
# 📌 사용자 정의 고정수 및 제외수 설정 영역
# ==========================================
FIXED_NUMBERS = []
EXCLUDE_NUMBERS = []
# ==========================================

DISCLAIMER = "<i>※ 통계·필터는 참고용이며 당첨 확률을 높이지 않습니다.</i>"


# ---------------------------------------------------------------- utils
def now_kst():
    return datetime.now(KST)


def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        print("Telegram token or chat ID missing.")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            print("Telegram response:", res.status)
    except Exception as e:
        print("Telegram send failed:", e)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def fmt(nums):
    return " · ".join(f"{n:02d}" for n in nums)


# ------------------------------------------------------- round numbers
def latest_finished_round(today=None):
    """오늘 기준으로 이미 추첨이 끝난 가장 최근 회차 (토요일 추첨 가정)."""
    today = today or now_kst().date()
    diff = (today - BASE_DATE).days
    return BASE_DRW_NO + diff // 7


def upcoming_round(today=None):
    """오늘 기준으로 다음에 추첨될 회차."""
    today = today or now_kst().date()
    diff = (today - BASE_DATE).days
    return BASE_DRW_NO + (diff + 6) // 7


# --------------------------------------------------------- draw data
def _http_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def fetch_draw(round_no):
    """한 회차 당첨 정보를 가져옴. 실패하면 None (가짜 번호는 절대 반환하지 않음)."""
    # 1) 동행복권
    try:
        d = _http_json(f"https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round_no}")
        if d.get("returnValue") == "success":
            nums = sorted(d[f"drwtNo{i}"] for i in range(1, 7))
            return {"round": int(d["drwNo"]), "date": d.get("drwNoDate", ""), "numbers": nums, "bonus": int(d["bnusNo"])}
    except Exception as e:
        print("dhlottery fetch failed:", e)

    # 2) 미러(smok95/lotto) - 필드명이 달라도 최대한 읽어봄
    try:
        d = _http_json(f"https://smok95.github.io/lotto/results/{round_no}.json")
        nums = d.get("numbers") or d.get("winning_numbers")
        bonus = d.get("bonus_no", d.get("bonus"))
        rno = d.get("draw_no", d.get("round", round_no))
        date = str(d.get("date", d.get("draw_date", "")))[:10]
        if nums and len(nums) == 6 and bonus:
            return {"round": int(rno), "date": date, "numbers": sorted(int(x) for x in nums), "bonus": int(bonus)}
    except Exception as e:
        print("mirror fetch failed:", e)
    return None


def load_draws():
    data = load_json(DRAWS_PATH, {"draws": []})
    if not isinstance(data, dict):
        data = {"draws": []}
    data.setdefault("draws", [])
    return data


def update_draws(data, target_round):
    """draws.json 의 마지막 회차 이후 ~ target_round 까지 새 회차를 채움. 변경 여부 반환."""
    changed = False
    have = {d["round"] for d in data["draws"]}
    last = max(have) if have else 0
    # 한 번에 너무 많이 받지 않도록 최대 8회차까지만
    for n in range(last + 1, min(target_round, last + 8) + 1):
        d = fetch_draw(n)
        if d is None:
            break
        if d["round"] not in have:
            data["draws"].append(d)
            have.add(d["round"])
            changed = True
    if changed:
        data["draws"].sort(key=lambda x: x["round"])
        data["updated_at"] = now_kst().isoformat(timespec="seconds")
        save_json(DRAWS_PATH, data)
    return changed


# -------------------------------------------------------------- stats
def compute_stats(draws):
    freq = Counter()
    last_seen = {}
    for d in draws:
        for n in d["numbers"]:
            freq[n] += 1
            last_seen[n] = d["round"]
    last_round = max(d["round"] for d in draws)

    recent = Counter()
    for d in sorted(draws, key=lambda x: x["round"])[-20:]:
        for n in d["numbers"]:
            recent[n] += 1

    allnums = range(1, 46)
    hot = sorted(allnums, key=lambda n: (-freq[n], n))[:10]
    cold = sorted(allnums, key=lambda n: (freq[n], n))[:10]
    hot20 = sorted(allnums, key=lambda n: (-recent[n], n))[:10]
    absent = sorted(allnums, key=lambda n: (-(last_round - last_seen.get(n, 0)), n))[:10]
    return {
        "freq": freq, "recent": recent, "last_seen": last_seen, "last_round": last_round,
        "hot": hot, "cold": cold, "hot20": hot20, "absent": absent,
    }


def get_monday_report():
    data = load_draws()
    update_draws(data, latest_finished_round())
    draws = data["draws"]
    if not draws:
        return "⚠️ <b>SANI LOTTO</b>\n데이터(data/draws.json)가 비어 있어 통계를 계산하지 못했습니다."

    s = compute_stats(draws)
    f, r, ls, lr = s["freq"], s["recent"], s["last_seen"], s["last_round"]
    hot = ", ".join(f"{n}({f[n]}회)" for n in s["hot"])
    cold = ", ".join(f"{n}({f[n]}회)" for n in s["cold"])
    hot20 = ", ".join(f"{n}({r[n]}회)" for n in s["hot20"])
    absent = ", ".join(f"{n}({lr - ls.get(n, 0)}회 전)" for n in s["absent"])

    return (
        "📊 <b>SANI LOTTO — 스마트 통계 분석 리포트</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📌 1~{lr}회 데이터 기준 (총 {len(draws)}회차)\n\n"
        f"🔥 <b>누적 핫 번호 TOP10</b>\n{hot}\n\n"
        f"🌡️ <b>최근 20회 핫 번호 TOP10</b>\n{hot20}\n\n"
        f"❄️ <b>누적 콜드 번호 TOP10</b>\n{cold}\n\n"
        f"⏳ <b>최장 미출현 번호 TOP10</b>\n{absent}\n\n"
        f"{DISCLAIMER}"
    )


# ------------------------------------------------------- combinations
def _max_run(combo):
    run = best = 1
    for a, b in zip(combo, combo[1:]):
        run = run + 1 if b == a + 1 else 1
        best = max(best, run)
    return best


def _passes_filters(combo, past_sets):
    if not (100 <= sum(combo) <= 170):
        return False
    odd = sum(1 for n in combo if n % 2)
    if odd < 2 or odd > 4:
        return False
    low = sum(1 for n in combo if n <= 22)
    if low < 2 or low > 4:
        return False
    if _max_run(combo) >= 3:
        return False
    if frozenset(combo) in past_sets:  # 역대 당첨 조합 제외
        return False
    return True


def generate_combinations(num_sets=10, past_sets=frozenset()):
    pool = [i for i in range(1, 46) if i not in EXCLUDE_NUMBERS]
    fixed = [n for n in dict.fromkeys(FIXED_NUMBERS) if 1 <= n <= 45 and n not in EXCLUDE_NUMBERS][:6]
    need = 6 - len(fixed)
    rest = [p for p in pool if p not in fixed]

    combos, seen = [], set()
    attempts = 0
    while len(combos) < num_sets and attempts < 50000:
        attempts += 1
        combo = sorted(fixed + random.sample(rest, need))
        key = tuple(combo)
        if key in seen:
            continue
        if _passes_filters(combo, past_sets):
            seen.add(key)
            combos.append(combo)

    # 필터를 통과한 조합이 부족하면(고정수가 많은 경우 등) 필터 없이 채움
    while len(combos) < num_sets:
        combo = sorted(fixed + random.sample(rest, need))
        if tuple(combo) not in seen:
            seen.add(tuple(combo))
            combos.append(combo)
    return combos


def load_picks():
    p = load_json(PICKS_PATH, {})
    p.setdefault("history", {})
    p.setdefault("totals", {"sets": 0, "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "none": 0})
    return p


def get_friday_report():
    data = load_draws()
    update_draws(data, latest_finished_round())
    past_sets = frozenset(frozenset(d["numbers"]) for d in data["draws"])

    target = upcoming_round()
    combos = generate_combinations(10, past_sets)

    picks = load_picks()
    picks["history"][str(target)] = {"combos": combos, "graded": False}
    save_json(PICKS_PATH, picks)

    symbols = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]
    lines = [f"{symbols[i]} {fmt(c)}" for i, c in enumerate(combos)]

    settings = ""
    if FIXED_NUMBERS or EXCLUDE_NUMBERS:
        settings = f"\n⚙️ <i>[고정수: {FIXED_NUMBERS} | 제외수: {EXCLUDE_NUMBERS}]</i>"

    return (
        f"🎯 <b>SANI LOTTO — 제 {target}회 추천 조합 (10선)</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📌 합계 100~170 · 홀짝 2~4 · 고저 균형 · 3연번 제외 · 역대 당첨조합 제외"
        f"{settings}\n\n" + "\n".join(lines) + f"\n\n{DISCLAIMER}"
    )


# ------------------------------------------------------------ grading
def grade(combo, winning, bonus):
    matched = len(set(combo) & set(winning))
    if matched == 6:
        return 1
    if matched == 5 and bonus in combo:
        return 2
    if matched == 5:
        return 3
    if matched == 4:
        return 4
    if matched == 3:
        return 5
    return 0


def get_sunday_report():
    data = load_draws()
    target = latest_finished_round()
    update_draws(data, target)

    draw = next((d for d in data["draws"] if d["round"] == target), None)
    if draw is None:
        draw = fetch_draw(target)
    if draw is None:
        return (
            f"⚠️ <b>SANI LOTTO — 제 {target}회 결과 조회 실패</b>\n"
            "당첨번호를 가져오지 못했습니다. 잠시 후 Actions 에서 수동 실행(Run workflow)해 주세요."
        )

    win, bonus = draw["numbers"], draw["bonus"]
    picks = load_picks()
    entry = picks["history"].get(str(target))

    if entry is None:
        this_week = "- 이번 회차에 저장된 추천 조합이 없어 대조하지 못했습니다."
    else:
        ranks = [grade(c, win, bonus) for c in entry["combos"]]
        lines = []
        for c, rk in zip(entry["combos"], ranks):
            matched = len(set(c) & set(win))
            tag = f"{rk}등" if rk else "낙첨"
            lines.append(f"{fmt(c)} → {matched}개 ({tag})")
        this_week = "\n".join(lines)

        if not entry.get("graded"):
            t = picks["totals"]
            for rk in ranks:
                t["sets"] += 1
                t[str(rk) if rk else "none"] += 1
            entry["graded"] = True
            entry["win"] = {"numbers": win, "bonus": bonus}
            save_json(PICKS_PATH, picks)

    t = picks["totals"]
    return (
        f"🏆 <b>SANI LOTTO — 제 {target}회 당첨 결과 & 전적</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎱 <b>당첨 번호</b>\n{fmt(win)} | 보너스 {bonus:02d}\n\n"
        f"📋 <b>이번 주 추천 조합 대조</b>\n{this_week}\n\n"
        f"📈 <b>누적 전적 (총 {t['sets']}조합)</b>\n"
        f"🥇 1등: {t['1']}회\n🥈 2등: {t['2']}회\n🥉 3등: {t['3']}회\n"
        f"🎖️ 4등: {t['4']}회\n🎗️ 5등: {t['5']}회\n❌ 낙첨: {t['none']}회\n\n"
        f"{DISCLAIMER}"
    )


# --------------------------------------------------------------- main
def main():
    now = now_kst()
    weekday = now.weekday()  # 0: 월, 4: 금, 6: 일
    print(f"Executing Lotto Bot at KST {now.strftime('%Y-%m-%d %H:%M:%S')} (Weekday: {weekday}, mode={MODE or 'auto'})")

    if MODE in ("monday", "friday", "sunday"):
        kind = MODE
    else:
        kind = {0: "monday", 4: "friday", 6: "sunday"}.get(weekday, "monday")

    try:
        msg = {"monday": get_monday_report, "friday": get_friday_report, "sunday": get_sunday_report}[kind]()
    except Exception as e:
        msg = f"⚠️ <b>SANI LOTTO 실행 오류</b>\n{type(e).__name__}: {e}"
        print("Report failed:", e)

    send_telegram(msg)


if __name__ == "__main__":
    main()
