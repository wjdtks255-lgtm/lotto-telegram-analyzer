
import os,json,random,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import urlencode

KST=timezone(timedelta(hours=9))
BASE="https://www.dhlottery.co.kr"
DATA=Path("data")
DRAWS_FILE=DATA/"draws.json"
STATE_FILE=DATA/"state.json"
TOKEN=os.getenv("TELEGRAM_TOKEN","").strip()
CHAT_ID=(os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID") or "").strip()
TICKET_COUNT=5
HISTORY_WEEKS=30
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*","Referer":BASE+"/lt645/result"}

def now():
    return datetime.now(KST)

def read_json(path,default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError,TypeError):
        return default

def save_json(path,obj):
    DATA.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(path)

def request_json(url):
    req=Request(url,headers=HEADERS)
    with urlopen(req,timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))

def parse_row(row):
    try:
        rnd=int(row["ltEpsd"])
        nums=[int(row[f"tm{i}WnNo"]) for i in range(1,7)]
        bonus=int(row["bnsWnNo"])
        raw=str(row.get("ltRflYmd",""))
        date=(raw[:4]+"-"+raw[4:6]+"-"+raw[6:8]) if len(raw)>=8 and raw[:8].isdigit() else ""
        if rnd<1 or len(set(nums))!=6 or any(n<1 or n>45 for n in nums):
            return None
        if bonus<1 or bonus>45 or bonus in nums or not date:
            return None
        datetime.strptime(date,"%Y-%m-%d")
        return {"round":rnd,"date":date,"numbers":sorted(nums),"bonus":bonus}
    except (KeyError,ValueError,TypeError):
        return None

def fetch_one(rnd):
    url=f"{BASE}/lt645/selectPstLt645Info.do?{urlencode({'srchLtEpsd':rnd})}"
    obj=request_json(url)
    rows=(obj.get("data") or {}).get("list") or []
    for row in rows:
        draw=parse_row(row)
        if draw and draw["round"]==rnd:
            return draw
    return None

def fetch_batch(center):
    url=f"{BASE}/lt645/selectPstLt645InfoNew.do?"+urlencode({"srchDir":"center","srchLtEpsd":center})
    obj=request_json(url)
    rows=(obj.get("data") or {}).get("list") or []
    result={}
    for row in rows:
        draw=parse_row(row)
        if draw:
            result[draw["round"]]=draw
    return result

def estimated_round():
    # 1회: 2002-12-07. 토요일 추첨 전에는 이전 회차 기준.
    today=now()
    first=datetime(2002,12,7,tzinfo=KST)
    target=today.date()
    days=(target-first.date()).days
    guess=max(1,days//7+1)
    if today.weekday()==5 and (today.hour,today.minute)<(20,35):
        guess=max(1,guess-1)
    return guess

def find_latest():
    guess=estimated_round()
    for rnd in range(guess, max(0,guess-5),-1):
        try:
            draw=fetch_one(rnd)
            if draw:
                latest=rnd
                while latest<guess+3:
                    nxt=fetch_one(latest+1)
                    if not nxt:
                        break
                    latest+=1
                return latest
        except Exception as e:
            print(f"Latest lookup error at {rnd}: {e}")
            break
    raise RuntimeError("동행복권 최신 회차 조회 실패. 공식 데이터 API 응답을 확인하세요.")

def validate_history(draws):
    if not draws:
        raise RuntimeError("공식 당첨번호 데이터가 비어 있습니다.")
    draws=sorted(draws,key=lambda d:d["round"])
    for i,d in enumerate(draws):
        if i and d["round"]!=draws[i-1]["round"]+1:
            raise RuntimeError(f"회차 누락: {draws[i-1]['round']} 다음이 {d['round']}입니다.")
        if len(d["numbers"])!=6 or len(set(d["numbers"]))!=6:
            raise RuntimeError(f"{d['round']}회 번호 검증 실패")
    return draws

def sync_draws():
    DATA.mkdir(parents=True,exist_ok=True)
    old=read_json(DRAWS_FILE,{})
    existing={}
    # 과거 DEFAULT_DRAWS / 합성 데이터는 공식 출처 기록이 없으면 재사용하지 않음.
    if old.get("source")=="Donghaeng Lottery verified API" and isinstance(old.get("draws"),list):
        for d in old["draws"]:
            try:
                existing[int(d["round"])]=d
            except (KeyError,ValueError,TypeError):
                pass

    latest=find_latest()
    print(f"Official latest round: {latest}")

    # 기존 공식 데이터가 정상일 때만 새 회차부터 증분 갱신.
    valid_existing=False
    if existing:
        try:
            old_draws=validate_history(list(existing.values()))
            valid_existing=(old_draws[-1]["round"]<=latest and old_draws[-1]["round"]>=latest-3)
        except Exception:
            valid_existing=False

    if not valid_existing:
        print("Building clean official archive...")
        existing={}
        # 묶음 API로 초기 데이터 수집. 회차 경계는 개별 조회로 보완.
        for center in range(1,latest+1,15):
            try:
                existing.update(fetch_batch(center))
                print(f"Batch {center}: {len(existing)} rounds collected")
            except Exception as e:
                print(f"Batch {center} failed: {e}")
            time.sleep(0.15)
        # 묶음 API가 반환하지 않은 회차는 개별 API로 조회.
        missing=[r for r in range(1,latest+1) if r not in existing]
        for rnd in missing:
            try:
                draw=fetch_one(rnd)
                if draw:
                    existing[rnd]=draw
            except Exception as e:
                raise RuntimeError(f"{rnd}회 조회 실패: {e}") from e
            if rnd%50==0:
                print(f"Individual recovery: {rnd}/{latest}")
            time.sleep(0.08)
    else:
        for rnd in range(max(existing)+1,latest+1):
            draw=fetch_one(rnd)
            if not draw:
                raise RuntimeError(f"신규 {rnd}회 데이터가 아직 공개되지 않았습니다.")
            existing[rnd]=draw
            time.sleep(0.1)

    draws=validate_history([existing[r] for r in sorted(existing) if r<=latest])
    if draws[0]["round"]!=1 or draws[-1]["round"]!=latest:
        raise RuntimeError("전체 공식 데이터 범위가 올바르지 않습니다.")
    save_json(DRAWS_FILE,{"source":"Donghaeng Lottery verified API","updated_at":now().isoformat(),"draws":draws})
    print(f"Verified archive: {len(draws)} draws, latest={draws[-1]['round']} ({draws[-1]['date']})")
    return draws

def get_stats(draws):
    freq={n:0 for n in range(1,46)}
    last_seen={n:None for n in range(1,46)}
    for d in draws:
        for n in d["numbers"]:
            freq[n]+=1
            last_seen[n]=d["round"]
    latest=draws[-1]["round"]
    gaps={n:(latest-last_seen[n] if last_seen[n] else latest) for n in range(1,46)}
    recent=draws[-HISTORY_WEEKS:]
    recent_freq={n:sum(n in d["numbers"] for d in recent) for n in range(1,46)}
    return freq,gaps,recent_freq

def make_ticket(draws,mode="balanced"):
    freq,gaps,recent=get_stats(draws)
    pool=list(range(1,46))
    weights=[]
    for n in pool:
        if mode=="hot":
            w=1+freq[n]
        elif mode=="cold":
            w=1+max(freq.values())-freq[n]
        elif mode=="recent":
            w=1+recent[n]*2
        else:
            # 빈도·최근 출현·미출현 기간을 완만하게 섞음
            w=1+0.7*freq[n]+0.8*recent[n]+min(gaps[n],20)*0.12
        weights.append(max(0.1,w))
    chosen=[]
    while len(chosen)<6:
        n=random.choices(pool,weights=weights,k=1)[0]
        i=pool.index(n)
        chosen.append(n)
        pool.pop(i)
        weights.pop(i)
    return sorted(chosen)

def generate_tickets(draws,count=TICKET_COUNT):
    tickets=[]
    seen=set()
    methods=["balanced","hot","cold","recent","balanced"]
    for i in range(count):
        for _ in range(500):
            t=make_ticket(draws,methods[i%len(methods)])
            key=tuple(t)
            if key not in seen:
                seen.add(key)
                tickets.append({"method":methods[i%len(methods)],"numbers":t})
                break
        else:
            raise RuntimeError("추천 조합 중복 방지에 실패했습니다.")
    return tickets

def count_hits(ticket,draw):
    return len(set(ticket)&set(draw["numbers"]))

def backtest(draws,window=100):
    if len(draws)<window+2:
        return None
    tested=draws[-window:]
    total_hits=0
    rank_counts={3:0,4:0,5:0,6:0}
    for draw in tested:
        prior=[d for d in draws if d["round"]<draw["round"]]
        if not prior:
            continue
        ticket=make_ticket(prior,"balanced")
        hits=count_hits(ticket,draw)
        total_hits+=hits
        if hits>=3:
            rank_counts[hits]=rank_counts.get(hits,0)+1
    return {"draws":len(tested),"avg_hits":total_hits/len(tested),"rank_counts":rank_counts}

def telegram_send(message):
    if not TOKEN or not CHAT_ID:
        raise RuntimeError("TELEGRAM_TOKEN 또는 TELEGRAM_CHAT_ID GitHub Secret이 없습니다.")
    import urllib.request
    payload=json.dumps({"chat_id":CHAT_ID,"text":message,"parse_mode":"HTML","disable_web_page_preview":True}).encode()
    req=Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage",data=payload,headers={"Content-Type":"application/json"})
    with urlopen(req,timeout=25) as r:
        result=json.loads(r.read().decode())
    if not result.get("ok"):
        raise RuntimeError(f"Telegram 전송 실패: {result}")
    print("Telegram sent")

def format_analysis(draws,tickets,bt):
    latest=draws[-1]
    freq,gaps,recent=get_stats(draws)
    hot=sorted(freq,key=lambda n:freq[n],reverse=True)[:8]
    cold=sorted(freq,key=lambda n:freq[n])[:8]
    delayed=sorted(gaps,key=lambda n:gaps[n],reverse=True)[:8]
    lines=[
        "🎯 <b>SANI LOTTO ANALYZER</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📌 공식 최신 회차: <b>{latest['round']}회</b>",
        f"📅 추첨일: {latest['date']}",
        "🎱 당첨번호: "+" · ".join(map(str,latest["numbers"]))+f"  | 보너스 <b>{latest['bonus']}</b>",
        f"📚 분석 데이터: 1~{latest['round']}회 ({len(draws):,}회)",
        "",
        "📊 <b>번호 통계</b>",
        "🔥 누적 출현 상위: "+", ".join(f"{n}({freq[n]})" for n in hot),
        "❄️ 누적 출현 하위: "+", ".join(f"{n}({freq[n]})" for n in cold),
        "⏳ 장기 미출현: "+", ".join(f"{n}({gaps[n]}회)" for n in delayed),
        "",
        "🧪 <b>최근 구간 워크포워드 참고</b>"
    ]
    if bt:
        lines.append(f"검증 회차 {bt['draws']}회 | 게임당 평균 일치 {bt['avg_hits']:.2f}개")
        lines.append(f"3개 이상 일치 {bt['rank_counts'].get(3,0)}회 | 4개 이상 {bt['rank_counts'].get(4,0)}회 | 5개 이상 {bt['rank_counts'].get(5,0)}회 | 6개 일치 {bt['rank_counts'].get(6,0)}회")
    else:
        lines.append("검증 데이터 부족")
    lines.extend(["","🎲 <b>분석 추천 조합</b>"])
    for i,t in enumerate(tickets,1):
        lines.append(f"{i}. "+" · ".join(map(str,t["numbers"]))+f"  <i>({t['method']})</i>")
    lines.extend(["","⚠️ 과거 통계는 다음 추첨 결과를 예측하지 못하며 모든 조합의 1등 확률은 같습니다.","📎 출처: 동행복권 회차별 결과 조회"])
    return "\n".join(lines)

def main():
    print("="*42)
    print("SANI LOTTO ANALYZER")
    print("="*42)
    draws=sync_draws()
    state=read_json(STATE_FILE,{})
    today=now().strftime("%Y-%m-%d")
    latest=draws[-1]["round"]
    tickets=generate_tickets(draws)
    bt=backtest(draws)
    print(f"Latest round={latest}; draws={len(draws)}; KST={now().isoformat()}")

    # 하루 한 번 분석 알림
    if state.get("daily_sent")!=today:
        telegram_send(format_analysis(draws,tickets,bt))
        state["daily_sent"]=today
        state["daily_round"]=latest
        save_json(STATE_FILE,state)

    # 토요일 오전 10시 이후, 최신 회차가 아직 당첨 결과가 반영되기 전이면
    # 해당 주 추천을 한 번 발송한다. 추첨 후에는 다음 회차 분석에 사용.
    current=now()
    saturday=current.weekday()==5 and current.hour>=10
    final_key=f"{current.strftime('%Y-%m-%d')}-{latest}"
    if saturday and state.get("weekly_sent")!=final_key:
        msg=[
            "🏁 <b>SANI LOTTO WEEKLY PICKS</b>",
            "━━━━━━━━━━━━━━━━━━",
            f"📅 작성 시각: {current.strftime('%Y-%m-%d %H:%M')} KST",
            f"📚 분석 기준: {latest}회까지의 공식 데이터",
            "",
            "🎯 <b>이번 주 추천 조합</b>"
        ]
        for i,t in enumerate(tickets,1):
            msg.append(f"{i}. "+" · ".join(map(str,t["numbers"])))
        msg.extend(["","⚠️ 추천 조합은 참고용이며 당첨을 보장하지 않습니다."])
        telegram_send("\n".join(msg))
        state["weekly_sent"]=final_key
        save_json(STATE_FILE,state)

    print("Completed successfully.")

if __name__=="__main__":
    main()
