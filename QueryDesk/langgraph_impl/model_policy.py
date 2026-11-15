"""Mandatory, local-only policy checks before provider requests; no payload logging."""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from .config import APP_DIR,environment_values
from .value_codec import dumps


class EgressDenied(RuntimeError):
    def details(self):return {'kind':'egress_policy'}


def guard_tracing():
    if any(str(os.environ.get(key,'false')).lower() in {'true','1'} for key in ('LANGSMITH_TRACING','LANGCHAIN_TRACING','LANGCHAIN_TRACING_V2')):
        raise EgressDenied('External model tracing must be disabled under the data-handling policy.')


class ModelPolicy:
    def __init__(self,data):
        if not isinstance(data,dict) or set(data)-{'version','profiles','sources'} or data.get('version')!=1:
            raise EgressDenied('Invalid model data-policy configuration.')
        self.profiles=data.get('profiles',[])
        self.sources=data.get('sources',{})
        if not isinstance(self.profiles,list) or not isinstance(self.sources,dict):raise EgressDenied('Invalid model profiles or source classifications.')
        for profile in self.profiles:
            if not isinstance(profile,dict) or set(profile)-{'id','endpoint','models','approved','classifications','allow_detected_pii','pii_sender_groups','max_payload_bytes'}:
                raise EgressDenied('Invalid model profile fields.')
            if type(profile.get('approved')) is not bool or type(profile.get('allow_detected_pii',False)) is not bool:
                raise EgressDenied('Model approval/PII flags must be booleans.')
            if not isinstance(profile.get('models'),list) or not isinstance(profile.get('classifications'),list):
                raise EgressDenied('Model profiles require model and classification lists.')
            if not isinstance(profile.get('id'),str) or not profile['id'] or not isinstance(profile.get('endpoint'),str):
                raise EgressDenied('Model profiles require a name and endpoint.')
            if not profile['models'] or any(not isinstance(model,str) or not model for model in profile['models']) or any(not isinstance(item,str) or item not in {'public','internal','restricted'} for item in profile['classifications']):
                raise EgressDenied('Invalid model identifiers or data classifications.')
            if not isinstance(profile.get('pii_sender_groups',[]),list):raise EgressDenied('Invalid PII sender groups.')
            if any(not isinstance(group,str) or not group.startswith(('role:','group:')) for group in profile.get('pii_sender_groups',[])):
                raise EgressDenied('Sender groups must explicitly identify role: or group: grants.')
        for source in self.sources.values():
            if not isinstance(source,dict) or set(source)-{'classification','require_local'} or source.get('classification') not in {'public','internal','restricted'} or type(source.get('require_local',False)) is not bool:
                raise EgressDenied('Invalid source data classification.')

    @classmethod
    def load(cls):
        values=environment_values()
        path=Path(values.get('MODEL_POLICY_FILE') or APP_DIR/'model_policy.json')
        if not path.is_absolute():path=APP_DIR/path
        try:return cls(json.loads(path.read_text(encoding='utf-8')))
        except (OSError,ValueError):raise EgressDenied('Configure an approved model profile in model_policy.json before using the LLM.') from None

    def check(self,settings,source,context,instructions='',groups=()):
        guard_tracing()
        endpoint=settings.base_url.rstrip('/')
        url=urlsplit(endpoint)
        local=url.hostname in {'localhost','127.0.0.1','::1'}
        if url.username or url.password or url.query or url.fragment or url.scheme not in {'http','https'} or (url.scheme!='https' and not local):
            raise EgressDenied('The model endpoint does not meet transport policy.')
        profiles=[p for p in self.profiles if p.get('endpoint','').rstrip('/')==endpoint and settings.model in p['models'] and p['approved']]
        if len(profiles)!=1:raise EgressDenied('The selected endpoint/model does not have exactly one approved data-handling profile.')
        profile=profiles[0]
        scope=self.sources.get(source,{'classification':'restricted','require_local':True})
        if scope['classification'] not in profile['classifications'] or (scope.get('require_local') and not local):
            raise EgressDenied('This source classification cannot be sent to the selected model profile.')
        payload=instructions+'\n'+dumps(context)
        cap=profile.get('max_payload_bytes',65536)
        if type(cap) is not int or not 1024<=cap<=1000000 or len(payload.encode())>cap:
            raise EgressDenied('The model context exceeds the approved payload budget.')
        # Detection is deliberately conservative, local, and not a complete PII classifier.
        credentials=r'(?i)(?:\b(?:sk-|gsk_)[a-z0-9_-]{16,}|\bbearer\s+[a-z0-9._-]{16,}|\b(?:password|api[_ -]?key|secret)[\"\x27]?\s*[:=]\s*[\"\x27]?[a-z0-9_+/=-]{8,})'
        if re.search(credentials,payload):raise EgressDenied('A possible credential was detected. Remove it before requesting model processing.')
        email=r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'
        phone=r'(?<!\w)\+\d[\d ()-]{8,}\d|\b\d{3}-\d{2}-\d{4}\b|\b\d{10,15}\b'
        if re.search(email,payload) or re.search(phone,payload):
            if not profile.get('allow_detected_pii',False) or not set(groups)&set(profile.get('pii_sender_groups',[])):
                raise EgressDenied('Possible sensitive personal information was detected. Both profile approval and a separate sender grant are required.')
        return {'profile':profile.get('id','approved'),'classification':scope['classification'],'mode':'local' if local else 'remote'}
