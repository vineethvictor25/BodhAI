from copy import deepcopy
from decimal import Decimal
import json
import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from langgraph_impl.api import create_app
from langgraph_impl.demo import create_demo
from langgraph_impl.storage import Database
from langgraph_impl.model_policy import EgressDenied
from langgraph_impl.visualization import profile_result,validate_plan,render_figure,VisualizationService
from langgraph_impl.tests.auth_helpers import sign_in_admin,PASSWORD


def proposal(kind='bar',x=0,y=1):
    return {'status':'chart','kind':kind,'library':'plotly','title':'Regional totals','reason':'Compare the available groups.',
        'x_column':x,'y_column':y,'suggestions':[]}


DATA={'columns':['region','total'],'rows':[['North',10],['South',20]],'returned_rows':2,'truncated':False}


class VisualizationTests(unittest.TestCase):
    def plan(self,data=DATA,raw=None):
        result,profile=profile_result(data)
        return render_figure(validate_plan(raw or proposal(),result,profile),result)

    def test_python_builds_only_allowed_traces_and_no_external_resources(self):
        result=self.plan()
        self.assertEqual(result['status'],'chart');self.assertEqual(result['figure']['data'][0]['type'],'bar')
        self.assertEqual(result['figure']['data'][0]['y'],[10,20])
        self.assertNotIn('http',json.dumps(result['figure']))

    def test_line_sorts_dates_and_preserves_null_metric_gaps(self):
        data={'columns':['month','total'],'rows':[['2026-03',30],['2026-01',10],['2026-02',None]],'truncated':False}
        result=self.plan(data,proposal('line'))
        self.assertEqual(result['row_indices'],[1,2,0])
        self.assertEqual(result['figure']['data'][0]['y'],[10,None,30])
        self.assertFalse(result['figure']['data'][0]['connectgaps'])

    def test_exact_large_value_is_a_kpi_but_not_rounded_into_a_chart(self):
        value=Decimal('12345678901234567890.123456789')
        data={'columns':['total'],'rows':[[value]],'truncated':False}
        kpi=self.plan(data,proposal('kpi',None,0))
        self.assertEqual(kpi['kpi']['value'],'12345678901234567890.123456789')
        data={'columns':['region','total'],'rows':[['N',value],['S',value]],'truncated':False}
        self.assertEqual(self.plan(data)['status'],'needs_data')

    def test_identifiers_and_numeric_strings_are_not_invented_metrics(self):
        for name,values in (('customer_id',[1,2]),('total',['10','20']),('total',[True,False])):
            data={'columns':['group',name],'rows':[['N',values[0]],['S',values[1]]],'truncated':False}
            self.assertEqual(self.plan(data)['status'],'needs_data')

    def test_duplicates_empty_rows_overcrowding_and_invalid_columns_are_rejected(self):
        cases=[{'columns':['region','total'],'rows':[['N',1],['N',2]]},
               {'columns':['region','total'],'rows':[]},
               {'columns':['region','total'],'rows':[[str(i),i] for i in range(31)]}]
        for data in cases:self.assertEqual(self.plan(data)['status'],'needs_data')
        self.assertEqual(self.plan(DATA,proposal(y=99))['status'],'needs_data')
        self.assertEqual(self.plan(DATA,proposal('line'))['status'],'needs_data')

    def test_scatter_uses_two_metrics_and_truncation_is_disclosed(self):
        data={'columns':['cost','revenue'],'rows':[[1,3],[2,5],[None,9]],'truncated':True}
        result=self.plan(data,proposal('scatter'))
        self.assertEqual(result['figure']['data'][0]['mode'],'markers')
        self.assertEqual(result['row_indices'],[0,1]);self.assertEqual(len(result['warnings']),3)

    def test_cell_labels_and_axis_text_are_escaped(self):
        data={'columns':['<b>region</b>','total'],'rows':[['<script>alert(1)</script>',1],['South',2]]}
        result=self.plan(data)
        serialized=json.dumps(result['figure'])
        self.assertNotIn('<script>',serialized);self.assertIn('&lt;script&gt;',serialized)

    def test_agent_receives_profiles_only_and_repeated_requests_are_cached(self):
        contexts=[];closed=[]
        class Model:
            def generate(self,instructions,context,output_type):contexts.append(context);return proposal()
            def close(self):closed.append(True)
        data=deepcopy(DATA);data['rows'][0][0]='Private customer label'
        service=VisualizationService()
        first=service.plan('job',data,Model,lambda:None,9999999999)
        second=service.plan('job',data,Model,lambda:None,9999999999)
        self.assertEqual(first,second);self.assertEqual(len(contexts),1);self.assertEqual(len(closed),1)
        self.assertNotIn('Private customer label',json.dumps(contexts))
        self.assertNotIn('rows',contexts[0]['profile'])

    def test_unsupported_data_gets_model_suggestions_and_missing_history_never_calls_model(self):
        class Model:
            def generate(self,*args):return {'status':'needs_data','kind':'none','library':'none','title':'Add a metric',
                'reason':'Text alone is not a chart.','x_column':None,'y_column':None,
                'suggestions':['Add total sales grouped by month.']}
        service=VisualizationService()
        result=service.plan('text',{'columns':['note'],'rows':[['Hello']]},Model,lambda:None,9999999999)
        self.assertEqual(result['status'],'needs_data');self.assertTrue(result['suggestions'])
        def forbidden():self.fail('Omitted rows must not cause a model request')
        result=service.plan('history',{'rows_retained':False},forbidden,lambda:None,9999999999)
        self.assertEqual(result['status'],'needs_data')

    def test_failed_handoffs_release_capacity_and_cannot_exceed_the_call_budget(self):
        counts=[]
        class Bad:
            def generate(self,*args):counts.append(1);return {'invalid':True}
        class Good:
            def generate(self,*args):return proposal()
        service=VisualizationService(max_calls=1)
        for _ in range(3):
            with self.assertRaises(RuntimeError):service.plan('job',DATA,Bad,lambda:None,9999999999)
        self.assertEqual(len(counts),3)
        self.assertEqual(service.plan('job',DATA,Good,lambda:None,9999999999)['status'],'chart')

    def test_deletion_and_expiry_remove_cached_figures(self):
        class Model:
            def generate(self,*args):return proposal()
        service=VisualizationService()
        service.plan(('job','stamp'),DATA,Model,lambda:None,9999999999)
        service.erase(['job']);self.assertFalse(service.cache)
        service.plan(('job','stamp'),DATA,Model,lambda:None,9999999999)
        with patch('langgraph_impl.visualization.time.time',return_value=10000000000):service.maintenance()
        self.assertFalse(service.cache)

    def test_large_payloads_and_access_revocation_never_publish_a_chart(self):
        def forbidden():self.fail('Oversized results must not call a model')
        data={'columns':['label','total'],'rows':[['x'*300000,1]]}
        service=VisualizationService()
        with self.assertRaises(ValueError):service.plan('large',data,forbidden,lambda:None,9999999999)
        live=[True]
        class Model:
            def generate(self,*args):live[0]=False;return proposal()
        def guard():
            if not live[0]:raise PermissionError('Revoked')
        with self.assertRaises(PermissionError):service.plan('revoked',DATA,Model,guard,9999999999)
        self.assertFalse(service.cache)

    def test_agent_selects_static_library_and_output_is_a_bounded_png(self):
        contexts=[]
        class Model:
            def generate(self,instructions,context,output_type):
                contexts.append(context);return {**proposal(),'library':'seaborn','reason':'Use Seaborn for a static category comparison.'}
        result=VisualizationService().plan('static',DATA,Model,lambda:None,9999999999,'static')
        self.assertEqual(result['renderer'],'seaborn');self.assertFalse(result['interactive'])
        self.assertIsNone(result['figure']);self.assertEqual(result['image']['mime_type'],'image/png')
        self.assertTrue(base64.b64decode(result['image']['data']).startswith(b'\x89PNG'))
        self.assertLessEqual(len(result['image']['data']),262144)
        self.assertEqual({item['library'] for item in contexts[0]['available_renderers']},{'matplotlib','seaborn'})

    def test_matplotlib_renders_time_series_and_presentation_is_in_cache_identity(self):
        contexts=[]
        class Model:
            def generate(self,instructions,context,output_type):
                contexts.append(context)
                return {**proposal('line'),'library':'matplotlib' if context['presentation']=='static' else 'plotly'}
        data={'columns':['month','total'],'rows':[['2026-01',10],['2026-02',None],['2026-03',20]]}
        service=VisualizationService()
        interactive=service.plan('job',data,Model,lambda:None,9999999999,'interactive')
        static=service.plan('job',data,Model,lambda:None,9999999999,'static')
        self.assertEqual(interactive['renderer'],'plotly');self.assertEqual(static['renderer'],'matplotlib')
        self.assertEqual(len(contexts),2);self.assertTrue(static['image']['data'])

    def test_disallowed_library_and_incompatible_interactivity_are_rejected(self):
        class Static:
            def generate(self,*args):return {**proposal(),'library':'matplotlib'}
        with self.assertRaises(ValueError):VisualizationService().plan('job',DATA,Static,lambda:None,9999999999,'interactive')
        with self.assertRaises(ValueError):VisualizationService(allowed_renderers=['folium'])
        class Unsupported:
            def generate(self,*args):return {**proposal(),'library':'os'}
        with self.assertRaises(RuntimeError):VisualizationService().plan('job',DATA,Unsupported,lambda:None,9999999999)


class VisualizationApiTests(unittest.TestCase):
    def setUp(self):
        directory=self.enterContext(tempfile.TemporaryDirectory());self.root=Path(directory)
        create_demo(self.root/'sample.db');self.db=Database(self.root/'sample.db');self.calls=[]
        parent=self
        class Model:
            def generate(self,instructions,context,output_type):parent.calls.append(context);return proposal()
            def close(self):pass
        class Work:
            def advance(self,*args,**kwargs):return {'result':{'status':'answered','answer':'Synthetic',
                'tables':['orders'],'queries':[{'sql':'SELECT ...','parameters':{},'result':deepcopy(DATA)}]}}
        self.app=create_app(self.db,workflow_factory=lambda query:Work(),record_path=self.root/'records.db',
            security_values={'ROW_SECURITY_ENABLED':'false','PII_ENCRYPTION_ENABLED':'false'},visualization_model_factory=Model)
        self.client=self.enterContext(TestClient(self.app));sign_in_admin(self.client)
        self.key=self.client.post('/api/queries',json={'question':'Synthetic chart query'}).json()['id']
        self.app.state.jobs.pool.shutdown(wait=True)

    def test_authenticated_owner_gets_a_chart_without_any_database_execution(self):
        with patch.object(self.db,'execute',side_effect=AssertionError('Visualization must not execute SQL')):
            response=self.client.post(f'/api/queries/{self.key}/visualize',json={})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['kind'],'bar');self.assertEqual(len(self.calls),1)
        self.assertEqual(self.client.get('/api/assets/plotly.js').status_code,200)
        self.assertEqual(self.client.get(f'/api/queries/{self.key}').json()['result']['answer'],'Synthetic')

    def test_csrf_ownership_revocation_and_expiry_are_enforced(self):
        token=self.client.headers.pop('X-CSRF-Token')
        self.assertEqual(self.client.post(f'/api/queries/{self.key}/visualize',json={}).status_code,403)
        self.client.headers['X-CSRF-Token']=token
        other=self.enterContext(TestClient(self.app))
        registered=other.post('/api/auth/register',json={'username':'chartmember','password':PASSWORD,'source':'sample','domain':'sales'})
        other.headers['X-CSRF-Token']=registered.json()['csrf_token']
        self.assertEqual(other.post(f'/api/queries/{self.key}/visualize',json={}).status_code,404)
        self.app.state.security.invalidate()
        self.assertEqual(self.client.post(f'/api/queries/{self.key}/visualize',json={}).status_code,409)
        self.app.state.records.delete_query(self.key)
        self.assertEqual(self.client.post(f'/api/queries/{self.key}/visualize',json={}).status_code,404)
        self.assertFalse(self.calls)

    def test_omitted_history_rows_and_policy_failure_do_not_rerun_or_mutate_results(self):
        with patch('langgraph_impl.agents.VisualizationAgent.recommend',side_effect=EgressDenied('Blocked')):
            response=self.client.post(f'/api/queries/{self.key}/visualize',json={})
            self.assertEqual(response.status_code,403)
        self.app.state.jobs.jobs.pop(self.key)
        response=self.client.post(f'/api/queries/{self.key}/visualize',json={})
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['status'],'needs_data')
        self.assertFalse(self.calls)
