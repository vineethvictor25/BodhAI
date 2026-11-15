"""Verified TLS 1.2+ by default; explicit development plaintext for SQL transports."""
import ssl


def tls_context(cafile=None,certfile=None,keyfile=None,enforce_tls12=True):
    context=ssl.create_default_context(cafile=cafile)
    if enforce_tls12:context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.check_hostname=True
    context.verify_mode=ssl.CERT_REQUIRED
    if certfile:context.load_cert_chain(certfile,keyfile)
    elif keyfile:raise ValueError('A TLS client key requires its certificate')
    return context


def postgres_tls(url,enforce_tls12=True):
    allowed={'TLSv1.2','TLSv1.3'} if enforce_tls12 else {'TLSv1','TLSv1.1','TLSv1.2','TLSv1.3'}
    mode=url.query.get('sslmode','verify-full' if enforce_tls12 else 'disable')
    if mode not in ({'verify-full'} if enforce_tls12 else {'verify-full','disable'}) or url.query.get('ssl_min_protocol_version','TLSv1.2') not in allowed:
        raise ValueError('Verified TLS 1.2 or newer is required')
    result={'sslmode':mode}
    if mode=='disable':return result
    if enforce_tls12 or 'ssl_min_protocol_version' in url.query:result['ssl_min_protocol_version']=url.query.get('ssl_min_protocol_version','TLSv1.2')
    return result


def mysql_tls(url,enforce_tls12=True):
    for key in ('ssl_verify_cert','ssl_verify_identity'):
        if key in url.query and url.query[key].lower() not in {'true','1'}:raise ValueError('TLS verification cannot be disabled')
    if not enforce_tls12 and not any(key in url.query for key in ('ssl_ca','ssl_cert','ssl_key')):return None
    return tls_context(url.query.get('ssl_ca'),url.query.get('ssl_cert'),url.query.get('ssl_key'),enforce_tls12)


def secure_http_session(credentials,enforce_tls12=True):
    """Separate verified transports for warehouse API calls and credential refresh."""
    import requests
    from requests.adapters import HTTPAdapter
    from google.auth.transport.requests import AuthorizedSession,Request
    class TLSAdapter(HTTPAdapter):
        def init_poolmanager(self,*args,**kwargs):
            kwargs['ssl_context']=tls_context(enforce_tls12=enforce_tls12)
            return super().init_poolmanager(*args,**kwargs)
        def proxy_manager_for(self,proxy,**kwargs):
            kwargs['ssl_context']=tls_context(enforce_tls12=enforce_tls12)
            if proxy.lower().startswith('https://'):kwargs['proxy_ssl_context']=tls_context(enforce_tls12=enforce_tls12)
            return super().proxy_manager_for(proxy,**kwargs)
        def send(self,request,**kwargs):
            if not request.url.lower().startswith('https://'):raise ValueError('Warehouse API requests require HTTPS')
            if kwargs.get('verify') is False:raise ValueError('TLS verification cannot be disabled')
            return super().send(request,**kwargs)
    refresh=requests.Session();refresh.mount('https://',TLSAdapter())
    session=AuthorizedSession(credentials,auth_request=Request(session=refresh))
    session.mount('https://',TLSAdapter())
    session.mount('http://',TLSAdapter())
    return session,refresh
