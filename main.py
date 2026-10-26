#!/usr/bin/env python3
"""SANI LOTTO — Strict Single-Send Schedule Controller.

- Mon-Fri 18:00 ~ 18:20 KST: General analysis report (Exactly once per day).
- Fri 18:25 ~ KST: Recommended picks (Exactly once on Friday).
- Draw Results: Instant check and single-send grading as soon as results are published.
"""

import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone, timedelta
from math import comb
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

# ----------------------------- Configuration -----------------------------
KST = timezone(timedelta(hours=9))
SOURCE = "https://smok95.github.io/lotto/results/"
SOURCE_NAME = "smok95/lotto (third-party mirror; verify against official results)"
DATA_DIR = Path(os.getenv("LOTTO_DATA_DIR", "data"))
DRAW_FILE = DATA_DIR / "draws.json"
STATE_FILE = DATA_DIR / "state.json"
TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT = (os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID") or "").strip()
PICK_COUNT = int(os.getenv("LOTTO_PICK_COUNT", "10"))
MAX_RETRIES = 3
HTTP_TIMEOUT = 25
MAX_HISTORY = 100

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="[%(asctime)s KST] %(levelname)s %(message)s",
)
log = logging.getLogger("sani-lotto")


def now_kst():
    return datetime.now(KST)


def stamp():
    return now_kst().isoformat(timespec="seconds")


def default_state():
    return {
        "version": 5,
        "analysis_sent_date": None,
        "picks_sent_date": None,
        "pending_results": [],
        "checked_rounds": [],
        "results_stats": {
            "counts": {str(i): 0 for i in range(6)},
            "total_tickets": 0,
            "checked_draws": 0,
            "history": [],
        },
    }


def load_json(path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"상태/데이터 파일을 읽을 수 없습니다: {path}: {exc}") from exc
    return value


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def load_state():
    state = load_json(STATE_FILE, default_state())
    if not isinstance(state, dict):
        raise RuntimeError("상태 파일 형식이 올바르지 않습니다.")
    defaults = default_state()
    for key, value in defaults.items():
        state.setdefault(key, value)
    state["version"] = 5
    state.setdefault("pending_results", [])
    state.setdefault("checked_rounds", [])
    stats = state.setdefault("results_stats", defaults["results_stats"])
    stats.setdefault("counts", {str(i): 0 for i in range(6)})
    for i in range(6):
        stats["counts"].setdefault(str(i), 0)
    stats.setdefault("total_tickets", 0)
    stats.setdefault("checked_draws", 0)
    stats.setdefault("history", [])
    return state


def get_json(url, timeout=HTTP_TIMEOUT):
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 SaniLotto/2.0",
                    "Accept": "application/json",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (URLError, HTTPError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_error = exc
            log.warning("데이터 요청 실패 %s/%s: %s", attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                time.sleep(attempt)
    raise RuntimeError(f"데이터 요청 최종 실패: {url}: {last_error}")


def parse_draw(row):
    try:
        round_no = int(row.get("draw_no", row.get("round", 0)))
        raw_numbers = row["numbers"]
        numbers = [int(n) for n in raw_numbers]
        bonus = int(row.get("bonus_no", row.get("bonus", 0)))
        date_text = str(row.get("date", row.get("draw_date", "")))[:10]
        datetime.strptime(date_text, "%Y-%m-%d")
        if round_no < 1 or len(numbers) != 6 or len(set(numbers)) != 6:
            return None
        if any(n < 1 or n > 45 for n in numbers):
            return None
        if bonus < 1 or bonus > 45 or bonus in numbers:
            return None
        return {
            "round": round_no,
            "date": date_text,
            "numbers": sorted(numbers),
            "bonus": bonus,
        }
    except (KeyError, TypeError, ValueError):
        return None


def get_latest_draw():
    obj = get_json(SOURCE + "latest.json")
    if isinstance(obj, list):
        rows = [parse_draw(row) for row in obj if isinstance(row, dict)]
        rows = [row for row in rows if row]
        draw = max(rows, key=lambda row: row["round"]) if rows else None
    else:
        draw = parse_draw(obj) if isinstance(obj, dict) else None
    if not draw:
        raise RuntimeError("최신 회차 데이터 검증에 실패했습니다.")
    return draw


def get_all_draws():
    obj = get_json(SOURCE + "all.json", timeout=40)
    rows = obj.get("results", obj.get("draws", [])) if isinstance(obj, dict) else obj
    if not isinstance(rows, list):
        raise RuntimeError("전체 회차 데이터 형식이 올바르지 않습니다.")
    by_round = {}
    for row in rows:
        if isinstance(row, dict):
            draw = parse_draw(row)
            if draw:
                by_round[draw["round"]] = draw
    if not by_round:
        raise RuntimeError("유효한 회차 데이터가 없습니다.")
    return by_round


def validate_draws(draws, latest):
    draws = sorted(draws, key=lambda row: row["round"])
    if not draws or draws[0]["round"] != 1:
        raise RuntimeError("전체 데이터에 1회차가 없습니다.")
    for previous, current in zip(draws, draws[1:]):
        if current["round"] != previous["round"] + 1:
            raise RuntimeError(
                f"회차 누락: {previous['round']}회 다음이 {current['round']}회입니다."
            )
    last = draws[-1]
    if (last["round"] != latest["round"]
            or last["numbers"] != latest["numbers"]
            or last["bonus"] != latest["bonus"]):
        raise RuntimeError("전체 데이터와 최신 회차 데이터가 일치하지 않습니다.")
    return draws


def sync_draws():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    latest = get_latest_draw()
    old = load_json(DRAW_FILE, {})
    saved = {}
    if isinstance(old, dict) and old.get("source") == SOURCE_NAME:
        for row in old.get("draws", []):
            if isinstance(row, dict):
                draw = parse_draw(row)
                if draw:
                    saved[draw["round"]] = draw
    if saved and min(saved) == 1 and max(saved) == latest["round"]:
        try:
            return validate_draws(list(saved.values()), latest)
        except RuntimeError as exc:
            log.warning("저장된 회차를 재검증합니다: %s", exc)

    all_draws = get_all_draws()
    if latest["round"] not in all_draws:
        raise RuntimeError("전체 데이터에 최신 회차가 없습니다.")
    if (all_draws[latest["round"]]["numbers"] != latest["numbers"]
            or all_draws[latest["round"]]["bonus"] != latest["bonus"]):
        raise RuntimeError("최신 회차와 전체 데이터의 당첨 번호가 다릅니다.")
    draws = validate_draws(list(all_draws.values()), latest)
    save_json(DRAW_FILE, {
        "source": SOURCE_NAME,
        "updated_at": stamp(),
        "draws": draws,
    })
    return draws


def historical_stats(draws):
    frequency = {n: 0 for n in range(1, 46)}
    last_seen = {n: None for n in range(1, 46)}
    recent = {n: 0 for n in range(1, 46)}
    for draw in draws:
        for number in draw["numbers"]:
            frequency[number] += 1
            last_seen[number] = draw["round"]
    for draw in draws[-30:]:
        for number in draw["numbers"]:
            recent[number] += 1
    latest_round = draws[-1]["round"]
    gap = {
        n: (latest_round - last_seen[n]) if last_seen[n] is not None else latest_round
        for n in range(1, 46)
    }
    return frequency, recent, gap


def is_valid_smart_ticket(ticket):
    total_sum = sum(ticket)
    if not (100 <= total_sum <= 175):
        return False
    odds = sum(1 for n in ticket if n % 2 != 0)
    if odds not in (2, 3, 4):
        return False
    highs = sum(1 for n in ticket if n >= 23)
    if highs not in (2, 3, 4):
        return False
    sorted_t = sorted(ticket)
    for i in range(len(sorted_t) - 2):
        if sorted_t[i+1] == sorted_t[i] + 1 and sorted_t[i+2] == sorted_t[i] + 2:
            return False
    decades = set((n - 1) // 10 for n in ticket)
    if len(decades) < 3:
        return False
    return True


def make_diversified_picks(draws, count=PICK_COUNT):
    frequency, recent, gap = historical_stats(draws)
    rng = secrets.SystemRandom()
    weights = {}
    for n in range(1, 46):
        w = (frequency[n] * 0.4) + (recent[n] * 2.0) + (min(gap[n], 15) * 0.5)
        weights[n] = max(w, 1.0)
    population = list(range(1, 46))
    prob_weights = [weights[n] for n in population]
    picks = set()
    max_attempts = 100_000

    for _ in range(max_attempts):
        if len(picks) >= count:
            break
        chosen = set()
        while len(chosen) < 6:
            pick_num = rng.choices(population, weights=prob_weights, k=1)[0]
            chosen.add(pick_num)
        ticket = tuple(sorted(chosen))
        if is_valid_smart_ticket(ticket):
            picks.add(ticket)

    while len(picks) < count:
        ticket = tuple(sorted(rng.sample(range(1, 46), 6)))
        if is_valid_smart_ticket(ticket):
            picks.add(ticket)

    return [{"method": "스마트 필터링 분석", "numbers": list(t)} for t in sorted(picks)]


def portfolio_summary(picks):
    counts = {n: 0 for n in range(1, 46)}
    for pick in picks:
        for n in pick["numbers"]:
            counts[n] += 1
    used = [value for value in counts.values() if value]
    pair_overlaps = []
    for i in range(len(picks)):
        for j in range(i + 1, len(picks)):
            pair_overlaps.append(len(set(picks[i]["numbers"]) & set(picks[j]["numbers"])))
    return {
        "covered_numbers": len(used),
        "min_uses": min(used) if used else 0,
        "max_uses": max(used) if used else 0,
        "max_pair_overlap": max(pair_overlaps) if pair_overlaps else 0,
    }


def send_telegram(message):
    if not TOKEN or not CHAT:
        raise RuntimeError("TELEGRAM_TOKEN과 TELEGRAM_CHAT_ID(또는 CHAT_ID)를 설정하세요.")
    payload = json.dumps({
        "chat_id": CHAT,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"텔레그램 API가 전송 실패를 반환했습니다: {result}")


def format_analysis(draws):
    latest = draws[-1]
    frequency, recent, gap = historical_stats(draws)
    hot = sorted(frequency, key=lambda n: (frequency[n], recent[n]), reverse=True)[:10]
    cold = sorted(frequency, key=lambda n: (frequency[n], recent[n]))[:10]
    latest_absent = sorted(gap, key=lambda n: gap[n], reverse=True)[:10]

    lines = [
        "📊 <b>SANI LOTTO — 스마트 통계 분석 리포트</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📌 최신 회차: <b>{latest['round']}회</b> ({latest['date']})",
        "🎱 당첨 번호: " + " · ".join(map(str, latest["numbers"])) + f" | 보너스 {latest['bonus']}",
        f"📚 분석 회차: {len(draws)}회 전체 데이터 반영",
        "",
        "🔥 <b>최근/누적 핫(Hot) 번호 상위 10</b>",
        ", ".join(f"{n}({frequency[n]}회)" for n in hot),
        "",
        "❄️ <b>누적 콜드(Cold) 번호 상위 10</b>",
        ", ".join(f"{n}({frequency[n]}회)" for n in cold),
        "",
        "⏳ <b>최장 미출현 번호 상위 10</b>",
        ", ".join(f"{n}({gap[n]}회 전)" for n in latest_absent),
    ]
    return "\n".join(lines)


def format_picks(draws, picks, target_round):
    summary = portfolio_summary(picks)
    lines = [
        "⚡ <b>SANI LOTTO — 필터링 당첨 필살 조합</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📌 기준: {draws[-1]['round']}회 데이터 | 대상: <b>{target_round}회</b>",
        f"🎯 추천 수: {len(picks)}게임 (스마트 밸런스 필터 적용)",
        "",
    ]
    for index, pick in enumerate(picks, 1):
        lines.append(f"{index:02d}. " + " · ".join(f"<b>{n:02d}</b>" for n in pick["numbers"]))
    lines.extend([
        "",
        f"🔍 <b>포트폴리오 분석</b>",
        f"• 활용 번호: {summary['covered_numbers']}/45개 커버",
        f"• 번호 중복 교차: 조합 간 최대 {summary['max_pair_overlap']}개 공유",
        "",
        "🔥 이번 회차 당첨을 기원합니다!",
    ])
    return "\n".join(lines)


def rank_ticket(numbers, draw):
    selected = set(numbers)
    winning = set(draw["numbers"])
    matches = len(selected & winning)
    if matches == 6:
        return 1, matches
    if matches == 5 and draw["bonus"] in selected:
        return 2, matches
    if matches == 5:
        return 3, matches
    if matches == 4:
        return 4, matches
    if matches == 3:
        return 5, matches
    return 0, matches


def format_result(draw, picks, results):
    counts = results["counts"]
    total = results["total_tickets"]
    prize_tickets = sum(counts[str(rank)] for rank in range(1, 6))
    lines = [
        "🏆 <b>SANI LOTTO — 당첨 결과 확인</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📌 대조 회차: <b>{draw['round']}회</b> ({draw['date']})",
        "🎱 실제 당첨 번호: " + " · ".join(map(str, draw["numbers"])) + f" | 보너스 {draw['bonus']}",
        "",
        f"📊 누적 집계: {results['checked_draws']}회차 / 총 {total}게임",
        f"🥇 1등: {counts['1']} | 🥈 2등: {counts['2']} | 🥉 3등: {counts['3']}",
        f"🎖️ 4등: {counts['4']} | 🎖️ 5등: {counts['5']} | ❌ 미당첨: {counts['0']}",
        f"🎯 5등 이상 당첨률: {prize_tickets}/{total} ({(100 * prize_tickets / total) if total else 0:.2f}%)",
        "",
        "<b>[추천 조합 대조 결과]</b>",
    ]
    for index, pick in enumerate(picks, 1):
        rank, matches = rank_ticket(pick["numbers"], draw)
        result_text = f"<b>{rank}등 당첨! 🎉</b>" if rank else "미당첨"
        lines.append(f"{index:02d}. {' · '.join(map(str, pick['numbers']))} — {result_text} ({matches}개 일치)")
    return "\n".join(lines)


def check_pending_results(draws, state):
    pending = state.get("pending_results", [])
    if not pending:
        return False
    by_round = {draw["round"]: draw for draw in draws}
    changed = False
    checked = set(state.get("checked_rounds", []))
    stats = state["results_stats"]

    processed_targets = set()
    new_pending = []

    for item in pending:
        target = int(item["target_round"])
        if target in checked or target in processed_targets:
            changed = True
            continue

        draw = by_round.get(target)
        if draw is None:
            new_pending.append(item)
            continue

        for pick in item["picks"]:
            rank, _ = rank_ticket(pick["numbers"], draw)
            stats["counts"][str(rank)] += 1
            stats["total_tickets"] += 1
        stats["checked_draws"] += 1
        stats["history"].append({
            "round": target,
            "date": draw["date"],
            "numbers": draw["numbers"],
            "bonus": draw["bonus"],
            "picks": item["picks"],
        })
        stats["history"] = stats["history"][-MAX_HISTORY:]
        
        checked.add(target)
        processed_targets.add(target)
        changed = True

        send_telegram(format_result(draw, item["picks"], stats))
        log.info("%s회 당첨 결과 즉시 확인 및 전송 완료", target)

    state["pending_results"] = new_pending
    state["checked_rounds"] = sorted(checked)[-500:]
    if changed:
        save_json(STATE_FILE, state)
    return changed


def run():
    current = now_kst()
    weekday = current.weekday()  # Mon=0 ... Sun=6
    state = load_state()

    # 1. 당첨번호 결과 즉시 확인 및 대조
    draws = sync_draws()
    check_pending_results(draws, state)

    if weekday == 5:
        log.info("토요일: 정기 분석 발송 없음")
        return

    today = current.strftime("%Y-%m-%d")
    event_name = os.getenv("GITHUB_EVENT_NAME", "").strip()
    is_manual = (event_name == "workflow_dispatch")

    analysis = format_analysis(draws)
    is_friday = (weekday == 4)
    current_hour = current.hour
    current_minute = current.minute

    # 2. 월~금 오후 6시 정각 분석 리포트 (18시 0분 ~ 20분 사이에만 하루 1회 엄격히 발송)
    analysis_sent = state.get("analysis_sent_date") == today
    if (is_manual or (current_hour == 18 and current_minute < 20)) and not analysis_sent:
        send_telegram(analysis)
        if not is_manual:
            state["analysis_sent_date"] = today
        save_json(STATE_FILE, state)
        log.info("일일 통계 분석 리포트 발송 완료 (18시 정각 타임)")

    # 3. 금요일 오후 6시 30분 추천 조합 발송 (18시 25분 이후에만 금요일 1회 발송)
    picks_sent = state.get("picks_sent_date") == today
    if is_friday and (is_manual or (current_hour == 18 and current_minute >= 25) or current_hour > 18) and not picks_sent:
        picks = make_diversified_picks(draws, PICK_COUNT)
        target_round = draws[-1]["round"] + 1
        picks_message = format_picks(draws, picks, target_round)
        send_telegram(picks_message)
        
        state.setdefault("pending_results", [])
        if not any(int(row.get("target_round", 0)) == target_round
                   for row in state["pending_results"]):
            state["pending_results"].append({
                "target_round": target_round,
                "created_at": stamp(),
                "picks": picks,
            })
            
        if not is_manual:
            state["picks_sent_date"] = today
        save_json(STATE_FILE, state)
        log.info("금요일 추천 번호 조합 별도 발송 완료 (18시 30분 타임)")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        log.exception("실행 실패; 상태를 임의 초기화하지 않았습니다.")
        raise
