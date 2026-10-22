
import os,json,random,time
from pathlib import Path
from datetime import datetime,timedelta,timezone
from urllib.request import Request,urlopen

KST=timezone(timedelta(hours=9))
MIRROR="https://smok95.github.io/lotto/results/"
DATA=Path("data")
DRAW_FILE=DATA/"draws.json"
STATE_FILE=DATA/"state.json"
TOKEN=os.getenv("TELEGRAM_TOKEN","").strip()
CHAT=(os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID") or "").strip()
COUNT=5
HEAD={"User-Agent":"Mozilla/5.0","Accept":"application/json"}

def log(msg):
    print(f"[{datetime.now(KST):%H:%M:%S}] {msg}",flush=True)

def load(path,default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError):
        return default

def save(path,obj):
    DATA.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj,ensure_ascii=False),encoding="utf-8")
    tmp.replace(path)

def get_json(url,timeout=25,retries=2):
    last=None
    for attempt in range(retries+1):
        try:
            req=Request(url,headers=HEAD)
            with urlopen(req,timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last=e
            log(f"다운로드 실패 {attempt+1}/{retries+1}: {type(e).__name__}")
            if attempt<retries:
                time.sleep(attempt+1)
    raise RuntimeError(f"데이터 다운로드 실패: {last}")

def parse(row):
    try:
        rnd=int(row.get("draw_no",row.get("round",0)))
        nums=[int(x) for x in row["numbers"]]
        bonus=int(row.get("bonus_no",row.get("bonus",0)))
        raw=str(row.get("date",row.get("draw_date","")))[:10]
        datetime.strptime(raw,"%Y-%m-%d")
        if rnd<1 or len(nums)!=6 or len(set(nums))!=6:
            return None
        if any(n<1 or n>45 for n in nums) or bonus not in range(1,46) or bonus in nums:
            return None
        return {"round":rnd,"date":raw,"numbers":sorted(nums),"bonus":bonus}
    except (KeyError,ValueError,TypeError):
        return None

def get_latest():
    obj=get_json(MIRROR+"latest.json")
    if isinstance(obj,list):
        obj=max((x for x in obj if parse(x)),key=lambda x:int(x.get("draw_no",x.get("round",0))),default=None)
    d=parse(obj) if isinstance(obj,dict) else None
    if not d:
        raise RuntimeError("최신 회차 JSON 형식 또는 번호 검증 실패")
    log(f"최신 회차 확인: {d['round']}회 ({d['date']})")
    return d

def get_all():
    log("전체 당첨 데이터 다운로드: JSON 1회 요청")
    obj=get_json(MIRROR+"all.json",timeout=40)
    if isinstance(obj,dict):
        rows=obj.get("results",obj.get("draws",[]))
    elif isinstance(obj,list):
        rows=obj
    else:
        rows=[]
    draws={}
    for row in rows:
        if isinstance(row,dict):
            d=parse(row)
            if d:
                draws[d["round"]]=d
    if not draws:
        raise RuntimeError("전체 데이터에서 유효한 회차를 찾지 못했습니다")
    return draws

def validate(draws,latest):
    draws=sorted(draws,key=lambda x:x["round"])
    if not draws or draws[0]["round"]!=1:
        raise RuntimeError("1회부터 시작하는 데이터가 없습니다")
    for i,d in enumerate(draws):
        if i and d["round"]!=draws[i-1]["round"]+1:
            raise RuntimeError(f"회차 누락: {draws[i-1]['round']} -> {d['round']}")
    if draws[-1]["round"]!=latest["round"]:
        raise RuntimeError(f"최신 회차 불일치: 저장 {draws[-1]['round']} / 조회 {latest['round']}")
    if draws[-1]["numbers"]!=latest["numbers"] or draws[-1]["bonus"]!=latest["bonus"]:
        raise RuntimeError("최신 회차 번호가 최신 조회 결과와 다릅니다")
    return draws

def sync_draws():
    DATA.mkdir(parents=True,exist_ok=True)
    latest=get_latest()
    old=load(DRAW_FILE,{})
    saved={}
    if old.get("source")=="smok95/lotto":
        for row in old.get("draws",[]):
            d=parse(row)
            if d:
                saved[d["round"]]=d

    # 검증된 저장 데이터가 최신이면 네트워크 요청은 최신 JSON 1회로 끝
    if saved and min(saved)==1 and max(saved)==latest["round"]:
        draws=validate(list(saved.values()),latest)
        log(f"저장 데이터 재사용: {len(draws)}회")
        return draws

    # 최초 실행 또는 회차가 추가된 경우 전체 JSON을 한 번 받아 갱신
    all_draws=get_all()
    if latest["round"] not in all_draws:
        raise RuntimeError("전체 데이터에 최신 회차가 없습니다")
    if all_draws[latest["round"]]["numbers"]!=latest["numbers"]:
        raise RuntimeError("전체 데이터와 최신 회차 번호가 불일치합니다")
    draws=validate(list(all_draws.values()),latest)
    save(DRAW_FILE,{
        "source":"smok95/lotto",
        "updated_at":datetime.now(KST).isoformat(),
        "draws":draws
    })
    log(f"데이터 저장 완료: {len(draws)}회")
    return draws

def statistics(draws):
    freq={n:0 for n in range(1,46)}
    last={n:0 for n in range(1,46)}
    recent={n:0 for n in range(1,46)}
    for d in draws:
        for n in d["numbers"]:
            freq[n]+=1
            last[n]=d["round"]
    for d in draws[-30:]:
        for n in d["numbers"]:
            recent[n]+=1
    latest=draws[-1]["round"]
    gap={n:latest-last[n] if last[n] else latest for n in range(1,46)}
    return freq,gap,recent

def ticket(draws,mode="balanced"):
    freq,gap,recent=statistics(draws)
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
            w=1+freq[n]*.7+recent[n]*.8+min(gap[n],20)*.12
        weights.append(max(.1,w))
    result=[]
    while len(result)<6:
        n=random.choices(pool,weights=weights,k=1)[0]
        i=pool.index(n)
        result.append(n)
        pool.pop(i)
        weights.pop(i)
    return sorted(result)

def recommendations(draws):
    result=[]
    seen=set()
    modes=["balanced","hot","cold","recent","balanced"]
    for i in range(COUNT):
        for _ in range(500):
            nums=ticket(draws,modes[i%len(modes)])
            if tuple(nums) not in seen:
                seen.add(tuple(nums))
                result.append({"method":modes[i%len(modes)],"numbers":nums})
                break
        else:
            raise RuntimeError("추천 조합 생성 실패")
    return result

def backtest(draws,window=100):
    hits=[]
    for d in draws[-window:]:
        prior=[x for x in draws if x["round"]<d["round"]]
        if prior:
            hits.append(len(set(ticket(prior))&set(d["numbers"])))
    if not hits:
        return None
    return {"n":len(hits),"avg":sum(hits)/len(hits),"ge3":sum(x>=3 for x in hits),"ge4":sum(x>=4 for x in hits)}

def send(msg):
    if not TOKEN or not CHAT:
        raise RuntimeError("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID Secret 확인 필요")
    payload=json.dumps({
        "chat_id":CHAT,"text":msg,"parse_mode":"HTML",
        "disable_web_page_preview":True
    }).encode()
    req=Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage",data=payload,headers={"Content-Type":"application/json"})
    with urlopen(req,timeout=20) as r:
        result=json.loads(r.read().decode())
    if not result.get("ok"):
        raise RuntimeError(f"Telegram 전송 실패: {result}")
    log("텔레그램 전송 완료")

def report(draws,picks,bt):
    d=draws[-1]
    freq,gap,_=statistics(draws)
    hot=sorted(freq,key=freq.get,reverse=True)[:8]
    cold=sorted(freq,key=freq.get)[:8]
    delayed=sorted(gap,key=gap.get,reverse=True)[:8]
    lines=[
        "🎯 <b>SANI LOTTO ANALYZER</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📌 최신 회차: <b>{d['round']}회</b>",
        f"📅 추첨일: {d['date']}",
        "🎱 당첨번호: "+" · ".join(map(str,d["numbers"]))+f" | 보너스 {d['bonus']}",
        f"📚 분석 데이터: 총 {len(draws)}회",
        "",
        "📊 <b>번호 통계</b>",
        "🔥 누적 출현 상위: "+", ".join(f"{n}({freq[n]})" for n in hot),
        "❄️ 누적 출현 하위: "+", ".join(f"{n}({freq[n]})" for n in cold),
        "⏳ 미출현 간격 상위: "+", ".join(f"{n}({gap[n]}회)" for n in delayed),
        "",
        "🧪 <b>최근 100회 워크포워드 백테스트</b>"
    ]
    if bt:
        lines.append(f"검증 {bt['n']}회 | 평균 일치 {bt['avg']:.2f}개 | 3개 이상 {bt['ge3']}회 | 4개 이상 {bt['ge4']}회")
    else:
        lines.append("검증 데이터 부족")
    lines.extend(["","🎲 <b>추천 조합</b>"])
    for i,p in enumerate(picks,1):
        lines.append(f"{i}. "+" · ".join(map(str,p["numbers"]))+f" <i>({p['method']})</i>")
    lines.extend(["","⚠️ 통계 기반 참고용 조합입니다. 모든 조합의 당첨 확률은 동일하며 당첨을 보장하지 않습니다."])
    return "\n".join(lines)

def main():
    log("로또 분석 시작")
    draws=sync_draws()
    state=load(STATE_FILE,{})
    today=datetime.now(KST).strftime("%Y-%m-%d")
    picks=recommendations(draws)
    bt=backtest(draws)
    if state.get("daily_sent")!=today:
        send(report(draws,picks,bt))
        state["daily_sent"]=today
        save(STATE_FILE,state)
    else:
        log("오늘 일일 알림은 이미 전송됨")

    dt=datetime.now(KST)
    week_key=dt.strftime("%Y-%m-%d")
    if dt.weekday()==5 and dt.hour>=10 and state.get("weekly_sent")!=week_key:
        msg="🏁 <b>SANI LOTTO WEEKLY PICKS</b>\n"
        msg+=f"분석 기준: {draws[-1]['round']}회까지\n\n"
        msg+="\n".join(f"{i}. "+" · ".join(map(str,p["numbers"])) for i,p in enumerate(picks,1))
        msg+="\n\n⚠️ 참고용 조합이며 당첨을 보장하지 않습니다."
        send(msg)
        state["weekly_sent"]=week_key
        save(STATE_FILE,state)
    log("작업 완료")

if __name__=="__main__":
    main()
