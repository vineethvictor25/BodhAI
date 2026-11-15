"""Registered warehouse adapters with scoped SQL parsing and protected execution.

Use a SELECT-only database role. Parsing and read-only transactions supplement database grants;
they cannot sandbox arbitrary stored functions, privileged views, or malicious database owners.
"""
import json
import math
from contextlib import contextmanager
from datetime import date,datetime
from decimal import Decimal

from .storage import Database,QueryError,SAFE_FUNCTIONS
from .value_codec import dumps as exact_dumps,encode
from .connector_config import WAREHOUSE_DIALECTS,new_connection
from .tls_policy import postgres_tls,mysql_tls


class WarehouseDatabase:
    supports_decimal_parameters=True

    @staticmethod
    def statement(sql,parameters):
        from sqlalchemy import text,bindparam,Numeric
        return text(sql).bindparams(*(bindparam(key,type_=Numeric(asdecimal=True)) for key,value in parameters.items() if isinstance(value,Decimal)))
    def __init__(self,address,kind,allowed_tables,schema=None,row_limit=100,timeout=3,tls12_enabled=True):
        try:
            from sqlalchemy import create_engine,inspect
            from sqlalchemy.engine import make_url
            from sqlalchemy.pool import NullPool
            import sqlglot
        except ImportError:
            raise QueryError("Install optional server connectors from requirements-sources.txt first") from None
        self.kind,self.row_limit,self.timeout=kind,row_limit,timeout
        if type(tls12_enabled) is not bool:raise QueryError('Invalid TLS enforcement setting')
        self.tls12_enabled=tls12_enabled
        if kind not in WAREHOUSE_DIALECTS or not 1<=row_limit<=1000 or not 0<timeout<=60:
            raise QueryError("Unsupported dialect or query limits")
        try:
            url=make_url(address)
            if url.get_backend_name()!=kind or not url.host or (kind!='oracle' and not url.database):
                raise ValueError()
            # Only known pure-Python/packaged drivers; never execute a URL-selected plugin.
            drivers={'postgresql':'postgresql+psycopg','mysql':'mysql+pymysql','mssql':'mssql+pytds','oracle':'oracle+oracledb','snowflake':'snowflake','redshift':'redshift+psycopg2'}
            url=url.set(drivername=drivers[kind])
            permitted_options={"sslmode","sslrootcert","sslcert","sslkey","ssl_min_protocol_version"} if kind=="postgresql" else {
                "ssl_ca","ssl_cert","ssl_key","ssl_verify_cert","ssl_verify_identity"}
            if kind in {'postgresql','mysql'} and set(url.query)-permitted_options:
                raise ValueError() # reject arbitrary URL plugins/files and multi-statement driver options
            self.namespace=schema or ("public" if kind=="postgresql" else url.database)
            if kind=="mysql" and self.namespace!=url.database:
                raise ValueError()
            connect_args={'connect_timeout':5}
            if kind=='postgresql':connect_args.update(postgres_tls(url,tls12_enabled))
            if kind=='mysql':
                context=mysql_tls(url,tls12_enabled)
                if context:connect_args['ssl']=context
                else:connect_args['ssl_disabled']=True
            if kind not in {'postgresql','mysql'}:
                url,self.namespace,connect_args=new_connection(url,kind,schema,timeout,tls12_enabled)
            self.engine=create_engine(url,poolclass=NullPool,hide_parameters=True,
                connect_args=connect_args,echo=False)
            self.allowed=set(allowed_tables)
            with self.connection() as conn:
                available=self.discover_tables(conn)
            if not self.allowed or not self.allowed<=available:
                raise QueryError("Configured tables are missing or not regular tables in the permitted schema")
        except Exception:
            if hasattr(self,"engine"):
                self.engine.dispose()
            raise QueryError("Cannot open this source. Verify connector dependencies, connection settings, schema and read-only table grants.") from None

    @contextmanager
    def connection(self):
        from sqlalchemy import text
        from .cancellation import interruptible, driver_cancel
        try:
            with self.engine.connect() as conn, interruptible(driver_cancel(conn)):
                if self.kind=="postgresql":
                    conn=conn.execution_options(postgresql_readonly=True)
                    with conn.begin():
                        conn.execute(text(f"SET LOCAL statement_timeout = {int(self.timeout*1000)}"))
                        yield conn
                elif self.kind=='mysql':
                    # SET SESSION applies before the first table-reading statement starts a transaction.
                    conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
                    conn.execute(text(f"SET SESSION max_execution_time = {int(self.timeout*1000)}"))
                    try:
                        yield conn
                    finally:
                        conn.rollback()
                else:
                    raw=conn.connection.driver_connection
                    if self.kind=='redshift':
                        conn.execute(text(f'SET statement_timeout = {max(1,int(self.timeout*1000))}'))
                        conn.execute(text('SET TRANSACTION READ ONLY'))
                    if self.kind=='oracle':
                        raw.call_timeout=math.ceil(self.timeout*1000)
                        # Avoid binary-float conversion of arbitrary-precision NUMBER.
                        import oracledb
                        def numbers(cursor,metadata):
                            if metadata.type_code==oracledb.DB_TYPE_NUMBER:
                                return cursor.var(str,arraysize=cursor.arraysize,outconverter=Decimal)
                        raw.outputtypehandler=numbers
                        conn.execute(text('SET TRANSACTION READ ONLY'))
                    try:
                        yield conn
                    finally:
                        conn.rollback()
        except QueryError:
            raise
        except Exception:
            raise QueryError("The source operation failed. Check read-only grants, schema, dialect and connection health.") from None

    def catalog(self):
        return {"dialect":self.kind,"default_schema":self.namespace,"tables":sorted(self.allowed)}

    def discover_tables(self,connection):
        from sqlalchemy import inspect
        return {self.physical_name(name) for name in inspect(connection).get_table_names(schema=self.namespace)}

    def physical_name(self,name):
        # Oracle/Snowflake reflection normalizes ordinary uppercase names to lowercase.
        # Expose native names so quoted policy columns resolve to the same objects.
        if self.kind in {'oracle','snowflake'} and name is not None and not getattr(name,'quote',False) and name==name.lower():
            return name.upper()
        return name

    def schema(self,tables):
        from sqlalchemy import inspect
        if not tables or not set(tables)<=self.allowed:
            raise QueryError("Choose permitted source tables")
        metadata=[]
        with self.connection() as conn:
            inspector=inspect(conn)
            for table in sorted(set(tables)):
                primary={self.physical_name(name) for name in inspector.get_pk_constraint(table,schema=self.namespace).get("constrained_columns",[])}
                columns=[{"name":self.physical_name(c["name"]),"type":str(c["type"]),"primary_key":self.physical_name(c["name"]) in primary,
                    "nullable":bool(c["nullable"])} for c in inspector.get_columns(table,schema=self.namespace)]
                if hasattr(self,"security_projection"):
                    columns=[c for c in columns if c["name"] in self.security_projection.get(table,[])]
                links=[]
                for key in inspector.get_foreign_keys(table,schema=self.namespace):
                    if self.physical_name(key["referred_table"]) not in self.allowed or self.physical_name(key.get("referred_schema")) not in {None,self.namespace}:
                        continue
                    links.extend({"column":self.physical_name(source),"references_table":self.physical_name(key["referred_table"]),"references_column":self.physical_name(target)}
                        for source,target in zip(key["constrained_columns"],key["referred_columns"]))
                if hasattr(self,"security_projection"):
                    links=[link for link in links if link["column"] in self.security_projection.get(table,[]) and
                        link["references_column"] in self.security_projection.get(link["references_table"],[])]
                metadata.append({"table":table,"columns":columns,"foreign_keys":links})
        return {"dialect":self.kind,"default_schema":self.namespace,"tables":metadata}

    def checked_sql(self,sql,parameters,tables):
        import sqlglot
        from sqlglot import exp
        from sqlglot.optimizer.scope import traverse_scope,Scope
        Database.inputs(self,sql,parameters,tables)
        if hasattr(self,'security_row_filters') and any(key.lower().startswith('__rls_') for key in parameters):
            raise QueryError('Row-policy parameter names are reserved')
        try:
            statements=sqlglot.parse(sql,read=WAREHOUSE_DIALECTS[self.kind])
        except sqlglot.errors.ParseError:
            raise QueryError("SQL does not parse in the selected dialect") from None
        if len(statements)!=1 or not isinstance(statements[0],(exp.Select,exp.Union,exp.Intersect,exp.Except)):
            raise QueryError("Only one read-only SELECT is permitted")
        tree=statements[0]
        if self.kind=='bigquery':
            for node in list(tree.find_all(exp.Parameter)):
                if isinstance(node.this,exp.Var) and node.this.name.isidentifier():node.replace(exp.Placeholder(this=node.this.name))
        if self.kind in {'oracle','snowflake'}:
            for node in tree.find_all(exp.Identifier):
                if not node.args.get('quoted'):node.set('this',node.this.upper())
        self.check_read_tree(tree)
        referenced=set()
        for scope in list(traverse_scope(tree)):
            for _,source in scope.sources.items():
                if isinstance(source,Scope):
                    continue # CTE/subquery; underlying scopes are checked separately
                if not isinstance(source,exp.Table) or not isinstance(source.this,exp.Identifier):
                    raise QueryError("Table functions and external data sources are not permitted")
                if source.catalog not in ({'',self.project} if self.kind=='bigquery' else {''}) or source.db not in {"",self.namespace} or source.name not in tables:
                    raise QueryError("SQL reads outside the confirmed tables/schema")
                referenced.add(source.name)
                source.set("db",exp.to_identifier(self.namespace,quoted=True))
                if self.kind=='bigquery':
                    source.set('catalog',exp.to_identifier(self.project,quoted=True))
                    source.this.set('quoted',True)
                    source.meta['quoted_table']=True
                if hasattr(self,"security_projection"):
                    columns=self.security_projection.get(source.name,[])
                    if not columns:
                        raise QueryError("No accessible columns remain in this table")
                    physical=source.copy()
                    physical.set("alias",None)
                    alias=source.alias_or_name
                    row_filter=getattr(self,'security_row_filters',{}).get(source.name)
                    if row_filter:
                        physical=physical.as_('__rls_b',quoted=True)
                    projection=exp.select(*[exp.column(column,quoted=True) for column in columns]).from_(physical)
                    if row_filter:projection=projection.where(sqlglot.parse_one(row_filter['sql'],read=WAREHOUSE_DIALECTS[self.kind]))
                    source.replace(exp.Subquery(this=projection,alias=exp.TableAlias(this=exp.to_identifier(alias,quoted=True))))
            if hasattr(self,"security_projection"):
                for column in scope.columns:
                    if column.db:
                        if column.db!=self.namespace or column.catalog not in ({'',self.project} if self.kind=='bigquery' else {''}):
                            raise QueryError("Column namespace is not permitted")
                        column.set("db",None)
                        column.set('catalog',None)
        # Regenerate parsed SQL, removing input comments. Placeholders remain named binds.
        limit=tree.args.get("limit")
        if self.kind not in {'postgresql','mysql'} and limit is not None:
            expression=limit.expression or limit.args.get('count')
            options=limit.args.get('limit_options')
            if not isinstance(expression,exp.Literal) or not expression.is_int or int(expression.this)<0 or (options and (options.args.get('percent') or options.args.get('with_ties'))):
                raise QueryError('This connector requires a nonnegative literal row limit without PERCENT or WITH TIES')
            tree=tree.limit(min(int(expression.this),self.row_limit+1));limit=tree.args.get('limit')
        if limit is None:
            tree=tree.limit(self.row_limit+1)
        elif isinstance(limit.expression,exp.Literal) and limit.expression.is_int and int(limit.expression.this)>self.row_limit+1:
            limit.set("expression",exp.Literal.number(self.row_limit+1))
        from sqlglot.dialects import Dialect
        dialect=Dialect.get_or_raise(WAREHOUSE_DIALECTS[self.kind])
        generator=dialect.generator_class
        class NamedBinds(generator):
            TRANSFORMS={**generator.TRANSFORMS,exp.Placeholder:lambda self,node:":"+node.name}
        if self.kind=='bigquery':
            NamedBinds.TRANSFORMS={**NamedBinds.TRANSFORMS,exp.Placeholder:lambda self,node:'@'+node.name}
        rendered=NamedBinds(dialect=dialect,comments=False).generate(tree)
        from sqlalchemy import text
        names={node.name for node in tree.find_all(exp.Placeholder)} if self.kind=='bigquery' else set(text(rendered).compile().params)
        if names!=set(self.bound_parameters(parameters,referenced)):
            raise QueryError("Bound parameter names must exactly match SQL placeholders")
        return rendered,referenced

    @staticmethod
    def check_read_tree(tree):
        from sqlglot import exp
        banned={"Insert","Update","Delete","Merge","Create","Drop","Alter","Command","Copy","Into","Lock",
            "Transaction","Commit","Rollback","Set","Use","Grant","Revoke","Attach","Detach","Execute","Pragma",
            "SessionParameter","Parameter","Hint","Dot","QueryOption","Pivot","Unpivot","TableSample",
            "HistoricalData","MatchRecognize","Connect","OpenJSON","OpenQuery","ReadCSV","ReadParquet"}
        functions=set(SAFE_FUNCTIONS)|{"cast","try_cast","extract","date_trunc","date_part","to_char","concat",
            "concat_ws","string_agg","date_add","date_sub","datediff","timestampdiff","year","month","day",
            "current_date","current_timestamp","case","if","timestamp_trunc","time_to_str","ts_or_ds_to_date",
            "date_diff","str_to_time","timestamp_add","timestamp_sub"}
        for node in tree.walk():
            if type(node).__name__ in banned:
                raise QueryError("SQL contains an unsupported or unsafe operation")
            if isinstance(node,exp.Func) and not isinstance(node,(exp.And,exp.Or)):
                name=node.name if isinstance(node,exp.Anonymous) else node.sql_name()
                if name.lower() not in functions:
                    raise QueryError("SQL contains a function outside the supported analytics allowlist")

    def bound_parameters(self,parameters,tables):
        result=dict(parameters)
        for table in tables:
            result.update(getattr(self,'security_row_filters',{}).get(table,{}).get('parameters',{}))
        return result

    def plan(self,sql,parameters,tables):
        rendered,referenced=self.checked_sql(sql,parameters,tables)
        schema=self.schema(sorted(referenced)) if referenced else {'tables':[]}
        return {'valid':True,'effective_sql':rendered,'effective_parameters':self.bound_parameters(parameters,referenced),'metadata_only':True,
            'execution_mode':'direct','read_scope':'Read-only SQL executes on the selected database with configured adapter projections and row filters.',
            'query_plan':['Local SQL checks only; source compilation occurs after approval'],
            'referenced_columns':{table['table']:[column['name'] for column in table['columns']] for table in schema['tables']}}

    def validate(self,sql,parameters,tables):
        if self.kind not in {'postgresql','mysql'}:return self.plan(sql,parameters,tables)
        from sqlalchemy import text
        rendered,referenced=self.checked_sql(sql,parameters,tables)
        parameters=self.bound_parameters(parameters,referenced)
        with self.connection() as conn:
            conn.execute(self.statement("EXPLAIN "+rendered,parameters),parameters).fetchmany(50)
        # Conservatively retain all columns of referenced tables; remote EXPLAIN does not
        # provide SQLite's authorizer-level column reads consistently across engines.
        schema=self.schema(sorted(referenced)) if referenced else {"tables":[]}
        return {"valid":True,"query_plan":["Source EXPLAIN succeeded"],"referenced_columns":
            {t["table"]:[c["name"] for c in t["columns"]] for t in schema["tables"]}}

    def execute(self,sql,parameters,tables):
        from sqlalchemy import text
        rendered,referenced=self.checked_sql(sql,parameters,tables)
        parameters=self.bound_parameters(parameters,referenced)
        with self.connection() as conn:
            cursor=conn.execution_options(stream_results=True,max_row_buffer=self.row_limit+1).execute(self.statement(rendered,parameters),parameters)
            try:
                columns=list(cursor.keys())
                raw=cursor.fetchmany(self.row_limit+1)
            finally:
                cursor.close()
        return self.format_remote_result(columns,raw,rendered,parameters)

    def format_remote_result(self,columns,raw,rendered,parameters):
        partial=len(raw)>self.row_limit
        rows=[]
        for row in raw[:self.row_limit]:
            values=[]
            for value in row:
                if isinstance(value,(datetime,date)):
                    value=value.isoformat()
                elif isinstance(value,Decimal):
                    encode(value) # validate without converting to binary floating point
                elif isinstance(value,(bytes,bytearray,memoryview)):
                    value,partial="[binary content omitted]",True
                elif value is not None and type(value) not in {str,int,float,bool}:
                    value=str(value)
                if isinstance(value,float) and not math.isfinite(value):
                    value,partial=None,True
                if isinstance(value,str) and len(value)>2000:
                    value,partial=value[:2000]+" [clipped]",True
                values.append(value)
            if len(exact_dumps(rows+[values]).encode())>65536:
                partial=True
                break
            rows.append(values)
        return {"columns":columns,"rows":rows,"returned_rows":len(rows),"truncated":partial,"row_limit":self.row_limit,"executed_sql":rendered,'executed_parameters':parameters}

    def close(self):
        self.engine.dispose()
