import datetime
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from sqlalchemy.engine import make_url
    import pytds
    import snowflake.connector
    import google.auth
except ImportError:
    raise unittest.SkipTest('Install optional warehouse requirements for transport checks')
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from langgraph_impl.tls_policy import tls_context,postgres_tls,mysql_tls,secure_http_session
from langgraph_impl.connector_config import new_connection


class TLSPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
        now=datetime.datetime.now(datetime.timezone.utc)
        cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1)).not_valid_after(now+datetime.timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),False).add_extension(x509.BasicConstraints(ca=True,path_length=None),True).sign(key,hashes.SHA256())
        (self.path/'ca.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (self.path/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))

    def handshake(self,context,hostname='localhost',old_server=False):
        server=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server.load_cert_chain(self.path/'ca.pem',self.path/'key.pem')
        if old_server:
            server.minimum_version=ssl.TLSVersion.TLSv1
            server.maximum_version=ssl.TLSVersion.TLSv1_1
        ci,co,si,so=(ssl.MemoryBIO() for _ in range(4))
        client=context.wrap_bio(ci,co,server_hostname=hostname)
        peer=server.wrap_bio(si,so,server_side=True)
        for _ in range(20):
            for endpoint,outgoing,incoming in ((client,co,si),(peer,so,ci)):
                try:endpoint.do_handshake()
                except ssl.SSLWantReadError:pass
                data=outgoing.read()
                if data:incoming.write(data)
            if client.version() and peer.version():return client.version()
        raise AssertionError('Handshake did not finish')

    def test_tls_handshake_verifies_certificates_hostname_and_protocol_floor(self):
        context=tls_context(str(self.path/'ca.pem'))
        self.assertIn(self.handshake(context),{'TLSv1.2','TLSv1.3'})
        with self.assertRaises(ssl.SSLError):self.handshake(context,old_server=True)
        with self.assertRaises(ssl.SSLCertVerificationError):self.handshake(context,hostname='wrong.example.test')
        with self.assertRaises(ssl.SSLCertVerificationError):self.handshake(tls_context())

    def test_postgres_and_redshift_enforce_verified_tls_and_block_downgrades(self):
        for kind,driver in (('postgresql','postgresql+psycopg'),('redshift','redshift+psycopg2')):
            url=make_url(driver+'://reader:secret@host/reporting')
            self.assertEqual(postgres_tls(url),{'sslmode':'verify-full','ssl_min_protocol_version':'TLSv1.2'})
            for query in ('sslmode=disable','sslmode=prefer','sslmode=require','ssl_min_protocol_version=TLSv1.1'):
                with self.subTest(kind=kind,query=query),self.assertRaises(ValueError):
                    changed=make_url(str(url.set(password='secret'))+'?'+query)
                    postgres_tls(changed)

    def test_mysql_requires_tls12_certificate_and_hostname_checks(self):
        context=mysql_tls(make_url('mysql://reader:secret@host/db'))
        self.assertEqual(context.minimum_version,ssl.TLSVersion.TLSv1_2)
        self.assertTrue(context.check_hostname);self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
        for query in ('ssl_verify_cert=false','ssl_verify_identity=0'):
            with self.assertRaises(ValueError):mysql_tls(make_url('mysql://reader:secret@host/db?'+query))

    def test_oracle_rejects_plaintext_and_uses_a_verified_tls_context(self):
        url=make_url('oracle+oracledb://reader:secret@host/?service_name=reporting')
        _,_,args=new_connection(url,'oracle','REPORTING',3)
        self.assertIn('(PROTOCOL=TCPS)',args['dsn'])
        self.assertEqual(args['ssl_context'].minimum_version,ssl.TLSVersion.TLSv1_2)
        self.assertTrue(args['ssl_context'].check_hostname)
        with self.assertRaises(ValueError):new_connection(url.update_query_dict({'protocol':'tcp'}),'oracle','REPORTING',3)

    def test_snowflake_tls_settings_are_supported_by_installed_driver(self):
        from snowflake.connector.connection_config import ConnectionConfig
        url=make_url('snowflake://reader:secret@org-account/REPORTING/ANALYTICS?warehouse=QUERY_WH&role=QUERY_READER')
        _,_,args=new_connection(url,'snowflake','ANALYTICS',3)
        config=ConnectionConfig(min_tls_version=args['min_tls_version'],tls_skip_verify=args['tls_skip_verify'],protocol=args['protocol'])
        self.assertEqual(config.min_tls_version,'tls12');self.assertFalse(config.tls_skip_verify);self.assertEqual(config.protocol,'https')

    def test_sqlserver_driver_pins_tls12_and_validates_peers(self):
        import pytds.tls
        from OpenSSL import SSL
        original=SSL.Context
        with patch.object(SSL,'Context',wraps=original) as factory:
            context=pytds.tls.create_context(str(self.path/'ca.pem'))
        factory.assert_called_once_with(SSL.TLSv1_2_METHOD)
        self.assertEqual(context.get_verify_mode(),SSL.VERIFY_PEER)
        _,_,args=new_connection(make_url('mssql+pytds://reader:secret@host/db'),'mssql','dbo',3)
        self.assertTrue(args['validate_host']);self.assertFalse(args['enc_login_only'])

    def test_bigquery_api_transport_blocks_http_and_verification_bypass(self):
        from google.auth.credentials import AnonymousCredentials
        import requests
        session,refresh=secure_http_session(AnonymousCredentials())
        self.addCleanup(session.close);self.addCleanup(refresh.close)
        for transport in (session,refresh):
            adapter=transport.get_adapter('https://bigquery.googleapis.com')
            context=adapter.poolmanager.connection_pool_kw['ssl_context']
            self.assertEqual(context.minimum_version,ssl.TLSVersion.TLSv1_2)
            self.assertTrue(context.check_hostname)
            request=requests.Request('GET','https://bigquery.googleapis.com').prepare()
            with self.assertRaises(ValueError):adapter.send(request,verify=False)
        with self.assertRaises(ValueError):session.get('http://bigquery.googleapis.com')

    def test_development_flag_allows_plaintext_sql_but_cloud_apis_keep_https(self):
        pg=make_url('postgresql://reader:secret@host/db')
        self.assertEqual(postgres_tls(pg,False),{'sslmode':'disable'})
        self.assertEqual(postgres_tls(pg.update_query_dict({'sslmode':'verify-full'}),False),{'sslmode':'verify-full'})
        self.assertIsNone(mysql_tls(make_url('mysql://reader:secret@host/db'),False))
        _,_,mssql=new_connection(make_url('mssql+pytds://reader:secret@host/db'),'mssql','dbo',3,False)
        self.assertIsNone(mssql['cafile'])
        _,_,oracle=new_connection(make_url('oracle+oracledb://reader:secret@host/?service_name=REPORTING'),'oracle','REPORTING',3,False)
        self.assertIn('(PROTOCOL=TCP)',oracle['dsn']);self.assertNotIn('ssl_context',oracle)
        _,_,redshift=new_connection(make_url('redshift+psycopg2://reader:secret@host/db'),'redshift','analytics',3,False)
        self.assertEqual(redshift['sslmode'],'disable')
        _,_,snowflake_args=new_connection(make_url('snowflake://reader:secret@org-account/DB/ANALYTICS?warehouse=WH&role=QUERY_READER'),'snowflake','ANALYTICS',3,False)
        self.assertEqual(snowflake_args['protocol'],'https');self.assertFalse(snowflake_args['tls_skip_verify'])
        self.assertNotIn('min_tls_version',snowflake_args)

    def test_transport_flag_is_part_of_sql_approval_identity(self):
        from langgraph_impl.tests.test_warehouse_connectors import WarehouseConnectorTests
        from langgraph_impl.access import protected_database
        from langgraph_impl.pii import PIIPolicy
        from langgraph_impl.planning import manifest
        from langgraph_impl.contracts import Candidate
        db=WarehouseConnectorTests().adapter('mssql')
        db.schema=lambda tables:{'tables':[{'table':'orders','columns':[{'name':'id'}],'foreign_keys':[]}]}
        policy=PIIPolicy({'PII_ENCRYPTION_ENABLED':'false'})
        view=protected_database(db,{'orders'},{},False,policy,'remote',lambda:None)
        candidate=Candidate.parse({'sql':'SELECT COUNT(*) FROM orders','parameters':[]})
        db.tls12_enabled=True;strict=manifest(view,candidate,['orders'])
        db.tls12_enabled=False;development=manifest(view,candidate,['orders'])
        self.assertNotEqual(strict['fingerprint'],development['fingerprint'])
        self.assertFalse(development['adapter_capabilities']['tls']['encryption_required'])

    def test_registry_validates_and_forwards_tls_development_setting(self):
        import json,sqlite3
        from langgraph_impl.sources import SourceRegistry
        from langgraph_impl.storage import Database
        from unittest.mock import MagicMock
        file=self.path/'source.db'
        with sqlite3.connect(file) as conn:conn.execute('CREATE TABLE orders(id INTEGER)')
        conn.close()
        config=self.path/'sources.json'
        config.write_text(json.dumps([{'id':'remote','name':'Remote','kind':'mysql','connection_env':'TEST_MYSQL_URL','allowed_tables':['orders']}]),encoding='utf-8')
        for flag in ('true','false'):
            registry=SourceRegistry(Database(file),config,environ={'WAREHOUSE_TLS12_ENABLED':flag,'TEST_MYSQL_URL':'mysql://reader:secret@host/db'})
            with patch('langgraph_impl.warehouse.WarehouseDatabase') as constructor:
                constructor.return_value.allowed={'orders'};registry.open('remote')
                self.assertEqual(constructor.call_args.kwargs['tls12_enabled'],flag=='true')
        with self.assertRaises(ValueError):SourceRegistry(Database(file),config,environ={'WAREHOUSE_TLS12_ENABLED':'maybe'})
