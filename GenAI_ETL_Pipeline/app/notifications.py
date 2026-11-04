"""Transactional email outbox. SMTP delivery is at-least-once, not exactly-once."""
import json
import os
import re
import smtplib
import ssl
import threading
import time
import uuid
from email.message import EmailMessage
from . import store

EVENTS={"pipeline.version_activated","pipeline.version_rolled_back","mapping.approved","remediation.applied"}
SAFE_FIELDS={"pipeline_id","revision","change_id","proposal_id","parent_run_id","restored_from_revision","published","status"}


def enabled(): return os.getenv("EMAIL_ENABLED","false").lower()=="true"


def queue_event(conn,event_id,run_id,kind,details):
    if kind not in EVENTS:
        if kind!="run.finished" or os.getenv("EMAIL_NOTIFY_PUBLICATION","false").lower()!="true" or not details.get("published",0): return
    payload={"event":kind,"at":store.now(),"run_id":run_id,"details":{k:v for k,v in details.items() if k in SAFE_FIELDS}}
    conn.execute("INSERT INTO notification_outbox(id,event_id,created_at,status,payload_json) VALUES(?,?,?,'pending',?)",(uuid.uuid4().hex,event_id,store.now(),json.dumps(payload)))


def configuration():
    host=os.getenv("SMTP_HOST","").strip()
    sender=os.getenv("EMAIL_FROM","").strip()
    recipients=[item.strip() for item in os.getenv("EMAIL_TO","").split(",") if item.strip()]
    pattern=r"^[^@\s<>,\r\n]+@[^@\s<>,\r\n]+\.[^@\s<>,\r\n]+$"
    if not host or not re.fullmatch(pattern,sender) or not recipients or len(recipients)>20 or any(not re.fullmatch(pattern,item) for item in recipients):
        raise ValueError("Set SMTP_HOST, EMAIL_FROM and up to 20 valid EMAIL_TO addresses")
    mode=os.getenv("SMTP_TLS_MODE","starttls").lower()
    if mode not in ("starttls","ssl"): raise ValueError("SMTP_TLS_MODE must be starttls or ssl")
    try: port=int(os.getenv("SMTP_PORT","465" if mode=="ssl" else "587"))
    except ValueError: raise ValueError("SMTP_PORT must be a valid port") from None
    if not 1<=port<=65535: raise ValueError("SMTP_PORT is out of range")
    user,password=os.getenv("SMTP_USERNAME",""),os.getenv("SMTP_PASSWORD","")
    if bool(user)!=bool(password): raise ValueError("Configure both SMTP_USERNAME and SMTP_PASSWORD, or neither for an approved relay")
    return {"host":host,"port":port,"mode":mode,"sender":sender,"recipients":recipients,"user":user,"password":password}


def send(payload,notification_id):
    config=configuration()
    message=EmailMessage()
    message["From"]=config["sender"]; message["To"]=", ".join(config["recipients"])
    message["Subject"]="FlowForge change notification: "+payload["event"].replace("."," ").replace("_"," ")
    message["Message-ID"]="<flowforge-"+notification_id+"@"+config["sender"].split("@",1)[1]+">"
    body=["A recorded FlowForge change was applied.","Event: "+payload["event"],"Time (UTC): "+payload["at"]]
    if payload.get("run_id"): body.append("Run: "+payload["run_id"])
    body += [key+": "+str(value) for key,value in payload["details"].items()]
    if payload["event"]=="pipeline.version_rolled_back": body.append("Rollback restored pipeline configuration only. Existing warehouse rows were not reverted.")
    body.append("Source records, credentials, prompts and full recipes are intentionally omitted.")
    message.set_content("\n".join(body))
    context=ssl.create_default_context()
    factory=smtplib.SMTP_SSL if config["mode"]=="ssl" else smtplib.SMTP
    kwargs={"host":config["host"],"port":config["port"],"timeout":10}
    if config["mode"]=="ssl": kwargs["context"]=context
    with factory(**kwargs) as smtp:
        if config["mode"]=="starttls": smtp.ehlo(); smtp.starttls(context=context); smtp.ehlo()
        if config["user"]: smtp.login(config["user"],config["password"])
        refused=smtp.send_message(message)
        if refused: raise smtplib.SMTPRecipientsRefused(refused)


def dispatch_one():
    if not enabled(): return None
    owner=uuid.uuid4().hex
    with store.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row=conn.execute("SELECT * FROM notification_outbox WHERE (status='pending' AND available_at<=?) OR (status='sending' AND lease_until<?) ORDER BY created_at LIMIT 1",(time.time(),time.time())).fetchone()
        if not row: return None
        notification=dict(row)
        conn.execute("UPDATE notification_outbox SET status='sending',attempts=attempts+1,lease_owner=?,lease_until=? WHERE id=?",(owner,time.time()+120,row["id"]))
    try:
        if notification["attempts"]>=5: raise ValueError("Delivery retry budget exhausted; inspect and retry manually")
        send(json.loads(notification["payload_json"]),notification["id"])
        with store.db() as conn:
            conn.execute("UPDATE notification_outbox SET status='sent',sent_at=?,last_error=NULL,lease_owner=NULL,lease_until=0 WHERE id=? AND lease_owner=?",(store.now(),notification["id"],owner))
    except Exception as error:
        permanent=isinstance(error,(ValueError,smtplib.SMTPAuthenticationError,smtplib.SMTPRecipientsRefused))
        status="blocked" if permanent else "failed" if notification["attempts"]+1>=5 else "pending"
        # SMTP exceptions may contain recipient addresses, server text or auth detail.
        detail=str(error) if isinstance(error,ValueError) else type(error).__name__+"; inspect SMTP settings/delivery and retry"
        with store.db() as conn:
            conn.execute("UPDATE notification_outbox SET status=?,last_error=?,available_at=?,lease_owner=NULL,lease_until=0 WHERE id=? AND lease_owner=?",(status,detail,time.time()+min(900,30*2**notification["attempts"]),notification["id"],owner))
    return notification["id"]


def retry(notification_id):
    with store.db() as conn:
        row=conn.execute("SELECT status FROM notification_outbox WHERE id=?",(notification_id,)).fetchone()
        if row is None: raise ValueError("Notification not found")
        if row[0] not in ("failed","blocked"): raise ValueError("Only failed or blocked delivery can be retried manually")
        conn.execute("UPDATE notification_outbox SET status='pending',attempts=0,available_at=0,last_error=NULL WHERE id=?",(notification_id,))
        store.audit(conn,None,"notification.retry_requested",{"notification_id":notification_id})
    return {"id":notification_id,"status":"pending"}


def status():
    try: config=configuration(); ready=True; count=len(config["recipients"])
    except ValueError: ready=False; count=0
    return {"enabled":enabled(),"configured":ready,"recipient_count":count,"notify_publication":os.getenv("EMAIL_NOTIFY_PUBLICATION","false").lower()=="true","delivery":"at-least-once SMTP with a durable outbox; duplicates are possible after an uncertain delivery"}


class NotificationWorker:
    def __init__(self): self.stop=threading.Event(); self.thread=None
    def start(self):
        if not enabled(): return
        def loop():
            while not self.stop.is_set():
                try: found=dispatch_one()
                except Exception: found=None
                if not found: self.stop.wait(2)
        self.thread=threading.Thread(target=loop,daemon=True); self.thread.start()
    def close(self):
        self.stop.set()
        if self.thread: self.thread.join(timeout=5)
