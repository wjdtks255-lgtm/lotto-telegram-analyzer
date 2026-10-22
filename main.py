
import os,json,random,time
from pathlib import Path
from datetime import datetime,timedelta,timezone
from urllib.request import Request,urlopen
from urllib.parse import urlencode

KST=timezone(timedelta(hours=9))
BASE="https://www.dhlottery.co.kr/lt645/"
DATA=Path("data")
DRAW_FILE=DATA/"draws.json"
STATE_FILE=DATA/"state.json"
TOKEN=os.getenv("TELEGRAM_TOKEN","").strip()
CHAT=(os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID") or "").strip()
COUNT=5
HEAD={"User-Agent":"Mozilla/5.0","Accept":"application/json, text/plain, */*","Referer":"https://www.dhlottery.co.kr/lt645/result"}

def log(s):
    print(f"[{datetime.now(KST).strftime('%H:%M:%S')}] {s}",flush=True)

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

def get_json(url):
    req=Request(url,headers=HEAD)
    with urlopen(req,timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

def fetch(rnd):
    url=BASE+"selectPstLt645Info.do?"+urlencode({"srchLtEpsd":rnd})
    obj=get_json(url)
    rows=(obj.get("data") or {}).get("list") or []
    if not rows:
        return None
    r=rows[0]
    try:
        n=int(r["ltEpsd"])
        nums=[int(r[f"tm{i}WnNo"]) for i in range(1,7)]
        bonus=int(r["bnsWnNo"])
        raw=str(r.get("ltRflYmd",""))
        date=f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw)>=8 and raw[:8].isdigit() else ""
        if n!=rnd or len(set(nums))!=6 or any(x<1 or x>45 for x in nums):
            raise ValueError("번호 검증 실패")
        if bonus<1 or bonus>45 or bonus in nums:
            raise ValueError("보너스 번호 검증 실패")
        datetime.strptime(date,"%Y-%m-%d")
        return {"round":n,"date":date,"numbers":sorted(nums),"bonus":bonus}
    except (KeyError,ValueError,TypeError) as e:
        raise RuntimeError(f"{rnd}회 응답 검증 실패: {e}") from e

def latest_round():
    # 1회차 추첨일 2002-12-07 기준으로 후보 회차 계산
    now=datetime.now(KST)
    first=datetime(2002,12,7,tzinfo=KST)
    guess=max(1,(now.date()-first.date()).days//7+1)
    if now.weekday()==5 and (now.hour,now.minute)<(20,35):
        guess=max(1,guess-1)
    for n in range(guess, max(0,guess-5),-1):
        try:
            if fetch(n):
                log(f"공식 최신 회차 확인: {n}")
                return n
        except Exception as e:
            log(f"최신 회차 조회 오류: {e}")
            raise
    raise RuntimeError("최신 회차 확인 실패")

def validate(draws):
    draws=sorted(draws,key=lambda x:x["round"])
    if not draws:
        raise RuntimeError("당첨 데이터가 비어 있습니다.")
    for i,d in enumerate(draws):
        if i and d["round"]!=draws[i-1]["round"]+1:
            raise RuntimeError(f"회차 누락: {draws[i-1]['round']} -> {d['round']}")
    return draws

def sync_draws():
    DATA.mkdir(parents=True,exist_ok=True)
    old=load(DRAW_FILE,{})
    saved={}
    if old.get("source")=="Donghaeng Lottery verified API":
        for d in old.get("draws",[]):
            try:
                saved[int(d["round"])]=d
            except (KeyError,ValueError,TypeError):
                pass

    latest=latest_round()
    if saved and max(saved)==latest:
        try:
            draws=validate([saved[n] for n in sorted(saved)])
            if draws[0]["round"]==1 and draws[-1]["round"]==latest:
                log(f"기존 데이터 재사용: {len(draws)}회")
                return draws
        except Exception:
            pass

    if saved and max(saved)<latest and min(saved)==1:
        log("신규 회차만 수집")
        for n in range(max(saved)+1,latest+1):
            d=fetch(n)
            if not d:
                raise RuntimeError(f"{n}회 데이터 미공개 또는 응답 없음")
            saved[n]=d
        draws=validate([saved[n] for n in sorted(saved)])
    else:
        log("최초 공식 데이터 수집 시작")
        saved={}
        # 개별 조회는 속도가 느리므로 최초 수집은 요청 간격을 짧게 유지
        # 서버가 요청을 제한하면 오류로 중단해 재시도할 수 있게 함
        for n in range(1,latest+1):
            d=fetch(n)
            if not d:
                raise RuntimeError(f"{n}회 데이터 조회 실패. 공식 사이트 응답 확인 필요")
            saved[n]=d
            if n%25==0:
                log(f"수집 진행: {n}/{latest}")
        draws=validate(list(saved.values()))

    if draws[0]["round"]!=1 or draws[-1]["round"]!=latest:
        raise RuntimeError("공식 데이터 범위 검증 실패")
    save(DRAW_FILE,{"source":"Donghaeng Lottery verified API","updated_at":datetime.now(KST).isoformat(),"draws":draws})
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
        if mode=="hot": w=1+freq[n]
        elif mode=="cold": w=1+max(freq.values())-freq[n]
        elif mode=="recent": w=1+recent[n]*2
        else: w=1+freq[n]*.7+recent[n]*.8+min(gap[n],20)*.12
        weights.append(max(.1,w))
    out=[]
    while len(out)<6:
        n=random.choices(pool,weights=weights,k=1)[0]
        i=pool.index(n)
        out.append(n)
        pool.pop(i)
        weights.pop(i)
    return sorted(out)

def recommendations(draws):
    result=[]
    seen=set()
    modes=["balanced","hot","cold","recent","balanced"]
    for i in range(COUNT):
        for _ in range(300):
            nums=ticket(draws,modes[i%len(modes)])
            key=tuple(nums)
            if key not in seen:
                seen.add(key)
                result.append({"method":modes[i%len(modes)],"numbers":nums})
                break
    return result

def backtest(draws,window=100):
    test=draws[-window:]
    hits=[]
    for i,d in enumerate(test):
        prior=[x for x in draws if x["round"]<d["round"]]
        if not prior:
            continue
        hits.append(len(set(ticket(prior))&set(d["numbers"])))
    if not hits:
        return None
    return {"n":len(hits),"avg":sum(hits)/len(hits),"ge3":sum(x>=3 for x in hits),"ge4":sum(x>=4 for x in hits)}

def send(msg):
    if not TOKEN or not CHAT:
        raise RuntimeError("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID Secret 확인 필요")
    payload=json.dumps({"chat_id":CHAT,"text":msg,"parse_mode":"HTML","disable_web_page_preview":True}).encode()
    req=Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage",data=payload,headers={"Content-Type":"application/json"})
    with urlopen(req,timeout=20) as r:
        result=json.loads(r.read().decode())
    if not result.get("ok"):
        raise RuntimeError(f"텔레그램 전송 실패: {result}")
    log("텔레그램 전송 완료")

def report(draws,picks,bt):
    d=draws[-1]
    freq,gap,recent=statistics(draws)
    hot=sorted(freq,key=freq.get,reverse=True)[:8]
    cold=sorted(freq,key=freq.get)[:8]
    delayed=sorted(gap,key=gap.get,reverse=True)[:8]
    lines=[
        "🎯 <b>SANI LOTTO ANALYZER</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📌 최신 공식 회차: <b>{d['round']}회</b> ({d['date']})",
        "🎱 "+" · ".join(map(str,d["numbers"]))+f"  | 보너스 {d['bonus']}",
        f"📚 분석 범위: 1~{d['round']}회 / 총 {len(draws)}회",
        "",
        "📊 <b>번호 통계</b>",
        "🔥 출현 상위: "+", ".join(f"{n}({freq[n]})" for n in hot),
        "❄️ 출현 하위: "+", ".join(f"{n}({freq[n]})" for n in cold),
        "⏳ 미출현 간격 상위: "+", ".join(f"{n}({gap[n]}회)" for n in delayed),
        "",
        "🧪 <b>워크포워드 백테스트</b>"
    ]
    if bt:
        lines.append(f"검증 {bt['n']}회 | 평균 일치 {bt['avg']:.2f}개 | 3개 이상 {bt['ge3']}회 | 4개 이상 {bt['ge4']}회")
    else:
        lines.append("검증 데이터 부족")
    lines.extend(["","🎲 <b>추천 조합</b>"])
    for i,p in enumerate(picks,1):
        lines.append(f"{i}. "+" · ".join(map(str,p["numbers"]))+f"  <i>{p['method']}</i>")
    lines.extend(["","⚠️ 모든 조합의 당첨 확률은 동일하며 추천은 당첨을 보장하지 않습니다."])
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
        state["last_round"]=draws[-1]["round"]
        save(STATE_FILE,state)
    else:
        log("오늘 일일 알림은 이미 전송됨")
    # 토요일 추천 알림: 당일 하루 한 번
    dt=datetime.now(KST)
    key=dt.strftime("%Y-%m-%d")
    if dt.weekday()==5 and dt.hour>=10 and state.get("weekly_sent")!=key:
        msg="🏁 <b>SANI LOTTO WEEKLY PICKS</b>\n"
        msg+=f"분석 기준: {draws[-1]['round']}회까지\n\n"
        msg+="\n".join(f"{i}. "+" · ".join(map(str,p["numbers"])) for i,p in enumerate(picks,1))
        msg+="\n\n⚠️ 참고용 조합이며 당첨을 보장하지 않습니다."
        send(msg)
        state["weekly_sent"]=key
        save(STATE_FILE,state)
    log("작업 완료")

if __name__=="__main__":
    main()
