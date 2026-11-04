"""Column protection; keys are external references, never stored with data."""
import base64
import hashlib
import hmac
import json
import os
import re
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

PREFIX='pii:aesgcm:v1:'
FIELD=re.compile(r'(?:^|[._])(?:email|email_address|phone|phone_number|mobile|ssn|social_security_number|pan|passport|national_id|dob|date_of_birth|birth_date|first_name|last_name|full_name|customer_name|home_address|street_address)(?:$|[._])',re.I)
EMAIL=re.compile(r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b',re.I)
SSN=re.compile(r'\b\d{3}-\d{2}-\d{4}\b')


def sensitive_source_paths(metadata):
    privacy=metadata.get('privacy',{})
    return set(privacy.get('protected_columns',[])) | set(privacy.get('sensitive_columns',[]))


def key(reference):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,120}',reference):raise ValueError('Invalid privacy key reference')
    provider=os.getenv('PII_KEY_PROVIDER','environment')
    try:
        if provider=='environment':encoded=os.getenv(reference,'')
        elif provider=='azure_key_vault':
            from urllib.parse import urlparse
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
            url=os.getenv('PII_KEY_VAULT_URL','');u=urlparse(url)
            if u.scheme!='https' or not u.hostname or not u.hostname.endswith('.vault.azure.net') or u.username or u.password or u.query or u.fragment:raise ValueError('Invalid vault URL')
            with DefaultAzureCredential() as credential:
                with SecretClient(vault_url=url,credential=credential) as client:encoded=client.get_secret(reference).value
        else:raise ValueError('Unknown key provider')
        value=base64.b64decode(encoded,validate=True)
        if len(value)!=32:raise ValueError('Wrong key size')
        return value
    except Exception:
        raise ValueError('Privacy key unavailable: configure a base64-encoded 32-byte key through the approved environment or Azure Key Vault reference') from None


def encrypt(value,reference,material=None,context=''):
    nonce=os.urandom(12);cipher=AESGCM(material or key(reference))
    payload=cipher.encrypt(nonce,json.dumps(value,ensure_ascii=False,allow_nan=False).encode(),context.encode())
    return PREFIX+reference+':'+base64.b64encode(nonce+payload).decode()


def decrypt(value,context='',keys=None):
    if not isinstance(value,str) or not value.startswith(PREFIX):return value
    try:
        reference,encoded=value[len(PREFIX):].split(':',1);raw=base64.b64decode(encoded,validate=True)
        if keys is None:keys={}
        if reference not in keys:keys[reference]=key(reference)
        clear=AESGCM(keys[reference]).decrypt(raw[:12],raw[12:],context.encode())
        return json.loads(clear)
    except (ValueError,InvalidTag,KeyError):raise ValueError('Encrypted PII column cannot be authenticated or decrypted; check key version and column context') from None


def walk(record,prefix=''):
    for name,value in record.items():
        path=f'{prefix}.{name}' if prefix else str(name)
        if isinstance(value,dict):yield from walk(value,path)
        else:yield path,value


def flagged(path,value=None):
    if isinstance(value,list):return bool(FIELD.search(path)) or any(flagged(path,v) for v in value)
    if isinstance(value,dict):return bool(FIELD.search(path)) or any(flagged(path+'.'+str(k),v) for k,v in value.items())
    return bool(FIELD.search(path) or isinstance(value,str) and not value.startswith(PREFIX) and (EMAIL.search(value) or SSN.search(value)))


def protect_source(rows,declared=(),reference='PII_ENCRYPTION_KEY'):
    paths=set(declared)
    paths.update(path for row in rows for path,value in walk(row) if flagged(path,value))
    existing={path for row in rows for path,_ in walk(row)}
    if not paths.issubset(existing):raise ValueError('Declared PII field is absent from the source')
    if not paths:return rows,[]
    needs_key=any(path in paths and value is not None and not(isinstance(value,str) and value.startswith(PREFIX)) for row in rows for path,value in walk(row))
    material=key(reference) if needs_key else None
    def protect(record,prefix=''):
        out={}
        for name,value in record.items():
            path=f'{prefix}.{name}' if prefix else str(name)
            if path in paths:
                out[name]=value if value is None or isinstance(value,str) and value.startswith(PREFIX) else encrypt(value,reference,material,context=path)
            elif isinstance(value,dict):out[name]=protect(value,path)
            else:out[name]=value
        return out
    return [protect(r) for r in rows],sorted(paths)


def decrypt_source(rows):
    keys={}
    def clear(record,prefix=''):
        result={}
        for name,value in record.items():
            path=f'{prefix}.{name}' if prefix else str(name)
            result[name]=clear(value,path) if isinstance(value,dict) else decrypt(value,path,keys)
        return result
    return [clear(r) for r in rows]


def protect_output(value,column):
    if value is None:return value
    if column.protection=='mask':return '[REDACTED]'
    if column.protection=='encrypt':return encrypt(value,column.key_ref,context=column.name)
    if column.protection=='hmac_sha256':
        return 'pii:hmac:v1:'+column.key_ref+':'+hmac.new(key(column.key_ref),str(value).encode(),hashlib.sha256).hexdigest()
    if flagged(column.name,value):raise ValueError('Sensitive output needs an explicit column protection policy')
    return value


def redact(value,path=''):
    if isinstance(value,dict):return {k:redact(v,f'{path}.{k}' if path else k) for k,v in value.items()}
    if isinstance(value,list):return [redact(v,path) for v in value]
    if flagged(path,value) or isinstance(value,str) and value.startswith(PREFIX):return '[REDACTED]'
    return value


def ai_payload(payload):
    # Block known clear-text PII patterns before any provider/fallback call.
    if EMAIL.search(payload) or SSN.search(payload) or PREFIX in payload or 'pii:hmac:v1:' in payload:raise ValueError('Model request blocked: remove personal data and encrypted PII values from model context')
    return payload
