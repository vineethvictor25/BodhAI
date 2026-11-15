"""Operator-configured data sources. Public profiles contain no connection secrets."""
import json
import math
import os
import re
import threading
from pathlib import Path

from dotenv import dotenv_values

from .config import APP_DIR
from .knowledge import KnowledgeBase
from .storage import Database, QueryError
from .contracts import DomainDefinition
from .connector_config import SOURCE_KINDS


class SourceRegistry:
    def __init__(self,default_database,profiles_file=None,environ=None):
        shell=os.environ if environ is None else environ
        self.values={**dict(dotenv_values(Path(shell.get("NLQ_ENV_FILE") or APP_DIR/".env"),interpolate=False)),**shell}
        tls_flag=str(self.values.get('WAREHOUSE_TLS12_ENABLED','true')).lower()
        if tls_flag not in {'true','false','1','0'}:raise ValueError('WAREHOUSE_TLS12_ENABLED must be true or false')
        self.tls12_enabled=tls_flag in {'true','1'}
        self.profiles={"sample":{"id":"sample","name":"Retail sample","kind":"sqlite"}}
        self.databases={"sample":default_database}
        self.knowledge={"sample":KnowledgeBase()}
        if self.values.get("DOMAINS_FILE"):
            self.knowledge["sample"]=self.load_domains(self.knowledge["sample"],self.values["DOMAINS_FILE"],default_database.allowed)
        self.lock=threading.RLock()
        filename=profiles_file or self.values.get("DATA_SOURCES_FILE")
        if filename:
            path=Path(filename)
            if not path.is_absolute():
                path=APP_DIR/path
            records=json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(records,list) or len(records)>30:
                raise ValueError("Data sources must be a list of at most 30 profiles")
            for profile in records:
                if not isinstance(profile,dict) or set(profile)-{"id","name","kind","connection_env","schema","allowed_tables","knowledge_directory","domains_file","row_limit","timeout_seconds","table_mappings","relationships"}:
                    raise ValueError("Invalid data source profile fields")
                identifier=profile.get("id","")
                if not re.fullmatch(r"[a-z][a-z0-9_-]{0,49}",identifier) or identifier in self.profiles:
                    raise ValueError("Data source IDs must be unique and cannot replace sample")
                if profile.get("kind") not in SOURCE_KINDS:
                    raise ValueError("Supported source kinds: "+', '.join(sorted(SOURCE_KINDS)))
                if not isinstance(profile.get("name"),str) or not 1<=len(profile["name"])<=100:
                    raise ValueError("Each source requires a display name")
                if not re.fullmatch(r"[A-Z][A-Z0-9_]*",profile.get("connection_env","")):
                    raise ValueError("Each source requires an environment variable name for its connection")
                tables=profile.get("allowed_tables")
                row_limit,timeout=profile.get('row_limit',100),profile.get('timeout_seconds',3)
                if type(row_limit) is not int or not 1<=row_limit<=1000 or type(timeout) not in {int,float} or not math.isfinite(timeout) or not 0<timeout<=60:
                    raise ValueError('Source limits require 1–1000 result rows and a positive timeout up to 60 seconds')
                if not isinstance(tables,list) or not tables or any(not isinstance(t,str) or not t for t in tables):
                    raise ValueError("Each source requires a non-empty allowed_tables list")
                if 'table_mappings' in profile or 'relationships' in profile:
                    if profile['kind']!='mssql' or 'table_mappings' not in profile:raise ValueError('table_mappings and relationships require a mapped SQL Server source')
                    from .sqlserver_multi import validate_mappings
                    validate_mappings(tables,profile['table_mappings'],profile.get('relationships'))
                self.profiles[identifier]=profile

    def list(self):
        return [{"id":key,"name":profile["name"],"kind":profile["kind"],
            "configured":key=="sample" or bool(self.values.get(profile["connection_env"]))} for key,profile in self.profiles.items()]

    def open(self,identifier="sample"):
        if identifier not in self.profiles:
            raise QueryError("Unknown data source")
        with self.lock:
            if identifier not in self.databases:
                profile=self.profiles[identifier]
                address=self.values.get(profile["connection_env"])
                if not address:
                    raise QueryError("This source is not configured. Ask the operator to set its connection environment variable.")
                limits={'row_limit':profile.get('row_limit',100),'timeout':profile.get('timeout_seconds',3)}
                if profile["kind"]=="sqlite":
                    filename=Path(address)
                    if not filename.is_absolute():
                        filename=APP_DIR/filename
                    db=Database(filename,allowed_tables=profile["allowed_tables"],**limits)
                elif profile['kind']=='mssql' and 'table_mappings' in profile:
                    from .sqlserver_multi import SQLServerMultiDatabase
                    db=SQLServerMultiDatabase(address,profile['allowed_tables'],profile['table_mappings'],profile.get('relationships'),schema=profile.get('schema','dbo'),tls12_enabled=self.tls12_enabled,**limits)
                elif profile['kind']=='bigquery':
                    from .bigquery_source import BigQueryDatabase
                    db=BigQueryDatabase(address,profile['allowed_tables'],profile.get('schema'),credentials_file=self.values.get('GOOGLE_APPLICATION_CREDENTIALS'),tls12_enabled=self.tls12_enabled,**limits)
                else:
                    from .warehouse import WarehouseDatabase
                    db=WarehouseDatabase(address,profile["kind"],profile["allowed_tables"],profile.get("schema"),tls12_enabled=self.tls12_enabled,**limits)
                directory=profile.get("knowledge_directory")
                try:
                    if directory:
                        path=Path(directory)
                        knowledge=KnowledgeBase(path if path.is_absolute() else APP_DIR/path)
                    else:
                        knowledge=KnowledgeBase.generic(db.allowed)
                    if profile.get("domains_file"):
                        knowledge=self.load_domains(knowledge,profile["domains_file"],db.allowed)
                except Exception:
                    if hasattr(db,"close"):
                        db.close()
                    raise
                self.databases[identifier]=db
                self.knowledge[identifier]=knowledge
            return self.databases[identifier],self.knowledge[identifier]

    @staticmethod
    def load_domains(knowledge,filename,allowed):
        path=Path(filename)
        if not path.is_absolute():
            path=APP_DIR/path
        definitions=json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(definitions,list) or len(definitions)>50:
            raise QueryError("Domains config must contain at most 50 definitions")
        domains=[{**DomainDefinition.model_validate(value).model_dump(),"revision":0} for value in definitions]
        if len({domain["id"] for domain in domains})!=len(domains) or any(not set(domain["tables"])<=set(allowed) for domain in domains):
            raise QueryError("Configured domains require unique IDs and permitted tables")
        return knowledge.with_domains(domains,allowed)

    def test(self,identifier):
        db,_=self.open(identifier)
        # Metadata discovery is read-only and also verifies table grants.
        db.schema(sorted(db.allowed))
        return {"source":identifier,"connected":True,"dialect":db.catalog()["dialect"],"table_count":len(db.allowed)}

    def close(self):
        for db in self.databases.values():
            if hasattr(db,"close"):
                db.close()
