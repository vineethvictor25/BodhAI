"""Local authentication, session revocation and inherited access policies."""
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import closing,contextmanager


class AuthError(ValueError):
    def __init__(self,message,status=403):
        super().__init__(message)
        self.status=status


HASH_SLOTS=threading.BoundedSemaphore(2)


def password_hash(password,salt=None):
    if not 15<=len(password)<=128:
        raise AuthError("Use a password of 15–128 characters.",422)
    salt=salt or secrets.token_bytes(16)
    if not HASH_SLOTS.acquire(blocking=False):
        raise AuthError("Sign-in is busy. Please try again shortly.",429)
    try:
        digest=hashlib.scrypt(password.encode(),salt=salt,n=2**15,r=8,p=3,dklen=64,maxmem=64*1024*1024)
        return "scrypt-v1:"+salt.hex()+":"+digest.hex()
    finally:
        HASH_SLOTS.release()


class SecurityStore:
    def __init__(self,filename):
        self.filename=filename
        with self.connect() as conn:
            conn.executescript('''
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','member')),
                pii INTEGER NOT NULL DEFAULT 0,enabled INTEGER NOT NULL DEFAULT 1,revision INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS user_grants(user_id TEXT NOT NULL,source TEXT NOT NULL,domain TEXT NOT NULL,
                PRIMARY KEY(user_id,source,domain));
            CREATE TABLE IF NOT EXISTS security_groups(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,
                pii TEXT NOT NULL CHECK(pii IN ('inherit','allow','deny')),grants_json TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS memberships(user_id TEXT NOT NULL,group_id TEXT NOT NULL,PRIMARY KEY(user_id,group_id));
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,csrf TEXT NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS auth_attempts(bucket TEXT NOT NULL,at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS auth_attempts_bucket ON auth_attempts(bucket,at);
            CREATE TABLE IF NOT EXISTS security_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS security_audit(id TEXT PRIMARY KEY,at REAL NOT NULL,actor TEXT,event TEXT NOT NULL,target TEXT);
            INSERT OR IGNORE INTO security_meta VALUES('epoch','1');
            INSERT OR IGNORE INTO security_groups VALUES('business_users','Business Users','inherit','[]',1);
            INSERT OR IGNORE INTO security_groups VALUES('pii_readers','PII Readers','allow','[]',1);
            INSERT OR IGNORE INTO security_groups VALUES('pii_restricted','PII Restricted','deny','[]',1);
            ''')

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.filename,timeout=10)) as conn:
            conn.row_factory=sqlite3.Row
            with conn:
                yield conn

    @staticmethod
    def audit(conn,actor,event,target=None):
        conn.execute("INSERT INTO security_audit VALUES(?,?,?,?,?)",(uuid.uuid4().hex,time.time(),actor,event,target))

    def setup_required(self):
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]==0

    def throttle(self,bucket,limit=8):
        bucket=hashlib.sha256(bucket.encode()).hexdigest()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM auth_attempts WHERE at<?",(time.time()-900,))
            if conn.execute("SELECT COUNT(*) FROM auth_attempts WHERE bucket=?",(bucket,)).fetchone()[0]>=limit:
                raise AuthError("Too many authentication attempts. Try again in 15 minutes.",429)
            conn.execute("INSERT INTO auth_attempts VALUES(?,?)",(bucket,time.time()))

    def create_user(self,username,password,*,setup=False,grant=None):
        digest=password_hash(password)
        identifier=uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            empty=conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]==0
            if setup!=empty:
                raise AuthError("Administrator setup is already complete." if setup else "Create the administrator first.",409)
            try:
                conn.execute("INSERT INTO users(id,username,password_hash,role,pii) VALUES(?,?,?,?,?)",
                    (identifier,username.casefold(),digest,"admin" if setup else "member",int(setup)))
            except sqlite3.IntegrityError:
                raise AuthError("This account name is unavailable.",409) from None
            if not setup:
                if not grant:
                    raise AuthError("Choose one onboarding domain.",422)
                conn.execute("INSERT INTO user_grants VALUES(?,?,?)",(identifier,grant["source"],grant["domain"]))
                conn.execute("INSERT INTO memberships VALUES(?,?)",(identifier,"business_users"))
            self.audit(conn,identifier,"administrator_setup" if setup else "self_onboarding",identifier)
        return self.user(identifier)

    def login(self,username,password):
        with self.connect() as conn:
            row=conn.execute("SELECT * FROM users WHERE username=?",(username.casefold(),)).fetchone()
        # Do comparable work for unknown accounts too; never reveal account existence.
        stored=row["password_hash"] if row else "scrypt-v1:"+"00"*16+":"+"00"*64
        candidate=password_hash(password,bytes.fromhex(stored.split(":")[-2]))
        if not row or not row["enabled"] or not hmac.compare_digest(candidate,stored):
            raise AuthError("Invalid account name or password.",401)
        return self.user(row["id"])

    def session(self,user_id):
        token,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE expires<?",(time.time(),))
            conn.execute("INSERT INTO sessions VALUES(?,?,?,?)",(hashlib.sha256(token.encode()).hexdigest(),user_id,csrf,time.time()+8*3600))
            self.audit(conn,user_id,"sign_in",user_id)
        return token,csrf

    def principal(self,token):
        if not token or len(token)>200:
            raise AuthError("Sign in to continue.",401)
        with self.connect() as conn:
            row=conn.execute("SELECT * FROM sessions WHERE token_hash=? AND expires>?",(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
        if not row:
            raise AuthError("Your session expired. Sign in again.",401)
        user=self.user(row["user_id"])
        if not user or not user["enabled"]:
            raise AuthError("This account is disabled.",401)
        return user,row["csrf"]

    def logout(self,token):
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?",(hashlib.sha256(token.encode()).hexdigest(),))

    def user(self,identifier):
        with self.connect() as conn:
            row=conn.execute("SELECT id,username,role,pii,enabled,revision FROM users WHERE id=?",(identifier,)).fetchone()
            if not row:
                return None
            result=dict(row)
            direct=[dict(r) for r in conn.execute("SELECT source,domain FROM user_grants WHERE user_id=?",(identifier,))]
            groups=[dict(r) for r in conn.execute("SELECT g.* FROM security_groups g JOIN memberships m ON m.group_id=g.id WHERE m.user_id=?",(identifier,))]
        inherited=[grant for group in groups for grant in json.loads(group["grants_json"])]
        result.update(direct_grants=direct,groups=[g["id"] for g in groups],
            grants=[{"source":s,"domain":d} for s,d in sorted({(g["source"],g["domain"]) for g in direct+inherited})],
            can_view_pii=result["role"]=="admin" or (not any(g["pii"]=="deny" for g in groups) and (bool(result["pii"]) or any(g["pii"]=="allow" for g in groups))))
        return result

    def users(self):
        with self.connect() as conn:
            identifiers=[row[0] for row in conn.execute("SELECT id FROM users ORDER BY username")]
        return [self.user(identifier) for identifier in identifiers]

    def groups(self):
        with self.connect() as conn:
            groups=[dict(row) for row in conn.execute("SELECT * FROM security_groups ORDER BY name")]
        for group in groups:
            group["grants"]=json.loads(group.pop("grants_json"))
        return groups

    def epoch(self):
        with self.connect() as conn:
            return conn.execute("SELECT value FROM security_meta WHERE key='epoch'").fetchone()[0]

    def stamp(self,user):
        return str(user["revision"])+":"+self.epoch()

    def check(self,identifier,stamp):
        user=self.user(identifier)
        if not user or not user["enabled"] or self.stamp(user)!=stamp:
            raise PermissionError("Access changed while this request was running. Submit it again.")
        return user

    def invalidate(self):
        with self.connect() as conn:
            conn.execute("UPDATE security_meta SET value=CAST(value AS INTEGER)+1 WHERE key='epoch'")

    def configure_signature(self,signature):
        with self.connect() as conn:
            previous=conn.execute("SELECT value FROM security_meta WHERE key='signature'").fetchone()
            if previous and previous[0]!=signature:
                conn.execute("UPDATE security_meta SET value=CAST(value AS INTEGER)+1 WHERE key='epoch'")
            conn.execute("INSERT OR REPLACE INTO security_meta VALUES('signature',?)",(signature,))

    def update_user(self,actor,identifier,grants,groups,pii,enabled,revision):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row=conn.execute("SELECT role,revision FROM users WHERE id=?",(identifier,)).fetchone()
            if not row or row[0]=="admin":
                raise AuthError("The administrator account cannot be changed through member access settings.",422)
            if row["revision"]!=revision:
                raise AuthError("Member access changed. Reload before saving.",409)
            known={row[0] for row in conn.execute("SELECT id FROM security_groups")}
            if not set(groups)<=known:
                raise AuthError("Choose existing groups.",422)
            conn.execute("UPDATE users SET pii=?,enabled=?,revision=revision+1 WHERE id=?",(int(pii),int(enabled),identifier))
            conn.execute("DELETE FROM user_grants WHERE user_id=?",(identifier,))
            conn.executemany("INSERT INTO user_grants VALUES(?,?,?)",[(identifier,s,d) for s,d in sorted({(g["source"],g["domain"]) for g in grants})])
            conn.execute("DELETE FROM memberships WHERE user_id=?",(identifier,))
            conn.executemany("INSERT INTO memberships VALUES(?,?)",[(identifier,g) for g in sorted(set(groups))])
            conn.execute("DELETE FROM sessions WHERE user_id=?",(identifier,))
            self.audit(conn,actor,"member_access_changed",identifier)
        return self.user(identifier)

    def save_group(self,actor,group):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing=conn.execute("SELECT revision FROM security_groups WHERE id=?",(group["id"],)).fetchone()
            revision=existing[0] if existing else 0
            if group["revision"]!=revision:
                raise AuthError("This group changed. Reload before saving.",409)
            try:
                conn.execute("INSERT INTO security_groups VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,pii=excluded.pii,grants_json=excluded.grants_json,revision=excluded.revision",(group["id"],group["name"],group["pii"],json.dumps(group["grants"]),revision+1))
            except sqlite3.IntegrityError:
                raise AuthError("This group name is unavailable.",409) from None
            conn.execute("UPDATE security_meta SET value=CAST(value AS INTEGER)+1 WHERE key='epoch'")
            self.audit(conn,actor,"group_policy_changed",group["id"])
