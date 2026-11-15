"""Local application records, separate from the read-only analytics database."""
import json
import sqlite3
import uuid
import time
import copy
from .value_codec import dumps as exact_dumps,loads as exact_loads
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from pathlib import Path

KEEP_PROGRESS=object()


class ReviewStore:
    def __init__(self, filename,cipher=None,ttl_seconds=86400,store_rows=False,clock=time.time):
        self.filename=Path(filename)
        self.cipher=cipher
        if not 0<ttl_seconds<=366*86400:raise ValueError('History retention must be positive and at most 366 days')
        self.ttl,self.store_rows,self.clock=ttl_seconds,store_rows,clock
        self.filename.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS queries (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS feedback (id TEXT PRIMARY KEY, query_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, rating TEXT NOT NULL, category TEXT NOT NULL,
                    comment TEXT NOT NULL, review_status TEXT NOT NULL DEFAULT 'pending');
                CREATE TABLE IF NOT EXISTS domains (source_id TEXT NOT NULL, id TEXT NOT NULL,
                    name TEXT NOT NULL, description TEXT NOT NULL, tables_json TEXT NOT NULL,
                    keywords_json TEXT NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(source_id,id));
                CREATE TABLE IF NOT EXISTS query_lifecycle(id TEXT PRIMARY KEY,owner_id TEXT,source TEXT,
                    access_stamp TEXT,created REAL NOT NULL,expires REAL NOT NULL,state TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS query_lifecycle_expiry ON query_lifecycle(state,expires);
                CREATE TABLE IF NOT EXISTS query_progress(query_id TEXT PRIMARY KEY,phase TEXT NOT NULL,
                    deadline REAL NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS query_submissions(owner_id TEXT NOT NULL,key_hash TEXT NOT NULL,
                    request_hash TEXT NOT NULL,access_stamp TEXT NOT NULL,query_id TEXT NOT NULL,expires REAL NOT NULL,
                    PRIMARY KEY(owner_id,key_hash));
                CREATE INDEX IF NOT EXISTS query_submissions_expiry ON query_submissions(expires);
            ''')
            # Legacy payloads stay untouched and unavailable until an explicit adoption/purge.
            conn.execute("INSERT INTO query_lifecycle SELECT id,NULL,NULL,NULL,?,?, 'legacy_pending' FROM queries WHERE id NOT IN (SELECT id FROM query_lifecycle)",(self.clock(),self.clock()+self.ttl))
            if cipher and cipher.enabled:
                # Seal existing application snapshots/comments without exposing them through HTTP.
                from .pii import PREFIX
                for key,snapshot in conn.execute("SELECT id,snapshot FROM queries").fetchall():
                    if not snapshot.startswith(PREFIX):
                        try:payload=exact_loads(snapshot)
                        except ValueError:payload=snapshot # preserve/quarantine malformed legacy payloads safely
                        conn.execute("UPDATE queries SET snapshot=? WHERE id=?",(cipher.encrypt(payload,'query:'+key),key))
                for key,comment in conn.execute("SELECT id,comment FROM feedback").fetchall():
                    if not comment.startswith(PREFIX):
                        conn.execute("UPDATE feedback SET comment=? WHERE id=?",(cipher.encrypt(comment,'feedback:'+key),key))
                for key,payload in conn.execute('SELECT query_id,payload FROM query_progress').fetchall():
                    if not payload.startswith(PREFIX):
                        conn.execute('UPDATE query_progress SET payload=? WHERE query_id=?',(cipher.encrypt(exact_loads(payload),'progress:'+key),key))

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.filename,timeout=10)) as conn:
            conn.execute('PRAGMA secure_delete=ON')
            with conn:
                yield conn

    def register(self,job):
        with self.connect() as conn:
            conn.execute("INSERT INTO query_lifecycle VALUES(?,?,?,?,?,?,?)",(job['id'],job.get('owner_id'),job.get('source'),job.get('access_stamp'),self.clock(),self.clock()+self.ttl,'live'))
        return self.metadata(job['id'])

    @staticmethod
    def _submission(conn,owner,key_hash,request_hash,stamp,clock):
        row=conn.execute('SELECT request_hash,access_stamp,query_id,expires FROM query_submissions WHERE owner_id=? AND key_hash=?',(owner,key_hash)).fetchone()
        if not row:return None
        if row[0]!=request_hash or row[1]!=stamp:return {'kind':'conflict'}
        life=conn.execute('SELECT state,expires FROM query_lifecycle WHERE id=?',(row[2],)).fetchone()
        if row[3]<=clock or not life or life[0]!='live' or life[1]<=clock:return {'kind':'gone'}
        return {'kind':'replay','id':row[2]}

    def submission(self,owner,key_hash,request_hash,stamp):
        with self.connect() as conn:
            return self._submission(conn,owner,key_hash,request_hash,stamp,self.clock())

    def register_submission(self,job,identity=None):
        """Atomically bind a key to a queued snapshot; no executable approval is stored."""
        snapshot={key:value for key,value in job.items() if key not in {'touched','timer','queued_at'}}
        # The question has not passed model egress checks yet. Recovery cannot execute
        # an initial queued submission, so persist its identity without raw content.
        snapshot['question']='[Submission received; no reviewed question persisted yet]'
        snapshot.setdefault('value_encoding','exact-v2')
        payload=self.cipher.encrypt(snapshot,'query:'+job['id']) if self.cipher and self.cipher.enabled else exact_dumps(snapshot)
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if identity:
                previous=self._submission(conn,job['owner_id'],*identity,job['access_stamp'],self.clock())
                if previous:return previous
            created=self.clock();expires=created+self.ttl
            conn.execute('INSERT INTO query_lifecycle VALUES(?,?,?,?,?,?,?)',(job['id'],job['owner_id'],job['source'],job['access_stamp'],created,expires,'live'))
            conn.execute('INSERT INTO queries VALUES(?,?)',(job['id'],payload))
            if identity:
                conn.execute('INSERT INTO query_submissions VALUES(?,?,?,?,?,?)',(job['owner_id'],*identity,job['access_stamp'],job['id'],expires))
        return {'kind':'created','id':job['id']}

    def metadata(self,key):
        with self.connect() as conn:
            conn.row_factory=sqlite3.Row
            row=conn.execute("SELECT * FROM query_lifecycle WHERE id=?",(key,)).fetchone()
        return dict(row) if row else None

    def is_live(self,key):
        metadata=self.metadata(key)
        return bool(metadata and metadata['state']=='live' and metadata['expires']>self.clock())

    def save_query(self, job,progress=KEEP_PROGRESS):
        job=copy.deepcopy(job)
        job.setdefault('value_encoding','exact-v2')
        if not self.store_rows:
            for query in (job.get('result') or {}).get('queries') or []:
                if query.get('result'):
                    query['result']['rows']=[]
                    query['result']['rows_retained']=False
        snapshot=self.cipher.encrypt(job,'query:'+job["id"]) if self.cipher and self.cipher.enabled else exact_dumps(job)
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute("SELECT state,expires FROM query_lifecycle WHERE id=?",(job['id'],)).fetchone()
            if not row or row[0]!='live' or row[1]<=self.clock():return False
            conn.execute("INSERT INTO queries VALUES (?,?) ON CONFLICT(id) DO UPDATE SET snapshot=excluded.snapshot",(job["id"],snapshot))
            if progress is None:conn.execute('DELETE FROM query_progress WHERE query_id=?',(job['id'],))
            elif progress is not KEEP_PROGRESS:
                if progress.get('version')!=1 or progress.get('phase') not in {'waiting','queued','running','executing'}:
                    raise ValueError('Invalid checkpoint phase/version')
                payload=self.cipher.encrypt(progress,'progress:'+job['id']) if self.cipher and self.cipher.enabled else exact_dumps(progress)
                conn.execute('INSERT INTO query_progress VALUES(?,?,?,?) ON CONFLICT(query_id) DO UPDATE SET phase=excluded.phase,deadline=excluded.deadline,payload=excluded.payload',
                    (job['id'],progress['phase'],progress['deadline'],payload))
        return True

    def progress_metadata(self,key):
        if not self.is_live(key):return None
        with self.connect() as conn:
            row=conn.execute('SELECT phase,deadline FROM query_progress WHERE query_id=?',(key,)).fetchone()
        return {'phase':row[0],'deadline':row[1]} if row else None

    def progress(self,key):
        if not self.is_live(key):return None
        with self.connect() as conn:
            row=conn.execute('SELECT payload FROM query_progress WHERE query_id=?',(key,)).fetchone()
        if not row:return None
        from .pii import PREFIX
        if row[0].startswith(PREFIX):
            if not self.cipher:raise ValueError('Encrypted checkpoints require the configured key')
            return self.cipher.decrypt(row[0],'progress:'+key)
        return exact_loads(row[0])

    def query(self, key):
        if not self.is_live(key):return None
        with self.connect() as conn:
            row=conn.execute("SELECT snapshot FROM queries WHERE id=?",(key,)).fetchone()
        if not row:
            return None
        from .pii import PREFIX
        if row[0].startswith(PREFIX):
            if not self.cipher:
                raise ValueError("Encrypted records require the configured PII key")
            result=self.cipher.decrypt(row[0],'query:'+key)
        else:result=exact_loads(row[0])
        result.setdefault('value_encoding','legacy-v1')
        return result

    def feedback(self,key,rating,category,comment):
        identifier=uuid.uuid4().hex
        if self.cipher and self.cipher.enabled:
            comment=self.cipher.encrypt(comment,'feedback:'+identifier)
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute("SELECT state,expires FROM query_lifecycle WHERE id=?",(key,)).fetchone()
            if not row or row[0]!='live' or row[1]<=self.clock():raise ValueError('This query expired or was deleted')
            conn.execute("INSERT INTO feedback(id,query_id,created_at,rating,category,comment) VALUES (?,?,?,?,?,?)",
                (identifier,key,datetime.now(timezone.utc).isoformat(),rating,category,comment))
        return {"id":identifier,"review_status":"pending"}

    def reviews(self,status="pending"):
        with self.connect() as conn:
            conn.row_factory=sqlite3.Row
            result=[dict(row) for row in conn.execute("SELECT f.* FROM feedback f JOIN query_lifecycle q ON q.id=f.query_id WHERE f.review_status=? AND q.state='live' AND q.expires>? ORDER BY f.created_at",(status,self.clock()))]
        if self.cipher:
            for record in result:
                record["comment"]=self.cipher.decrypt(record["comment"],'feedback:'+record["id"])
        return result

    @staticmethod
    def _erase(conn,keys,state):
        conn.executemany('DELETE FROM query_progress WHERE query_id=?',[(key,) for key in keys])
        conn.executemany("DELETE FROM feedback WHERE query_id=?",[(key,) for key in keys])
        conn.executemany("DELETE FROM queries WHERE id=?",[(key,) for key in keys])
        conn.executemany("UPDATE query_lifecycle SET state=? WHERE id=?",[(state,key) for key in keys])

    def delete_query(self,key):
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._erase(conn,[key],'deleted')

    def delete_owner(self,owner):
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            keys=[row[0] for row in conn.execute("SELECT id FROM query_lifecycle WHERE owner_id=? AND state='live'",(owner,))]
            self._erase(conn,keys,'deleted')
        return keys

    def maintenance(self):
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            keys=[row[0] for row in conn.execute("SELECT id FROM query_lifecycle WHERE state='live' AND expires<=? LIMIT 200",(self.clock(),))]
            self._erase(conn,keys,'expired')
            # Unknown IDs can never be written by save_query; tombstones need not grow forever.
            conn.execute("DELETE FROM query_lifecycle WHERE state IN ('deleted','expired') AND expires<?",(self.clock()-7*86400,))
            conn.execute('DELETE FROM query_submissions WHERE expires<?',(self.clock()-7*86400,))
        return len(keys)

    def legacy_inventory(self,apply=False,purge=False):
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            keys=[row[0] for row in conn.execute("SELECT id FROM query_lifecycle WHERE state='legacy_pending'")]
            summary={'legacy_pending':len(keys),'adoptable':0,'invalid_timestamp_or_unreadable':0,'already_expired':0,'applied':apply}
            for key in keys:
                if purge:
                    if apply:self._erase(conn,[key],'deleted')
                    continue
                try:
                    raw=conn.execute('SELECT snapshot FROM queries WHERE id=?',(key,)).fetchone()[0]
                    from .pii import PREFIX
                    job=self.cipher.decrypt(raw,'query:'+key) if raw.startswith(PREFIX) and self.cipher else exact_loads(raw)
                    created=datetime.fromisoformat(job['created_at'])
                    if created.tzinfo is None:raise ValueError('Missing timezone')
                    created=created.timestamp()
                    if created>self.clock():raise ValueError('Future legacy timestamp')
                    expires=created+self.ttl
                    summary['adoptable']+=1
                    summary['already_expired']+=int(expires<=self.clock())
                    if apply:
                        conn.execute("UPDATE query_lifecycle SET owner_id=?,source=?,access_stamp=?,created=?,expires=?,state='live' WHERE id=?",(job.get('owner_id'),job.get('source'),job.get('access_stamp'),created,expires,key))
                except (ValueError,KeyError,TypeError,IndexError):summary['invalid_timestamp_or_unreadable']+=1
        return summary

    def domains(self,source_id):
        with self.connect() as conn:
            conn.row_factory=sqlite3.Row
            records=[dict(row) for row in conn.execute("SELECT * FROM domains WHERE source_id=? ORDER BY name",(source_id,))]
        for record in records:
            record["tables"]=json.loads(record.pop("tables_json"))
            record["keywords"]=json.loads(record.pop("keywords_json"))
        return records

    def save_domain(self,source_id,domain):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous=conn.execute("SELECT revision FROM domains WHERE source_id=? AND id=?",(source_id,domain["id"])).fetchone()
            revision=previous[0] if previous else 0
            if revision!=domain["revision"]:
                raise ValueError("The domain was changed elsewhere. Reload before saving.")
            conn.execute("INSERT OR REPLACE INTO domains VALUES (?,?,?,?,?,?,?,?)",
                (source_id,domain["id"],domain["name"],domain["description"],json.dumps(domain["tables"]),
                 json.dumps(domain["keywords"]),revision+1,datetime.now(timezone.utc).isoformat()))
        return {**domain,"revision":revision+1}
