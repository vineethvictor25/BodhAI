"""Isolated API load/failure exercise using synthetic data and an in-process provider."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import time
import uuid

from fastapi.testclient import TestClient
from httpx import Request
from langchain_core.messages import AIMessage
from openai import APIConnectionError

from .api import create_app
from .config import ModelConfig
from .demo import create_demo
from .model_policy import ModelPolicy
from .models import LangChainModel
from .storage import Database,QueryError
from .workflow import LangGraphWorkflow


class SyntheticChat:
    def __init__(self,scenario,delay):self.scenario,self.delay=scenario,delay
    def bind_tools(self,tools,**options):self.kind=tools[0].__name__;return self
    def invoke(self,messages):
        time.sleep(self.delay)
        if self.scenario=='provider_outage':raise APIConnectionError(request=Request('POST','http://synthetic.invalid/v1'))
        slow='SELECT SUM(a.order_id+b.order_id+c.order_id+d.order_id+e.order_id+f.order_id+g.order_id+h.order_id) FROM orders a,orders b,orders c,orders d,orders e,orders f,orders g,orders h'
        values={
            'IntentOutput':{'status':'ready','task':'Count orders','assumptions':[],'message':''},
            'SchemaSelection':{'tables':['orders'],'reason':'Synthetic test'},
            'ColumnSelection':{'tables':[{'table':'orders','columns':['order_id']}],'reason':'Synthetic test'},
            'SQLSubmission':{'sql':slow if self.scenario=='database_timeout' else 'SELECT COUNT(*) AS n FROM orders','parameters':[]},
            'ReviewOutput':{'approved':True,'feedback':'Synthetic test review'},
            'AnswerOutput':{'answer':'There are 5 orders.','reason':'The synthetic query counts the sample orders.'}}
        return AIMessage(content='',tool_calls=[{'id':'synthetic','name':self.kind,'args':values[self.kind]}],
            usage_metadata={'input_tokens':10,'output_tokens':5,'total_tokens':15})


def run_load(users=4,requests=12,workers=2,scenario='normal',delay=.005):
    if not 1<=users<=20 or not 1<=requests<=500 or not 1<=workers<=16 or scenario not in {'normal','provider_outage','database_timeout','cancellation'}:
        raise ValueError('Use 1–20 users, 1–500 requests, 1–16 workers and a supported scenario')
    if not 0<=delay<=1:raise ValueError('Synthetic delay must be between 0 and 1 second')
    started=time.monotonic();results=[];read_count=[0];timeouts=[0];read_lock=threading.Lock()
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);create_demo(root/'sample.db',expanded=False)
        class CountedDatabase(Database):
            def execute(self,*args,**kwargs):
                with read_lock:read_count[0]+=1
                try:return super().execute(*args,**kwargs)
                except QueryError as error:
                    if 'interrupted' in str(error):
                        with read_lock:timeouts[0]+=1
                    raise
        db=CountedDatabase(root/'sample.db',timeout=.01 if scenario=='database_timeout' else 3)
        policy=ModelPolicy({'version':1,'sources':{'sample':{'classification':'internal'}},'profiles':[{
            'id':'synthetic','endpoint':'http://127.0.0.1:1/v1','models':['synthetic'],
            'approved':True,'classifications':['internal']}]})
        def factory(query):
            return LangGraphWorkflow(db,max_attempts=1,model_factory=lambda role:LangChainModel(
                config=ModelConfig(model='synthetic',api_key='synthetic-only',base_url='http://127.0.0.1:1/v1'),
                chat=SyntheticChat(scenario,delay),egress_policy=policy))
        app=create_app(db,workflow_factory=factory,record_path=root/'records.db',security_values={
            'QUERY_WORKERS':str(workers),'QUERY_MAX_PENDING':str(min(200,max(workers,users*2))),
            'QUERY_MAX_PER_USER':'3','AUTH_COOKIE_SECURE':'false','ROW_SECURITY_ENABLED':'false','PII_ENCRYPTION_ENABLED':'false',
            'AUTH_SETUP_TOKEN':'synthetic-load-test-setup-secret'})
        clients=[]
        with TestClient(app) as administrator:
            password='Synthetic-load-test-password-7843!'
            setup=administrator.post('/api/auth/setup',json={'username':'loadadmin','password':password,
                'setup_token':'synthetic-load-test-setup-secret'})
            if setup.status_code!=200:raise RuntimeError('Synthetic administrator setup failed')
            for index in range(users):
                client=TestClient(app,client=(f'synthetic-user-{index}',50000+index))
                response=client.post('/api/auth/register',json={'username':f'loaduser{index}','password':password,'source':'sample','domain':'sales'})
                if response.status_code!=200:raise RuntimeError('Synthetic user setup failed')
                client.headers['X-CSRF-Token']=response.json()['csrf_token'];clients.append(client)
            def request(index):
                client=clients[index%users];begin=time.monotonic()
                key=uuid.uuid4().hex;body={'question':'How many orders?'}
                response=client.post('/api/queries',json=body,headers={'Idempotency-Key':key})
                if response.status_code==429:return {'outcome':'rejected','seconds':time.monotonic()-begin,'replayed':False}
                if response.status_code!=202:return {'outcome':'unexpected_http','seconds':time.monotonic()-begin,'replayed':False}
                identifier=response.json()['id']
                replay=client.post('/api/queries',json=body,headers={'Idempotency-Key':key})
                matched=replay.status_code==202 and replay.json()['id']==identifier
                if scenario=='cancellation':client.post(f'/api/queries/{identifier}/cancel',json={})
                deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    response=client.get('/api/queries/'+identifier)
                    if response.status_code!=200:break
                    job=response.json()
                    if job['status'] in {'completed','failed'}:
                        return {'outcome':'cancelled' if job.get('cancellation_requested') else job['status'],
                                'seconds':time.monotonic()-begin,'replayed':matched}
                    if job['status']=='awaiting_confirmation':
                        client.post(f'/api/queries/{identifier}/confirm',json={'version':job['confirmation']['version'],'tables':['orders']})
                    elif job['status']=='awaiting_sql_approval':
                        sql=job['sql_approval']
                        client.post(f'/api/queries/{identifier}/approve-sql',json={'version':sql['version'],'fingerprint':sql['fingerprint'],'decision':'approve'})
                    time.sleep(.005)
                return {'outcome':'test_timeout','seconds':time.monotonic()-begin,'replayed':matched}
            try:
                load_started=time.monotonic()
                with ThreadPoolExecutor(max_workers=users) as pool:results=list(pool.map(request,range(requests)))
                load_elapsed=time.monotonic()-load_started
            finally:
                app.state.jobs.pool.shutdown(wait=True)
                for client in clients:client.close()
            metrics=app.state.jobs.operational_snapshot()
    outcomes={name:sum(item['outcome']==name for item in results) for name in sorted({item['outcome'] for item in results})}
    accepted=[item for item in results if item['outcome']!='rejected']
    latencies=sorted(item['seconds'] for item in accepted)
    expected={'normal':'completed','provider_outage':'failed','database_timeout':'failed','cancellation':'cancelled'}[scenario]
    checks={'accepted_work_exists':bool(accepted),'all_expected_outcomes':bool(accepted) and all(item['outcome']==expected for item in accepted),
            'all_replays_same_job':bool(accepted) and all(item['replayed'] for item in accepted),
            'queue_drained':metrics['queue']['active_plus_queued']==0,
            'single_read_per_normal_job':read_count[0]==len(accepted) if scenario=='normal' else True,
            'no_reads_on_outage_or_cancel':read_count[0]==0 if scenario in {'provider_outage','cancellation'} else True,
            'provider_outage_observed':any(row['category']=='provider' and row['outcome']=='connection' for row in metrics['durations']) if scenario=='provider_outage' else True,
            'database_timeout_observed':timeouts[0]==len(accepted) and bool(accepted) if scenario=='database_timeout' else True}
    return {'mode':'isolated_in_process_api_with_synthetic_provider','scenario':scenario,'users':users,'requests':requests,'workers':workers,
            'elapsed_seconds':time.monotonic()-started,'load_elapsed_seconds':load_elapsed,
            'accepted_jobs_per_second':len(accepted)/load_elapsed,'admission_rejection_fraction':outcomes.get('rejected',0)/requests,
            'outcomes':outcomes,'warehouse_read_attempts':read_count[0],
            'accepted_p95_seconds':latencies[max(0,(95*len(latencies)+99)//100-1)] if latencies else None,
            'checks':checks,'passed':all(checks.values()),'operations':metrics}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--users',type=int,default=4);parser.add_argument('--requests',type=int,default=12)
    parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--scenario',choices=['normal','provider_outage','database_timeout','cancellation'],default='normal')
    parser.add_argument('--report',default='outputs/load_test/report.json')
    args=parser.parse_args();report=run_load(args.users,args.requests,args.workers,args.scenario)
    path=Path(args.report);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in report.items() if key!='operations'},indent=2))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
