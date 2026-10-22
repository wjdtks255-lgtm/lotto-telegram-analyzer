
import os,json,time,random
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
COUNT=10
BACKTEST_ROUNDS=100
HEAD={"User-Agent":"Mozilla/5.0","Accept":"application/json"}

def log(s):
    print(f"[{datetime.now(KST):%H:%M:%S}] {s}",flush=True)

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
    err=None
    for i in range(retries+1):
        try:
            req=Request(url,headers=HEAD)
            with urlopen(req,timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            err=e
            log(f"다운로드 실패 {i+1}/{retries+1}: {type(e).__name__}")
            if i<retries:
                time.sleep(i+1)
    raise RuntimeError(f"데이터 다운로드 실패: {err}")

def parse(row):
    try:
        rnd=int(row.get("draw_no",row.get("round",0)))
        nums=[int(x) for x in row["numbers"]]
        bonus=int(row.get("bonus_no",row.get("bonus",0)))
        date=str(row.get("date",row.get("draw_date","")))[:10]
        datetime.strptime(date,"%Y-%m-%d")
        if rnd<1 or len(nums)!=6 or len(set(nums))!=6:
            return None
        if any(n<1 or n>45 for n in nums) or bonus not in range(1,46) or bonus in nums:
            return None
        return {"round":rnd,"date":date,"numbers":sorted(nums),"bonus":bonus}
    except (KeyError,ValueError,TypeError):
        return None

def get_latest():
    obj=get_json(MIRROR+"latest.json")
    if isinstance(obj,list):
        rows=[parse(x) for x in obj if isinstance(x,dict)]
        rows=[x for x in rows if x]
        d=max(rows,key=lambda x:x["round"]) if rows else None
    else:
        d=parse(obj) if isinstance(obj,dict) else None
    if not d:
        raise RuntimeError("최신 회차 데이터 검증 실패")
    log(f"최신 회차: {d['round']}회 ({d['date']})")
    return d

def get_all():
    obj=get_json(MIRROR+"all.json",timeout=40)
    rows=obj.get("results",obj.get("draws",[])) if isinstance(obj,dict) else obj
    if not isinstance(rows,list):
        raise RuntimeError("전체 데이터 형식 오류")
    out={}
    for row in rows:
        if isinstance(row,dict):
            d=parse(row)
            if d:
                out[d["round"]]=d
    if not out:
        raise RuntimeError("유효한 회차 데이터 없음")
    return out

def validate(draws,latest):
    draws=sorted(draws,key=lambda x:x["round"])
    if not draws or draws[0]["round"]!=1:
        raise RuntimeError("1회차 데이터가 없습니다")
    for i,d in enumerate(draws):
        if i and d["round"]!=draws[i-1]["round"]+1:
            raise RuntimeError(f"회차 누락: {draws[i-1]['round']} -> {d['round']}")
    last=draws[-1]
    if last["round"]!=latest["round"] or last["numbers"]!=latest["numbers"] or last["bonus"]!=latest["bonus"]:
        raise RuntimeError("최신 회차 또는 당첨번호 불일치")
    return draws

def sync_draws():
    DATA.mkdir(parents=True,exist_ok=True)
    latest=get_latest()
    old=load(DRAW_FILE,{})
    saved={}
    if old.get("source")=="smok95/lotto":
        for row in old.get("draws",[]):
            if isinstance(row,dict):
                d=parse(row)
                if d:
                    saved[d["round"]]=d
    if saved and min(saved)==1 and max(saved)==latest["round"]:
        try:
            draws=validate(list(saved.values()),latest)
            log(f"저장 데이터 재사용: {len(draws)}회")
            return draws
        except Exception as e:
            log(f"저장 데이터 재검증: {e}")
    all_draws=get_all()
    if latest["round"] not in all_draws:
        raise RuntimeError("전체 데이터에 최신 회차가 없습니다")
    d=all_draws[latest["round"]]
    if d["numbers"]!=latest["numbers"] or d["bonus"]!=latest["bonus"]:
        raise RuntimeError("전체 데이터와 최신 회차 데이터 불일치")
    draws=validate(list(all_draws.values()),latest)
    save(DRAW_FILE,{"source":"smok95/lotto","updated_at":datetime.now(KST).isoformat(),"draws":draws})
    log(f"전체 데이터 저장: {len(draws)}회")
    return draws

def stats(draws):
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

def make_ticket(draws,mode="balanced",seed=None):
    rng=random.Random(seed)
    freq,gap,recent=stats(draws)
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
    out=[]
    while len(out)<6:
        n=rng.choices(pool,weights=weights,k=1)[0]
        i=pool.index(n)
        out.append(n)
        pool.pop(i)
        weights.pop(i)
    return sorted(out)

def recommendations(draws):
    modes=["balanced","hot","cold","recent"]
    out=[]
    seen=set()
    for i in range(COUNT):
        for _ in range(1000):
            nums=make_ticket(draws,modes[i%len(modes)])
            if tuple(nums) not in seen:
                seen.add(tuple(nums))
                out.append({"method":modes[i%len(modes)],"numbers":nums})
                break
        else:
            raise RuntimeError("추천 조합 생성 실패")
    return out

def backtest(draws,window=BACKTEST_ROUNDS):
    test=draws[-min(window,len(draws)-1):]
    hits=[]
    for d in test:
        prior=[x for x in draws if x["round"]<d["round"]]
        if not prior:
            continue
        seen=set()
        for j in range(COUNT):
            nums=make_ticket(prior,"balanced",seed=d["round"]*1000+j)
            if tuple(nums) not in seen:
                seen.add(tuple(nums))
                hits.append(len(set(nums)&set(d["numbers"])))
    if not hits:
        return None
    dist={i:sum(h==i for h in hits) for i in range(7)}
    return {"rounds":len(test),"games":len(hits),"avg":sum(hits)/len(hits),
            "dist":dist,"ge2":sum(h>=2 for h in hits),
            "ge3":sum(h>=3 for h in hits),"ge4":sum(h>=4 for h in hits)}

def send(msg):
    if not TOKEN or not CHAT:
        raise RuntimeError("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID 확인 필요")
    payload=json.dumps({"chat_id":CHAT,"text":msg,"parse_mode":"HTML",
                        "disable_web_page_preview":True}).encode()
    req=Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                data=payload,headers={"Content-Type":"application/json"})
    with urlopen(req,timeout=20) as r:
        result=json.loads(r.read().decode())
    if not result.get("ok"):
        raise RuntimeError(f"텔레그램 전송 실패: {result}")
    log("텔레그램 전송 완료")

def analysis_report(draws,bt):
    d=draws[-1]
    freq,gap,recent=stats(draws)
    hot=sorted(freq,key=lambda n:(freq[n],recent[n]),reverse=True)[:10]
    cold=sorted(freq,key=lambda n:(freq[n],-recent[n]))[:10]
    recent_top=sorted(recent,key=lambda n:(recent[n],freq[n]),reverse=True)[:10]
    delayed=sorted(gap,key=lambda n:gap[n],reverse=True)[:10]
    lines=[
        "📊 <b>SANI LOTTO — DAILY ANALYSIS</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📌 최신 회차: <b>{d['round']}회</b> ({d['date']})",
        "🎱 당첨번호: "+" · ".join(map(str,d["numbers"]))+f" | 보너스 {d['bonus']}",
        f"📚 분석 데이터: 총 {len(draws)}회",
        "",
        "🔥 <b>누적 출현 상위 10</b>",
        ", ".join(f"{n}({freq[n]}회)" for n in hot),
        "",
        "❄️ <b>누적 출현 하위 10</b>",
        ", ".join(f"{n}({freq[n]}회)" for n in cold),
        "",
        "⚡ <b>최근 30회 출현 상위 10</b>",
        ", ".join(f"{n}({recent[n]}회)" for n in recent_top),
        "",
        "⏳ <b>장기 미출현 상위 10</b>",
        ", ".join(f"{n}({gap[n]}회)" for n in delayed),
        "",
        "🧪 <b>최근 100회 워크포워드 백테스트</b>"
    ]
    if bt:
        lines.extend([
            f"검증 회차: {bt['rounds']}회 | 평가 조합: {bt['games']}개",
            f"조합당 평균 일치: {bt['avg']:.3f}개",
            f"2개 일치: {bt['dist'][2]}회 | 3개: {bt['dist'][3]}회",
            f"4개: {bt['dist'][4]}회 | 5개: {bt['dist'][5]}회 | 6개: {bt['dist'][6]}회",
            f"2개 이상: {bt['ge2']}회 ({bt['ge2']/bt['games']*100:.2f}%)",
            f"3개 이상: {bt['ge3']}회 ({bt['ge3']/bt['games']*100:.2f}%)",
            f"4개 이상: {bt['ge4']}회 ({bt['ge4']/bt['games']*100:.2f}%)"
        ])
    else:
        lines.append("백테스트 데이터 부족")
    lines.extend(["","ℹ️ 과거 통계는 미래 당첨을 보장하지 않습니다."])
    return "\n".join(lines)

def picks_report(draws,picks):
    lines=["🎯 <b>SANI LOTTO — FRIDAY PICKS</b>",
           "━━━━━━━━━━━━━━━━━━",
           f"📌 분석 기준: {draws[-1]['round']}회까지",
           "🎲 <b>이번 주 추천 조합 10개</b>"]
    for i,p in enumerate(picks,1):
        lines.append(f"{i:02d}. "+" · ".join(map(str,p["numbers"]))+f" <i>({p['method']})</i>")
    lines.extend(["","📌 다음 추첨 회차 결과를 확인해 성적표에 반영합니다.",
                  "⚠️ 당첨을 보장하지 않습니다."])
    return "\n".join(lines)

def rank_ticket(nums,draw):
    win=set(draw["numbers"])
    matches=len(set(nums)&win)
    bonus=draw["bonus"]
    if matches==6:
        return 1,matches
    if matches==5 and bonus in nums:
        return 2,matches
    if matches==5:
        return 3,matches
    if matches==4:
        return 4,matches
    if matches==3:
        return 5,matches
    return 0,matches

def result_report(draw,picks,results):
    counts=results["counts"]
    total=results["total_tickets"]
    wins=sum(counts[str(i)] for i in range(1,6))
    lines=[
        "🏆 <b>SANI LOTTO — OFFICIAL RESULT CHECK</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📌 대조 회차: <b>{draw['round']}회</b>",
        f"📅 추첨일: {draw['date']}",
        "🎱 당첨번호: "+" · ".join(map(str,draw["numbers"]))+f" | 보너스 {draw['bonus']}",
        "",
        "🥇 <b>누적 등수별 당첨 조합</b>",
        f"🥇 1등: <b>{counts['1']}회</b>",
        f"🥈 2등: <b>{counts['2']}회</b>",
        f"🥉 3등: <b>{counts['3']}회</b>",
        f"🏅 4등: <b>{counts['4']}회</b>",
        f"🎖️ 5등: <b>{counts['5']}회</b>",
        f"🎟️ 미당첨 조합: {counts['0']}회",
        "",
        "📈 <b>누적 성적</b>",
        f"검사한 추천 조합: {total}개",
        f"당첨 등수 발생 조합: {wins}개",
        f"당첨 조합 비율: {wins/total*100:.2f}%" if total else "당첨 조합 비율: 0%",
        "",
        "📋 <b>이번 회차 추천 조합 결과</b>"
    ]
    for i,p in enumerate(picks,1):
        rank,matches=rank_ticket(p["numbers"],draw)
        label=f"{rank}등" if rank else "미당첨"
        lines.append(f"{i:02d}. {' · '.join(map(str,p['numbers']))} — {label} ({matches}개 일치)")
    lines.extend(["","ℹ️ 등수 통계는 봇이 생성해 저장한 추천 조합에 대한 집계입니다."])
    return "\n".join(lines)

def check_pending_results(draws,state):
    pending=state.get("pending_results",[])
    if not pending:
        return False
    by_round={d["round"]:d for d in draws}
    changed=False
    for item in pending[:]:
        target=int(item["target_round"])
        if target in state.get("checked_rounds",[]):
            pending.remove(item)
            changed=True
            continue
        draw=by_round.get(target)
        if not draw:
            log(f"{target}회 결과 미공개 또는 데이터 미갱신")
            continue
        # 누적 통계 초기화 및 이전 데이터 호환
        results=state.setdefault("results_stats",{
            "counts":{"0":0,"1":0,"2":0,"3":0,"4":0,"5":0},
            "total_tickets":0,"checked_draws":0,"history":[]
        })
        counts=results.setdefault("counts",{"0":0,"1":0,"2":0,"3":0,"4":0,"5":0})
        for k in ["0","1","2","3","4","5"]:
            counts.setdefault(k,0)
        for p in item["picks"]:
            rank,matches=rank_ticket(p["numbers"],draw)
            counts[str(rank)]+=1
            results["total_tickets"]+=1
        results["checked_draws"]+=1
        results["history"].append({
            "round":target,"date":draw["date"],
            "numbers":draw["numbers"],"bonus":draw["bonus"],
            "picks":item["picks"]
        })
        results["history"]=results["history"][-100:]
        state.setdefault("checked_rounds",[]).append(target)
        state["checked_rounds"]=state["checked_rounds"][-200:]
        send(result_report(draw,item["picks"],results))
        pending.remove(item)
        changed=True
        log(f"{target}회 추천 조합 결과 집계 완료")
    state["pending_results"]=pending
    if changed:
        save(STATE_FILE,state)
    return changed

def main():
    now=datetime.now(KST)
    weekday=now.weekday()
    state=load(STATE_FILE,{})

    # 주말에는 정기 분석을 보내지 않지만, 일요일은 당첨 결과 확인
    if weekday==6:
        if not state.get("pending_results"):
            log("일요일: 확인할 추천 조합 결과 없음")
            return
        draws=sync_draws()
        state=load(STATE_FILE,{})
        check_pending_results(draws,state)
        return
    if weekday==5:
        log("토요일: 정기 알림 없음")
        return

    today=now.strftime("%Y-%m-%d")
    week=now.strftime("%G-W%V")
    friday=weekday==4
    if state.get("analysis_sent_date")==today:
        log("오늘 번호 분석은 이미 전송됨")
        return

    log("로또 분석 시작")
    draws=sync_draws()
    state=load(STATE_FILE,{})
    if state.get("analysis_sent_date")==today:
        log("오늘 번호 분석은 이미 전송됨")
        return

    bt=backtest(draws)
    analysis=analysis_report(draws,bt)
    if friday:
        picks=recommendations(draws)
        target=draws[-1]["round"]+1
        send(analysis+"\n\n"+picks_report(draws,picks))
        state["pending_results"]=state.get("pending_results",[])
        # 같은 추첨 회차에 대한 추천 조합 중복 저장 방지
        if not any(int(x.get("target_round",0))==target for x in state["pending_results"]):
            state["pending_results"].append({
                "target_round":target,
                "created_week":week,
                "picks":picks
            })
        state["analysis_sent_date"]=today
        state["friday_picks_sent_week"]=week
        save(STATE_FILE,state)
        log(f"금요일 분석 및 추천 10개 전송 완료: 대상 {target}회")
    else:
        send(analysis)
        state["analysis_sent_date"]=today
        save(STATE_FILE,state)
        log("일일 번호 분석 전송 완료")

if __name__=="__main__":
    main()
