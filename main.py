#!/usr/bin/env python3
"""SANI LOTTO — transparent, diversified random picks (not a prediction model).

This program does not claim to improve lottery odds. It generates distinct,
random combinations with balanced number coverage across the portfolio, reports
historical frequencies as descriptive statistics only, and checks results.
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
        "version": 2,
        "analysis_sent_date": None,
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
        # Do not silently replace a corrupted history/state file with an empty one.
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
    # Keep fields from older versions while adding the new transparent schema.
    defaults = default_state()
    for key, value in defaults.items():
        state.setdefault(key, value)
    state["version"] = 2
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


def lottery_odds():
    """Exact combinatorial odds for one 6-number ticket in a 6/45 draw."""
    total = comb(45, 6)
    exact = {
        matches: comb(6, matches) * comb(39, 6 - matches) / total
        for matches in range(7)
    }
    # Rank 2: exactly five main numbers plus the bonus number.
    rank2 = 6 / total
    # Rank 3: exactly five main numbers, excluding the bonus.
    rank3 = (comb(6, 5) * 38) / total
    # Rank 4: exactly four main numbers; rank 5: exactly three.
    rank4 = (comb(6, 4) * comb(39, 2)) / total
    rank5 = (comb(6, 3) * comb(39, 3)) / total
    return {
        "total_combinations": total,
        "exact_matches": exact,
        "rank1": 1 / total,
        "rank2": rank2,
        "rank3": rank3,
        "rank4": rank4,
        "rank5": rank5,
        "any_prize": rank2 + rank3 + rank4 + rank5 + exact[6],
    }


def make_diversified_picks(count=PICK_COUNT):
    """Create distinct random tickets with balanced coverage across the set.

    For 10 tickets, each of 45 numbers appears once or twice in the portfolio
    (60 slots total). This is diversification, not an odds-enhancement method.
    """
    if count < 1:
        raise ValueError("조합 수는 1 이상이어야 합니다.")
    if count * 6 < 45:
        # For small sets, use distinct uniform random tickets; full 1/2 coverage
        # is only possible when the total number of slots is at least 45.
        rng = secrets.SystemRandom()
        picks = set()
        while len(picks) < count:
            picks.add(tuple(sorted(rng.sample(range(1, 46), 6))))
        return [{"method": "균등 무작위", "numbers": list(p)} for p in sorted(picks)]

    rng = secrets.SystemRandom()
    total_slots = count * 6
    max_tries = 50_000
    for _ in range(max_tries):
        numbers = list(range(1, 46))
        duplicated = rng.sample(numbers, total_slots - 45)
        slots = numbers + duplicated
        rng.shuffle(slots)
        tickets = [tuple(sorted(slots[i:i + 6])) for i in range(0, total_slots, 6)]
        if any(len(set(ticket)) != 6 for ticket in tickets):
            continue
        if len(set(tickets)) != count:
            continue
        return [
            {"method": "균등 무작위 분산", "numbers": list(ticket)}
            for ticket in tickets
        ]
    raise RuntimeError("분산 조합을 만들지 못했습니다. 조합 수를 확인하세요.")


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
    odds = lottery_odds()

    lines = [
        "📊 <b>SANI LOTTO — 과거 통계 (예측 아님)</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📌 최신 회차: <b>{latest['round']}회</b> ({latest['date']})",
        "🎱 당첨 번호: " + " · ".join(map(str, latest["numbers"])) + f" | 보너스 {latest['bonus']}",
        f"📚 확인한 회차: {len(draws)}회 | 데이터: {SOURCE_NAME}",
        "",
        "🔥 <b>누적 출현 상위 10 (과거 기록)</b>",
        ", ".join(f"{n}({frequency[n]}회)" for n in hot),
        "",
        "❄️ <b>누적 출현 하위 10 (과거 기록)</b>",
        ", ".join(f"{n}({frequency[n]}회)" for n in cold),
        "",
        "⏳ <b>가장 오래 미출현한 10개 (과거 기록)</b>",
        ", ".join(f"{n}({gap[n]}회 전)" for n in latest_absent),
        "",
        "🎯 <b>공정한 6/45 추첨의 한 조합 기준 이론 확률</b>",
        f"3개 이상 일치(5등 이상): <b>{odds['any_prize'] * 100:.4f}%</b>",
        f"1등: <b>1/{odds['total_combinations']:,}</b>",
        "평균 일치 개수: <b>0.8개</b>",
        "",
        "ℹ️ 과거 출현 횟수·미출현 기간은 다음 회차 번호의 확률을 높이지 않습니다.",
        "이 봇은 당첨 예측기가 아니며, 통계는 과거 기록 설명용입니다.",
    ]
    return "\n".join(lines)


def format_picks(draws, picks, target_round):
    summary = portfolio_summary(picks)
    lines = [
        "🎲 <b>SANI LOTTO — 무작위 분산 조합</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📌 기준: {draws[-1]['round']}회까지 | 대상: {target_round}회",
        f"조합 수: {len(picks)}개 | 서로 다른 조합으로 생성",
    ]
    for index, pick in enumerate(picks, 1):
        lines.append(f"{index:02d}. " + " · ".join(map(str, pick["numbers"])))
    lines.extend([
        "",
        f"조합 묶음 번호 분산: {summary['covered_numbers']}/45개 번호 사용, 번호별 사용 횟수 {summary['min_uses']}~{summary['max_uses']}회",
        f"두 조합 사이 최대 공통 번호: {summary['max_pair_overlap']}개",
        "",
        "⚠️ 분산 구성은 번호 예측이나 당첨 확률 상승을 뜻하지 않습니다.",
        "서로 다른 10개 조합의 1등 확률은 10/8,145,060 (약 1/814,506)입니다.",
        "구매 여부와 금액은 본인이 정한 예산 안에서 판단하세요.",
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
        "🏆 <b>SANI LOTTO — 추천 조합 결과 확인</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📌 대조 회차: <b>{draw['round']}회</b> ({draw['date']})",
        "🎱 당첨 번호: " + " · ".join(map(str, draw["numbers"])) + f" | 보너스 {draw['bonus']}",
        "",
        f"누적 확인: {results['checked_draws']}회 추첨 / {total}개 조합",
        f"누적 1등: {counts['1']} | 2등: {counts['2']} | 3등: {counts['3']}",
        f"누적 4등: {counts['4']} | 5등: {counts['5']} | 미당첨: {counts['0']}",
        f"3개 이상 일치 조합: {prize_tickets}/{total} ({(100 * prize_tickets / total) if total else 0:.3f}%)",
        "※ 조합 당첨 빈도이며 구매비·당첨금·수익률을 반영한 값이 아닙니다.",
        "",
        "<b>이번 회차 조합별 결과</b>",
    ]
    for index, pick in enumerate(picks, 1):
        rank, matches = rank_ticket(pick["numbers"], draw)
        result = f"{rank}등" if rank else "미당첨"
        lines.append(f"{index:02d}. {' · '.join(map(str, pick['numbers']))} — {result} ({matches}개 일치)")
    return "\n".join(lines)


def check_pending_results(draws, state):
    pending = state.get("pending_results", [])
    if not pending:
        return False
    by_round = {draw["round"]: draw for draw in draws}
    changed = False
    checked = set(state.get("checked_rounds", []))
    stats = state["results_stats"]

    for item in list(pending):
        target = int(item["target_round"])
        if target in checked:
            pending.remove(item)
            changed = True
            continue
        draw = by_round.get(target)
        if draw is None:
            log.info("%s회 결과가 아직 데이터에 없습니다.", target)
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
        state["checked_rounds"] = sorted(checked)[-500:]

        # Send before removing/saving the pending record. If delivery fails,
        # the result remains pending and can be retried on the next run.
        send_telegram(format_result(draw, item["picks"], stats))
        pending.remove(item)
        changed = True
        log.info("%s회 조합 결과 집계 완료", target)

    state["pending_results"] = pending
    if changed:
        save_json(STATE_FILE, state)
    return changed


def run():
    current = now_kst()
    weekday = current.weekday()  # Monday=0 ... Sunday=6
    state = load_state()

    # Sunday: check results for previously published picks.
    if weekday == 6:
        if not state.get("pending_results"):
            log.info("일요일: 확인할 추천 조합 결과가 없습니다.")
            return
        draws = sync_draws()
        check_pending_results(draws, state)
        return

    # Saturday: no routine message; draw results are checked on Sunday.
    if weekday == 5:
        log.info("토요일: 정기 발송 없음")
        return

    today = current.strftime("%Y-%m-%d")
    if state.get("analysis_sent_date") == today:
        log.info("오늘 분석은 이미 전송했습니다.")
        return

    draws = sync_draws()
    analysis = format_analysis(draws)
    is_friday = weekday == 4

    if is_friday:
        picks = make_diversified_picks(PICK_COUNT)
        target_round = draws[-1]["round"] + 1
        picks_message = format_picks(draws, picks, target_round)
        # Send first; only record a sent recommendation after Telegram confirms.
        send_telegram(analysis + "\n\n" + picks_message)
        state.setdefault("pending_results", [])
        if not any(int(row.get("target_round", 0)) == target_round
                   for row in state["pending_results"]):
            state["pending_results"].append({
                "target_round": target_round,
                "created_at": stamp(),
                "picks": picks,
            })
    else:
        send_telegram(analysis)

    state["analysis_sent_date"] = today
    save_json(STATE_FILE, state)
    log.info("분석 전송 완료 (%s)", "추천 조합 포함" if is_friday else "통계만")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        log.exception("실행 실패; 상태를 임의 초기화하지 않았습니다.")
        raise
