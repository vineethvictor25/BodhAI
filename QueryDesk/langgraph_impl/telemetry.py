"""Bounded, process-local aggregate metrics. Never collect payloads or identifiers."""
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import threading
import time
from functools import wraps

ROLES = {'intent','workspace','schema','columns','sql','validation','execution','response','visualization','other'}
BOUNDS = (.01,.05,.1,.25,.5,1,2,5,10,30,60,120,300,600,1200)
active = ContextVar('query_metrics', default=None)
role = ContextVar('query_metric_role', default='other')


def failure_kind(error):
    name=type(error).__name__
    if name=='QueryCancelled':return 'cancelled'
    if name=='EgressDenied':return 'policy'
    code=getattr(error,'status_code',None)
    if code==429:return 'rate_limit'
    if code==408 or name in {'APITimeoutError','TimeoutError'}:return 'timeout'
    if code in {500,502,503,504}:return 'server'
    if type(code) is int and 400<=code<500:return 'configuration'
    if name=='APIConnectionError':return 'connection'
    return 'error'


class Metrics:
    def __init__(self):
        self.lock=threading.RLock()
        self.started=time.monotonic()
        self.started_at=datetime.now(timezone.utc).isoformat()
        self.counts=Counter()
        self.durations={}
        self.tokens={}

    def count(self,name):
        if name not in {'submitted','completed','failed','cancelled','cancel_requested','queue_rejected',
                        'user_limit_rejected','idempotency_replayed','idempotency_conflict','idempotency_gone'}:
            raise ValueError('Unknown metric counter')
        with self.lock:self.counts[name]+=1

    def duration(self,category,name,outcome,seconds):
        if category not in {'stage','provider','warehouse','queue_wait'}:raise ValueError('Unknown metric category')
        name=name if name in ROLES else 'other'
        if outcome not in {'ok','failed','cancelled','policy','rate_limit','timeout','server','configuration','connection','error'}:outcome='error'
        with self.lock:
            row=self.durations.setdefault((category,name,outcome),{'count':0,'sum_seconds':0.,'max_seconds':0.,'buckets':[0]*(len(BOUNDS)+1)})
            seconds=max(0.,float(seconds));row['count']+=1;row['sum_seconds']+=seconds
            row['max_seconds']=max(row['max_seconds'],seconds)
            bucket=next((i for i,bound in enumerate(BOUNDS) if seconds<=bound),len(BOUNDS))
            row['buckets'][bucket]+=1

    def usage(self,name,value):
        name=name if name in ROLES else 'other'
        valid=isinstance(value,dict) and all(type(value.get(key)) is int and value[key]>=0 for key in ('input_tokens','output_tokens','total_tokens'))
        with self.lock:
            row=self.tokens.setdefault(name,{'reported_calls':0,'unknown_usage_calls':0,'input_tokens':0,'output_tokens':0,'total_tokens':0})
            if not valid:row['unknown_usage_calls']+=1;return
            row['reported_calls']+=1
            for key in ('input_tokens','output_tokens','total_tokens'):row[key]+=value[key]

    def snapshot(self):
        with self.lock:
            durations=[]
            for (category,name,outcome),row in sorted(self.durations.items()):
                cumulative=0;buckets=[]
                for index,count in enumerate(row['buckets']):
                    cumulative+=count
                    buckets.append({'upper_seconds':BOUNDS[index] if index<len(BOUNDS) else None,'count':cumulative})
                p95=next(bucket['upper_seconds'] for bucket in buckets if bucket['count']>=row['count']*.95)
                durations.append({'category':category,'name':name,'outcome':outcome,
                    'count':row['count'],'mean_seconds':row['sum_seconds']/row['count'],
                    'max_seconds':row['max_seconds'],'sum_seconds':row['sum_seconds'],
                    'p95_upper_seconds':p95,'buckets':buckets})
            return {'started_at':self.started_at,'uptime_seconds':time.monotonic()-self.started,
                    'scope':'single_api_process_since_start','counters':dict(self.counts),
                    'durations':durations,'tokens':{key:dict(value) for key,value in self.tokens.items()}}


@contextmanager
def observe(metrics):
    token=active.set(metrics)
    try:yield
    finally:active.reset(token)


@contextmanager
def stage(name):
    token=role.set(name if name in ROLES else 'other')
    try:yield
    finally:role.reset(token)


@contextmanager
def measured(category):
    metrics=active.get();started=time.monotonic();outcome='ok'
    try:yield
    except BaseException as error:
        outcome=failure_kind(error)
        if metrics and category=='provider':metrics.usage(role.get(),None)
        raise
    finally:
        if metrics:metrics.duration(category,role.get(),outcome,time.monotonic()-started)


def usage(value):
    metrics=active.get()
    if metrics:metrics.usage(role.get(),value)


def instrument_stage(name):
    def decorate(handler):
        @wraps(handler)
        def call(*args,**kwargs):
            metrics=active.get();started=time.monotonic();outcome='ok'
            with stage(name):
                try:
                    result=handler(*args,**kwargs)
                    if result.get('status')=='failed' or (name=='validation' and result.get('validation_ok') is False) or (name=='execution' and result.get('rows') is None):outcome='failed'
                    return result
                except BaseException as error:
                    outcome=failure_kind(error);raise
                finally:
                    if metrics:metrics.duration('stage',name,outcome,time.monotonic()-started)
        return call
    return decorate
