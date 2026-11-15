from langgraph_impl.tests.auth_helpers import sign_in_admin
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from langgraph_impl.api import create_app
from langgraph_impl.demo import create_demo
from langgraph_impl.storage import Database,QueryError
from langgraph_impl.sources import SourceRegistry
from langgraph_impl.knowledge import KnowledgeBase


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        create_demo(self.path/"sales.db")
        self.db=Database(self.path/"sales.db")
        self.record_path=self.path/"records.sqlite3"
        self.domain={"id":"customer_sales","name":"Customer sales","description":"Customer purchases and order volume",
            "keywords":["buying","purchases"],"tables":["customers","orders"]}

    def app(self):
        return create_app(self.db,workflow_factory=lambda query:None,record_path=self.record_path)

    def test_manual_domain_validation_revision_and_restart_persistence(self):
        with TestClient(self.app()) as client:
            sign_in_admin(client)
            endpoint="/api/sources/sample/domains"
            self.assertEqual(client.post(endpoint,json={**self.domain,"tables":["private"]}).status_code,422)
            saved=client.post(endpoint,json=self.domain)
            self.assertEqual(saved.status_code,200)
            self.assertEqual(saved.json()["revision"],1)
            self.assertEqual(client.post(endpoint,json=self.domain).status_code,409)
            updated=client.post(endpoint,json={**self.domain,"revision":1,"description":"Updated description"})
            self.assertEqual(updated.json()["revision"],2)
        with TestClient(self.app()) as client:
            sign_in_admin(client)
            directory=client.get("/api/sources/sample/domains").json()
            found=next(domain for domain in directory["domains"] if domain["id"]==self.domain["id"])
            self.assertEqual(found["description"],"Updated description")
            self.assertEqual(directory["mode"],"relevance_hint")
            self.assertIn(self.domain["id"],[d["id"] for d in client.get("/api/config").json()["workspaces"]])

    def test_developer_config_domains_are_hints_and_cannot_grant_permissions(self):
        filename=self.path/"domains.json"
        filename.write_text(json.dumps([self.domain]))
        knowledge=SourceRegistry.load_domains(KnowledgeBase(),filename,self.db.allowed)
        routing=knowledge.route("buying",self.db.allowed,requested=["customer_sales"],soft=True)
        self.assertEqual(set(routing["tables"]),self.db.allowed)
        self.assertEqual(routing["preferred_tables"],["customers","orders"])
        self.assertIn("payments",routing["tables"])
        filename.write_text(json.dumps([{**self.domain,"tables":["private"]}]))
        with self.assertRaises(QueryError):
            SourceRegistry.load_domains(KnowledgeBase(),filename,self.db.allowed)

    def test_ai_suggestions_are_metadata_only_drafts_until_explicit_save(self):
        with TestClient(self.app()) as client,patch('langgraph_impl.api.LangChainModel') as model:
            sign_in_admin(client)
            model.return_value.generate.return_value={"domains":[self.domain]}
            endpoint="/api/sources/sample/domains"
            response=client.post(endpoint+"/suggest",json={})
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.json()["draft"])
            self.assertNotIn(self.domain["id"],[d["id"] for d in client.get(endpoint).json()["domains"]])
            context=model.return_value.generate.call_args.args[1]
            self.assertIn("schema",context)
            self.assertNotIn("rows",json.dumps(context))
            model.return_value.generate.return_value={"domains":[{**self.domain,"tables":["private"]}]}
            self.assertEqual(client.post(endpoint+"/suggest",json={}).status_code,422)
