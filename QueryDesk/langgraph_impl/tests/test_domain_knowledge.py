from copy import deepcopy
from pathlib import Path
import base64
import json
import secrets
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from langgraph_impl.api import create_app
from langgraph_impl.demo import create_demo
from langgraph_impl.storage import Database
from langgraph_impl.domain_knowledge import Definition,KnowledgeStore,SaveDefinition
from langgraph_impl.persistence import ReviewStore
from langgraph_impl.pii import LocalCipher,PIIPolicy
from langgraph_impl.access import protected_database
from langgraph_impl.knowledge import KnowledgeBase
from langgraph_impl.tests.auth_helpers import sign_in_admin,PASSWORD


def metric():
    return {'kind':'metric','title':'Eligible order count','description':'Count orders excluding cancelled orders.',
        'aliases':['eligible orders'],'columns':{'orders':['order_id','status','ordered_at']},'unit':'orders',
        'grain':'one order','date_basis':'orders.ordered_at','filters':['Exclude cancelled orders'],
        'questions':['How many eligible orders were placed in October?']}


class DomainKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);create_demo(self.root/'sales.db');self.db=Database(self.root/'sales.db')
        self.app=create_app(self.db,record_path=self.root/'records.db',security_values={'PII_ENCRYPTION_ENABLED':'false','ROW_SECURITY_ENABLED':'false'})
        self.admin=self.enterContext(TestClient(self.app));self.admin_user=sign_in_admin(self.admin)
        self.owner,self.owner_user=self.member('domainowner')
        self.reviewer,self.reviewer_user=self.member('domainreviewer')
        self.reader,self.reader_user=self.member('domainreader')
        self.endpoint='/api/sources/sample/knowledge'

    def member(self,name,domain='sales'):
        client=self.enterContext(TestClient(self.app))
        response=client.post('/api/auth/register',json={'username':name,'password':PASSWORD,'source':'sample','domain':domain})
        self.assertEqual(response.status_code,200,response.text);client.headers['X-CSRF-Token']=response.json()['csrf_token']
        return client,response.json()['user']

    def assign(self,user,role):
        response=self.admin.post(self.endpoint+'/assignments',json={'domain':'sales','user_id':user['id'],'role':role})
        self.assertEqual(response.status_code,200,response.text)

    def save(self,content=None,revision=0,client=None,key='eligible_orders',domain='sales'):
        return (client or self.admin).post(self.endpoint+'/entries',json={'domain':domain,'id':key,'expected_revision':revision,'content':content or metric()})

    def decision(self,action,revision=1,client=None,key='eligible_orders',confirmed=True):
        return (client or self.admin).post(self.endpoint+f'/sales/{key}/review',json={'revision':revision,'action':action,
            'confirm_business_review':confirmed,'review_note':'Compared the definition with reporting requirements; metadata validation alone does not prove results.'})

    def publish(self,content=None,key='eligible_orders'):
        response=self.save(content,key=key);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.decision('submit',key=key).status_code,200)
        response=self.decision('publish',key=key);self.assertEqual(response.status_code,200,response.text)

    def test_authentication_csrf_and_separate_maintenance_permission(self):
        anonymous=self.enterContext(TestClient(self.app))
        self.assertEqual(anonymous.get(self.endpoint).status_code,401)
        self.assertEqual(self.save(client=self.reader).status_code,403)
        self.assign(self.owner_user,'owner')
        self.assertEqual(self.save(client=self.owner).status_code,200)
        token=self.owner.headers.pop('X-CSRF-Token')
        self.assertEqual(self.save(client=self.owner,key='other').status_code,403)
        self.owner.headers['X-CSRF-Token']=token
        self.assertEqual(self.owner.post(self.endpoint+'/assignments',json={'domain':'sales','user_id':self.reader_user['id'],'role':'owner'}).status_code,403)

    def test_assignment_never_grants_data_or_an_unassigned_domain(self):
        before=self.app.state.security.user(self.owner_user['id'])['grants']
        response=self.admin.post(self.endpoint+'/assignments',json={'domain':'inventory','user_id':self.owner_user['id'],'role':'owner'})
        self.assertEqual(response.status_code,403)
        self.assign(self.owner_user,'owner')
        self.assertEqual(self.app.state.security.user(self.owner_user['id'])['grants'],before)
        self.assertEqual(self.save(client=self.owner,domain='inventory').status_code,403)

    def test_draft_review_publish_and_generation_context(self):
        self.assign(self.owner_user,'owner');self.assign(self.reviewer_user,'reviewer')
        self.assertEqual(self.save(client=self.owner).status_code,200)
        self.assertFalse(any(m['name']=='Eligible order count' for m in self.reader.get('/api/metrics').json()['metrics']))
        self.assertEqual(self.reader.get(self.endpoint).json()['entries'],[])
        self.assertEqual(self.decision('submit',client=self.owner).status_code,200)
        self.assertEqual(self.decision('publish',client=self.owner).status_code,403)
        self.assertEqual(self.decision('publish',client=self.reviewer,confirmed=False).status_code,409)
        self.assertEqual(self.decision('publish',client=self.reviewer).status_code,200)
        self.assertTrue(any(m['name']=='Eligible order count' for m in self.reader.get('/api/metrics').json()['metrics']))
        entries=self.reader.get(self.endpoint).json()['entries'];self.assertEqual(len(entries),1)
        self.assertEqual(entries[0]['published_revision'],1)

    def test_reviewer_cannot_publish_their_own_previous_draft(self):
        self.assign(self.owner_user,'owner');self.assertEqual(self.save(client=self.owner).status_code,200)
        self.assertEqual(self.decision('submit',client=self.owner).status_code,200)
        self.assign(self.owner_user,'reviewer')
        self.assertEqual(self.decision('publish',client=self.owner).status_code,403)

    def test_publication_invalidates_old_access_stamp_and_old_review(self):
        self.assertEqual(self.save().status_code,200);self.assertEqual(self.decision('submit').status_code,200)
        stamp=self.app.state.security.stamp(self.admin_user)
        self.assertEqual(self.decision('publish').status_code,200)
        with self.assertRaises(PermissionError):self.app.state.security.check(self.admin_user['id'],stamp)

    def test_revisions_are_immutable_and_concurrent_updates_are_rejected(self):
        self.publish()
        revised={**metric(),'description':'Revised business definition.'}
        self.assertEqual(self.save(revised,revision=1).status_code,200)
        self.assertEqual(self.save(revised,revision=1).status_code,409)
        entries=self.admin.get(self.endpoint).json()['entries']
        self.assertEqual(next(v for v in entries if v['revision']==1)['content']['description'],metric()['description'])
        self.assertEqual(next(v for v in entries if v['revision']==2)['published_revision'],1)

    def test_retire_rollback_and_rejection(self):
        self.publish();self.assertEqual(self.save({**metric(),'description':'New definition.'},revision=1).status_code,200)
        self.assertEqual(self.decision('submit',revision=2).status_code,200)
        self.assertEqual(self.decision('reject',revision=2).status_code,200)
        self.assertEqual(self.decision('publish',revision=2).status_code,409)
        self.assertEqual(self.decision('retire').status_code,200)
        self.assertFalse(any(m['name']=='Eligible order count' for m in self.admin.get('/api/metrics').json()['metrics']))
        self.assertEqual(self.decision('rollback').status_code,200)
        self.assertTrue(any(m['name']=='Eligible order count' for m in self.admin.get('/api/metrics').json()['metrics']))

    def test_hidden_pii_and_unknown_columns_are_not_knowledge_backdoors(self):
        self.assign(self.owner_user,'owner')
        hidden={**metric(),'columns':{'customers':['name']},'date_basis':''}
        self.assertEqual(self.save(hidden,client=self.owner).status_code,422)
        self.publish(hidden)
        self.assertFalse(any(m['name']=='Eligible order count' for m in self.owner.get('/api/metrics').json()['metrics']))
        self.assertEqual(self.owner.get(self.endpoint).json()['entries'],[])
        self.assertEqual(self.save({**metric(),'columns':{'orders':['unknown']}} ,key='bad').status_code,422)

    def test_stale_columns_are_excluded_and_do_not_reenter_prompts(self):
        self.publish()
        with patch('langgraph_impl.domain_knowledge.validate_definition',side_effect=ValueError('Schema changed')):
            self.assertFalse(any(m['name']=='Eligible order count' for m in self.reader.get('/api/metrics').json()['metrics']))

    def test_sql_examples_plan_without_reading_business_rows(self):
        example={'kind':'example','title':'Eligible order example','description':'Count records with a read-only query.',
            'columns':{'orders':['order_id']},'sql':'SELECT COUNT(*) total FROM orders','questions':['Count eligible order example']}
        with patch.object(Database,'execute',side_effect=AssertionError('No reads before user SQL approval')):
            self.publish(example,key='count_example')
            self.assertTrue(self.admin.get(self.endpoint).json()['entries'])
        unsafe={**example,'sql':'SELECT name FROM sqlite_master'}
        self.assertEqual(self.save(unsafe,key='unsafe').status_code,422)
        write={**example,'sql':'DELETE FROM orders'}
        self.assertEqual(self.save(write,key='write').status_code,422)

    def test_relationship_join_keys_and_context_reach_schema_selection(self):
        content={'kind':'relationship','title':'Order customer link','description':'Each order belongs to one customer.',
            'relationship':{'left_table':'orders','right_table':'customers','keys':{'customer_id':'customer_id'},'cardinality':'many_to_one'},
            'questions':['Which customer placed each order?']}
        self.publish(content,key='order_customer')
        policy=PIIPolicy({'PII_ENCRYPTION_ENABLED':'false'})
        view=protected_database(self.db,self.db.allowed,{},True,policy,'sample',lambda:None)
        knowledge=self.app.state.knowledge_store.augment(KnowledgeBase(),view)
        routing=knowledge.route('Which customer placed each order?',view.allowed)
        self.assertTrue(any('declared cardinality many_to_one' in r for r in routing['business_context']['relationship_rules']))
        self.assertTrue(routing['business_context']['knowledge_versions'])

    def test_credentials_personal_values_unknown_fields_and_import_state_rejected(self):
        for body in [{**metric(),'description':'Contact alice@example.test'}, {**metric(),'description':'token sk-'+('x'*30)}, {**metric(),'approved':True}]:
            self.assertEqual(self.save(body,key='bad').status_code,422)
        response=self.admin.post(self.endpoint+'/preview',json={'domain':'sales','id':'preview','content':metric()})
        self.assertEqual(response.status_code,200);self.assertFalse(response.json()['saved'])
        self.assertEqual(self.admin.get(self.endpoint).json()['entries'],[])

    def test_ai_suggestions_are_permitted_metadata_only_and_never_saved(self):
        self.assign(self.owner_user,'owner')
        with patch('langgraph_impl.api.LangChainModel') as model:
            model.return_value.generate.return_value={'entries':[metric()]}
            response=self.owner.post(self.endpoint+'/suggest',json={'domain':'sales','goal':'Help define eligible orders'})
            self.assertEqual(response.status_code,200,response.text);self.assertFalse(response.json()['saved'])
            payload=model.return_value.generate.call_args.args[1]
            self.assertEqual(set(payload),{'schema','goal'})
            self.assertNotIn('rows',json.dumps(payload));model.return_value.close.assert_called_once()
        self.assertEqual(self.admin.get(self.endpoint).json()['entries'],[])

    def test_assignment_revocation_blocks_draft_changes(self):
        self.assign(self.owner_user,'owner');self.assign(self.owner_user,'remove')
        self.assertEqual(self.save(client=self.owner).status_code,403)

    def test_missing_review_questions_and_unsubmitted_versions_cannot_publish(self):
        self.assertEqual(self.save({**metric(),'questions':[]}).status_code,200)
        self.assertEqual(self.decision('publish').status_code,409)
        self.assertEqual(self.decision('submit').status_code,200)
        self.assertEqual(self.decision('publish').status_code,409)

    def test_knowledge_payloads_use_existing_configured_encryption(self):
        key=self.root/'key';key.write_bytes(base64.urlsafe_b64encode(secrets.token_bytes(32)))
        cipher=LocalCipher(key,True);records=ReviewStore(self.root/'encrypted.db',cipher=cipher)
        store=KnowledgeStore(records)
        content=metric();store.save('sample',SaveDefinition(domain='sales',id='encrypted',content=content),self.admin_user)
        with records.connect() as conn:
            payload=conn.execute('SELECT payload FROM dk_versions').fetchone()[0]
        self.assertTrue(payload.startswith('qdpii:v1:'));self.assertNotIn(content['description'],payload)
        self.assertEqual(store.versions('sample',{'sales'})[0]['content']['description'],content['description'])

    def test_enabling_encryption_seals_existing_plaintext_knowledge(self):
        path=self.root/'later_encrypted.db';plain=ReviewStore(path);store=KnowledgeStore(plain)
        store.save('sample',SaveDefinition(domain='sales',id='existing',content=metric()),self.admin_user)
        key=self.root/'later_key';key.write_bytes(base64.urlsafe_b64encode(secrets.token_bytes(32)))
        sealed=KnowledgeStore(ReviewStore(path,cipher=LocalCipher(key,True)))
        with sealed.records.connect() as conn:
            self.assertTrue(conn.execute('SELECT payload FROM dk_versions').fetchone()[0].startswith('qdpii:v1:'))
            self.assertTrue(conn.execute('SELECT note FROM dk_events').fetchone()[0].startswith('qdpii:v1:'))
        self.assertEqual(sealed.versions('sample',{'sales'})[0]['content']['title'],metric()['title'])

    def test_access_revocation_inside_write_transaction_rolls_back(self):
        def revoked():raise PermissionError('Access changed')
        with self.assertRaises(PermissionError):
            self.app.state.knowledge_store.save('sample',SaveDefinition(domain='sales',id='revoked',content=metric()),self.admin_user,revoked)
        self.assertEqual(self.admin.get(self.endpoint).json()['entries'],[])

    def test_conflicting_published_aliases_require_clarification(self):
        self.publish()
        self.publish({**metric(),'title':'Confirmed order count','description':'Different business meaning.'},key='confirmed_orders')
        policy=PIIPolicy({'PII_ENCRYPTION_ENABLED':'false'})
        view=protected_database(self.db,self.db.allowed,{},True,policy,'sample',lambda:None)
        knowledge=self.app.state.knowledge_store.augment(KnowledgeBase(),view)
        self.assertIn('Which business definition',knowledge.clarification('Show eligible orders'))
        self.assertIsNone(knowledge.clarification('Show Eligible order count'))

    def test_readers_cannot_fetch_drafts_through_history_or_domain_parameters(self):
        self.publish()
        self.assertEqual(self.save({**metric(),'description':'Unpublished proposed change'},revision=1).status_code,200)
        reader=self.reader.get(self.endpoint+'/sales/eligible_orders/history').json()['entries']
        self.assertEqual([v['revision'] for v in reader],[1])
        self.assertEqual(len(self.admin.get(self.endpoint+'/sales/eligible_orders/history').json()['entries']),2)
        self.assertEqual(self.reader.get(self.endpoint+'?domain=inventory').status_code,403)
        self.assertEqual(self.reader.get(self.endpoint+'/inventory/eligible_orders/history').status_code,403)

    def test_published_metric_drives_real_protected_graph_with_fresh_approvals(self):
        from langgraph_impl.workflow import LangGraphWorkflow
        from langgraph_impl.tests.test_workflow import FixtureModel
        self.publish()
        policy=PIIPolicy({'PII_ENCRYPTION_ENABLED':'false'})
        view=protected_database(self.db,self.db.allowed,{},True,policy,'sample',lambda:None)
        knowledge=self.app.state.knowledge_store.augment(KnowledgeBase(),view)
        scripts={'intent':[{'status':'ready','task':'Count eligible orders','assumptions':[],'message':''}],
            'schema':[{'tables':['orders'],'reason':'Use the published order-count definition'}],
            'columns':[],'sql':[{'sql':"SELECT COUNT(*) eligible_orders FROM orders WHERE status <> :excluded",'parameters':[{'name':'excluded','value':'cancelled'}]}],
            'validation':[{'approved':True,'feedback':'Applies the published exclusion'}],
            'response':[{'answer':'The eligible order count is shown.','reason':'Excluded cancelled orders.'}]}
        models={role:FixtureModel(calls) for role,calls in scripts.items()}
        approvals=[]
        def approve(proposal):approvals.append(proposal);return True
        result=LangGraphWorkflow(view,knowledge=knowledge,model_factory=lambda role:models[role]).ask('Show eligible orders',
            confirm_tables=lambda proposal:['orders'],confirm_sql=approve)
        self.assertEqual(result['status'],'answered');self.assertEqual(len(approvals),1)
        with self.db.connection() as conn:expected=conn.execute("SELECT COUNT(*) FROM orders WHERE status<>'cancelled'").fetchone()[0]
        self.assertEqual(result['queries'][0]['result']['rows'],[[expected]])
        self.assertIn('Eligible order count',json.dumps(models['intent'].contexts[0]))
        self.assertIn('Eligible order count',json.dumps(models['schema'].contexts[0]))
        self.assertIn('Eligible order count',json.dumps(models['sql'].contexts[0]))
        self.assertIn('Eligible order count',json.dumps(models['response'].contexts[0]))


if __name__=='__main__':unittest.main()
