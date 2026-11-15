"""Registered SQL dialects and conservative, operator-only connection configuration."""
import math
import re
from .tls_policy import tls_context,postgres_tls

WAREHOUSE_DIALECTS={'postgresql':'postgres','mysql':'mysql','mssql':'tsql','oracle':'oracle','snowflake':'snowflake','redshift':'redshift','bigquery':'bigquery'}
SOURCE_KINDS=frozenset({'sqlite',*WAREHOUSE_DIALECTS})


def new_connection(url,kind,schema,timeout,tls12_enabled=True):
    """Configure new adapters without accepting URL-selected plugins or local files."""
    simple=lambda value:isinstance(value,str) and bool(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]{0,127}',value))
    if not url.host or not re.fullmatch(r'[A-Za-z0-9_.:-]+',url.host) or not url.username or not url.password or url.drivername not in {'mssql+pytds','oracle+oracledb','snowflake','redshift+psycopg2'}:
        # Driver is replaced by the caller before this check.
        raise ValueError('Incomplete connector URL')
    if not simple(schema):raise ValueError('An explicit single schema is required')
    if kind in {'oracle','snowflake'} and schema!=schema.upper():raise ValueError('Use the native uppercase schema name')
    if kind=='mssql':
        if not url.database or set(url.query)-{'ssl_ca'}:raise ValueError('SQL Server URL options are managed by the adapter')
        import certifi
        cafile=url.query.get('ssl_ca') or (certifi.where() if tls12_enabled else None)
        return url.difference_update_query(['ssl_ca']),schema,{'login_timeout':5,'timeout':math.ceil(timeout),
            'cafile':cafile,'validate_host':True,'enc_login_only':False,'readonly':True}
    if kind=='redshift':
        if not url.database or set(url.query)-{'sslmode','sslrootcert','ssl_min_protocol_version'}:
            raise ValueError('Redshift requires certificate-verified TLS')
        return url.update_query_dict(postgres_tls(url,tls12_enabled)),schema,{'connect_timeout':5,**postgres_tls(url,tls12_enabled)}
    if kind=='oracle':
        service=url.query.get('service_name','')
        if url.database or set(url.query)-{'service_name','protocol','ssl_ca'} or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}',service):
            raise ValueError('Oracle requires a service_name URL option')
        protocol=url.query.get('protocol','tcps' if tls12_enabled else 'tcp')
        if protocol not in ({'tcps'} if tls12_enabled else {'tcp','tcps'}):raise ValueError('Oracle requires verified TCPS when TLS enforcement is enabled')
        context=tls_context(url.query.get('ssl_ca'),enforce_tls12=tls12_enabled) if protocol=='tcps' else None
        url=url.difference_update_query(['protocol','ssl_ca'])
        # SQLAlchemy otherwise builds a TCP DSN, which takes precedence over a
        # separate protocol argument. Override that generated DSN explicitly.
        dsn=f'(DESCRIPTION=(ADDRESS=(PROTOCOL={protocol.upper()})(HOST={url.host})(PORT={url.port or 1521}))(CONNECT_DATA=(SERVICE_NAME={service})))'
        args={'tcp_connect_timeout':5,'protocol':protocol,'dsn':dsn}
        if context:args.update(ssl_server_dn_match=True,ssl_context=context)
        return url,schema,args
    if kind=='snowflake':
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',url.host) or set(url.query)-{'warehouse','role'}:
            raise ValueError('Unsupported Snowflake account or URL options')
        parts=(url.database or '').split('/')
        if len(parts)!=2 or not all(simple(part) for part in parts) or parts[1]!=schema:
            raise ValueError('Snowflake URL database/schema must match the configured schema')
        if not all(simple(url.query.get(key)) for key in ('role','warehouse')):
            raise ValueError('Snowflake requires an explicit warehouse and restricted role')
        if url.query['role'].upper() in {'ACCOUNTADMIN','SECURITYADMIN','ORGADMIN','USERADMIN','SYSADMIN'}:
            raise ValueError('Use a dedicated SELECT-only Snowflake role')
        return url,schema,{'login_timeout':5,'network_timeout':max(5,math.ceil(timeout)),
            'protocol':'https','tls_skip_verify':False,**({'min_tls_version':'tls12'} if tls12_enabled else {}),
            'session_parameters':{'STATEMENT_TIMEOUT_IN_SECONDS':math.ceil(timeout),
                'STATEMENT_QUEUED_TIMEOUT_IN_SECONDS':math.ceil(timeout),'QUOTED_IDENTIFIERS_IGNORE_CASE':False}}
    raise ValueError('Unregistered connector')
