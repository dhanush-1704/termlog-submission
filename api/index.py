import os,json,re,threading,time,collections
from datetime import datetime,date
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from urllib.request import urlopen,Request
from fastapi import FastAPI,HTTPException
from pydantic import BaseModel,Field

app=FastAPI(title="Acme Appliances Ledger QA")
IST=ZoneInfo("Asia/Kolkata")
_cache={"at":0,"orders":None,"rates":None}
_lock=threading.Lock()

class Question(BaseModel):
    question:str=Field(min_length=1,max_length=2000)

def fetch(path):
    sign=os.environ.get("LEDGER_QUIZ_SIGN")
    if not sign: raise RuntimeError("Missing LEDGER_QUIZ_SIGN environment variable")
    params=urlencode({"email":os.environ.get("LEDGER_EMAIL","23f2001912@ds.study.iitm.ac.in"),"quizSign":sign,"questionId":"q-ledger-agent-server","path":path})
    with urlopen(Request("https://exam.sanand.workers.dev/questionData?"+params,headers={"User-Agent":"AcmeLedgerQA/1.0"}),timeout=8) as r:return r.read().decode()

def ts(v):
    try:
        d=datetime.fromisoformat(str(v).replace("Z","+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=IST)
    except:return datetime.min.replace(tzinfo=IST)

def load():
    if _cache["orders"] is not None and time.time()-_cache["at"]<300:return _cache["orders"],_cache["rates"]
    with _lock:
        if _cache["orders"] is not None and time.time()-_cache["at"]<300:return _cache["orders"],_cache["rates"]
        raw=fetch("/export"); rows=[]
        for line in raw.splitlines():
            try:
                x=json.loads(line)
                if isinstance(x,dict) and "id" in x:rows.append(x)
            except:pass
        if not rows:
            x=json.loads(raw);rows=x if isinstance(x,list) else x.get("orders",[])
        rates=json.loads(fetch("/rates")).get("usd_per_unit",{})
        latest={}
        for x in rows:
            k=str(x.get("id",""))
            if k not in latest or ts(x.get("updated_at"))>=ts(latest[k].get("updated_at")):latest[k]=x
        if not latest or not rates:raise RuntimeError("Empty ledger or rates")
        _cache.update(at=time.time(),orders=list(latest.values()),rates=rates)
        return _cache["orders"],_cache["rates"]

def usd(o,r):return float(o.get("amount",0) or 0)*float(r.get(str(o.get("currency","USD")).upper(),0) or 0)
def money(x):return round(x+1e-9,2)
def period(q):
    ms={"january":1,"jan":1,"february":2,"feb":2,"march":3,"mar":3,"april":4,"apr":4,"may":5,"june":6,"jun":6,"july":7,"jul":7,"august":8,"aug":8,"september":9,"sep":9,"sept":9,"october":10,"oct":10,"november":11,"nov":11,"december":12,"dec":12}
    for n,m in ms.items():
        z=re.search(r"\\b"+n+r"\\s+(20\\d{2})",q)
        if z:
            y=int(z.group(1));return date(y,m,1),date(y+(m==12),m%12+1,1)
    z=re.search(r"\\b(20\\d{2})-(0[1-9]|1[0-2])\\b",q)
    if z:
        y,m=map(int,z.groups());return date(y,m,1),date(y+(m==12),m%12+1,1)
    return None

def filtered(q,allrows):
    rows=list(allrows)
    regions={str(x.get("region","")) for x in rows}
    products=sorted({str(x.get("product","")) for x in rows},key=len,reverse=True)
    customers=sorted({str(x.get("customer","")) for x in rows},key=len,reverse=True)
    region=next((x for x in regions if x and re.search(r"\\b"+re.escape(x.lower())+r"\\b",q)),None)
    product=next((x for x in products if x.lower() in q),None)
    customer=next((x for x in customers if re.search(r"\\b"+re.escape(x.lower())+r"\\b",q)),None)
    if region:rows=[x for x in rows if str(x.get("region","")).lower()==region.lower()]
    if product:rows=[x for x in rows if str(x.get("product","")).lower()==product.lower()]
    if customer:rows=[x for x in rows if str(x.get("customer","")).lower()==customer.lower()]
    p=period(q)
    if p:
        a,b=p
        rows=[x for x in rows if a<=ts(x.get("created_at")).astimezone(IST).date()<b]
    ds=re.findall(r"\\b20\\d{2}-\\d{2}-\\d{2}\\b",q)
    if len(ds)>=2:
        try:
            a,b=map(date.fromisoformat,ds[:2]);rows=[x for x in rows if a<=ts(x.get("created_at")).astimezone(IST).date()<=b]
        except:pass
    return rows

def compute(q):
    allrows,rates=load();rows=filtered(q,allrows)
    if not rows:return "No matching orders were found."
    refund=any(w in q for w in ("refund","refunded","refunds"))
    void=any(w in q for w in ("void","voided","cancelled","canceled"))
    count=any(w in q for w in ("how many","count","number of orders","number of"))
    qty=any(w in q for w in ("units","quantity","items sold","how many items"))
    average=any(w in q for w in ("average","avg"))
    rank=any(w in q for w in ("most","top","highest","best-selling","best selling","least","fewest","lowest"))
    if refund:
        rows=[x for x in rows if x.get("status")=="refunded"]
        if count:return str(len(rows))
        return f"Total refunds were ${money(sum(usd(x,rates) for x in rows)):.2f} USD across {len(rows)} refunded orders."
    if void:
        rows=[x for x in rows if x.get("status")=="void"]
        return str(len(rows)) if count else f"There were {len(rows)} void orders totaling ${money(sum(usd(x,rates) for x in rows)):.2f} USD."
    if rank or "by product" in q or "by customer" in q or "by region" in q:
        paid=[x for x in rows if x.get("status")=="paid"]
        field="product" if ("product" in q or "item" in q) else ("customer" if "customer" in q else "region")
        totals=collections.defaultdict(float)
        for x in paid:totals[str(x.get(field,"Unknown"))]+=usd(x,rates)
        if not totals:return "No paid orders matched those filters."
        low=any(w in q for w in ("least","fewest","lowest"))
        k=(min if low else max)(totals,key=totals.get)
        return f"{k} had the {'lowest' if low else 'highest'} paid revenue at ${money(totals[k]):.2f} USD."
    if qty:
        return str(sum(int(x.get("qty",0) or 0) for x in rows if x.get("status")=="paid"))
    if count:return str(sum(1 for x in rows if x.get("status")=="paid"))
    paid=[x for x in rows if x.get("status")=="paid"]
    total=sum(usd(x,rates) for x in paid)
    if average:return f"${money(total/len(paid)):.2f} USD average per paid order." if paid else "No paid orders matched those filters."
    if any(w in q for w in ("revenue","sales","sold","income","total amount","total value","how much","total")):
        return f"${money(total):.2f} USD"
    return f"Found {len(rows)} matching orders. Ask for revenue, refunds, order count, quantity, or a product/customer/region breakdown."

@app.get("/")
def root():return {"service":"Acme Appliances order ledger","status":"ok","endpoint":"POST /answer"}
@app.get("/health")
def health():return {"ok":True}
@app.post("/answer")
@app.post("/api/index")
@app.post("/api/index.py")
def answer(body:Question):
    try:return {"answer":compute(body.question.lower().strip())}
    except Exception as e:raise HTTPException(status_code=503,detail="Ledger data unavailable: "+type(e).__name__)
