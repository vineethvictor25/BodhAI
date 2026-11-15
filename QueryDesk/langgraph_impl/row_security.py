"""Versioned, operator-owned row rules; no model SQL or identity claims are trusted."""
import hashlib
import json
import re
import threading
from pathlib import Path

from .config import APP_DIR
from .storage import QueryError


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}',value):
        raise ValueError('Row-policy identifiers must be simple table/column/attribute names')
    return value


def fields(value,allowed):
    if not isinstance(value,dict) or set(value)-set(allowed):raise ValueError('Unknown row-policy fields')


def validate(data):
    fields(data,{'version','admin_bypass','attributes','sources'})
    if type(data.get('version')) is not int or data['version']!=1 or type(data.get('admin_bypass')) is not bool:
        raise ValueError('Row policy requires version 1 and an explicit admin_bypass boolean')
    attributes=data.get('attributes',{})
    fields(attributes,{'users','groups'})
    for kind in ('users','groups'):
        subjects=attributes.get(kind,{})
        if not isinstance(subjects,dict) or len(subjects)>10000:raise ValueError('Invalid row-policy subjects')
        for subject,values in subjects.items():
            if not isinstance(subject,str) or not subject or len(subject)>128 or subject!=subject.casefold():raise ValueError('Row-policy account/group IDs must be lowercase')
            if kind=='users' and not re.fullmatch(r'[0-9a-f]{32}',subject):raise ValueError('User row attributes require the server-issued account ID, not a username')
            if kind=='groups' and not re.fullmatch(r'[a-z][a-z0-9_-]{0,49}',subject):raise ValueError('Invalid group policy ID')
            if not isinstance(values,dict) or len(values)>30:raise ValueError('Invalid subject attributes')
            for name,items in values.items():
                identifier(name)
                if not isinstance(items,list) or len(items)>500:raise ValueError('Attributes must be bounded value lists')
                for item in items:
                    if not ((type(item) is str and 0<len(item)<=200) or (type(item) is int and -(2**63)<=item<2**63)):
                        raise ValueError('Row attribute values must be strings or signed 64-bit integers')
    sources=data.get('sources')
    if not isinstance(sources,dict) or len(sources)>30:raise ValueError('Invalid row-policy sources')
    for source,tables in sources.items():
        if not isinstance(source,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,49}',source):raise ValueError('Invalid row-policy source ID')
        if not isinstance(tables,dict) or len(tables)>500:raise ValueError('Invalid row-policy table mappings')
        for table,rule in tables.items():
            identifier(table);fields(rule,{'mode','where','parents'})
            if rule.get('mode') not in {'global','filtered','deny'}:raise ValueError('Unknown row-policy table mode')
            predicates,parents=rule.get('where',[]),rule.get('parents',[])
            if not isinstance(predicates,list) or not isinstance(parents,list) or len(predicates)>20 or len(parents)>8:
                raise ValueError('Invalid row predicates/relationships')
            if rule['mode']!='filtered' and (predicates or parents):raise ValueError('Only filtered tables accept predicates or parents')
            if rule['mode']=='filtered' and not predicates and not parents:raise ValueError('Filtered tables require a restriction')
            for predicate in predicates:
                fields(predicate,{'column','op','attribute'})
                identifier(predicate.get('column'));identifier(predicate.get('attribute'))
                if predicate.get('op') not in {'eq','in'}:raise ValueError('Supported row operators are eq and in')
            for parent in parents:
                fields(parent,{'table','columns'})
                identifier(parent.get('table'))
                if parent['table'] not in tables:raise ValueError('Every policy parent requires an explicit table rule')
                pairs=parent.get('columns')
                if not isinstance(pairs,dict) or not 1<=len(pairs)<=8:raise ValueError('Specify all relationship column pairs')
                for local,remote in pairs.items():identifier(local);identifier(remote)
                if len(set(pairs.values()))!=len(pairs):raise ValueError('Relationship columns must be distinct')
        heights={}
        def walk(table,path):
            if table in path or len(path)>=8:raise ValueError('Cyclic or excessively deep row-policy relationships')
            if table in heights:return heights[table]
            height=1+max((walk(parent['table'],path+[table]) for parent in tables[table].get('parents',[])),default=0)
            if height>8:raise ValueError('Excessively deep row-policy relationships')
            heights[table]=height
            return height
        for table in tables:walk(table,[])
    return data


class RowSecurity:
    def __init__(self,values):
        flag=str(values.get('ROW_SECURITY_ENABLED','false')).lower()
        if flag not in {'true','false','1','0'}:raise ValueError('ROW_SECURITY_ENABLED must be true or false')
        self.enabled=flag in {'true','1'}
        self.path=Path(values.get('ROW_SECURITY_FILE') or APP_DIR/'row_security.json')
        if not self.path.is_absolute():self.path=APP_DIR/self.path
        self.lock=threading.RLock()
        self.data,self.signature=self.read()

    def read(self):
        if not self.enabled:data={'version':1,'admin_bypass':True,'attributes':{},'sources':{}}
        else:
            try:
                raw=self.path.read_bytes()
                if len(raw)>2_000_000:raise ValueError('Row policy exceeds size limit')
                data=validate(json.loads(raw.decode('utf-8')))
            except (OSError,ValueError,TypeError):raise ValueError('The row-security configuration is missing or invalid; access is blocked.') from None
        signature=hashlib.sha256(json.dumps({'enabled':self.enabled,'policy':data},sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return data,signature

    def refresh(self):
        with self.lock:
            data,signature=self.read()
            changed=signature!=self.signature
            self.data,self.signature=data,signature
            return changed

    def check(self,signature):
        try:_,current=self.read()
        except ValueError:raise PermissionError('Row policy is invalid. Access is blocked.') from None
        if current!=signature:raise PermissionError('Row policy changed. Submit the question again.')

    def scope(self,database,source,user,pii):
        with self.lock:return RowScope(self,database,source,user,pii)


class RowScope:
    def __init__(self,policy,database,source,user,pii):
        self.policy,self.database,self.source,self.pii=policy,database,source,pii
        self.signature=policy.signature
        self.enabled=policy.enabled
        self.bypass=not self.enabled or (user['role']=='admin' and policy.data['admin_bypass'])
        self.rules=policy.data['sources'].get(source,{})
        self.metadata={}
        contributions=[]
        attributes=policy.data.get('attributes',{})
        for group in user.get('groups',[]):contributions.append(attributes.get('groups',{}).get(group,{}))
        contributions.append(attributes.get('users',{}).get(user.get('id',''),{}))
        self.attributes={}
        for contribution in contributions:
            for key,values in contribution.items():
                values={(type(value).__name__,value) for value in values}
                self.attributes[key]=self.attributes[key]&values if key in self.attributes else values

    def check(self):self.policy.check(self.signature)

    def schema(self,table):
        if table not in self.database.allowed:raise QueryError('Row policy references a table outside source permissions')
        if table not in self.metadata:self.metadata[table]=self.database.schema([table])['tables'][0]
        return self.metadata[table]

    def filter(self,table,dialect='sqlite',namespace=None):
        self.check()
        parameters={}
        quote=lambda name:('`'+name.replace('`','``')+'`') if dialect in {'mysql','bigquery'} else ('"'+name.replace('"','""')+'"')
        qualified=lambda table:(quote(namespace)+'.' if namespace else '')+quote(table)
        key_prefix='__rls_'+hashlib.sha256(table.encode()).hexdigest()[:12]+'_'
        nodes=[0]
        def column(table,alias,name):
            if name not in {c['name'] for c in self.schema(table)['columns']} or name in self.pii.get(table,[]):
                raise QueryError('Row policies require existing non-PII columns')
            return quote(alias)+'.'+quote(name)
        def compile_rule(table,alias,path):
            nodes[0]+=1
            if nodes[0]>200:raise QueryError('Row-policy expression is too large')
            if table in path or len(path)>=8:raise QueryError('Invalid row-policy relationship path')
            rule=self.rules.get(table,{'mode':'deny'})
            if rule['mode']=='global':return '1=1'
            if rule['mode']=='deny':return '1=0'
            clauses=[]
            for predicate in rule.get('where',[]):
                expression=column(table,alias,predicate['column'])
                values=sorted(self.attributes.get(predicate['attribute'],set()),key=lambda value:(value[0],str(value[1])))
                if not values or (predicate['op']=='eq' and len(values)!=1):clauses.append('1=0');continue
                binds=[]
                for _,value in values:
                    if len(parameters)>=500:raise QueryError('Row-policy parameter budget exceeded')
                    key=key_prefix+str(len(parameters));parameters[key]=value;binds.append(':'+key)
                clauses.append(expression+(' = '+binds[0] if predicate['op']=='eq' else ' IN ('+','.join(binds)+')'))
            for parent in rule.get('parents',[]):
                target=parent['table'];target_alias='__rls_p'+str(len(path)+1)
                primary={c['name'] for c in self.schema(target)['columns'] if c['primary_key']}
                if not primary or not primary<=set(parent['columns'].values()):
                    raise QueryError('Row relationships must include the complete parent primary key')
                joins=[column(target,target_alias,remote)+' = '+column(table,alias,local) for local,remote in parent['columns'].items()]
                joins.append(compile_rule(target,target_alias,path+[table]))
                clauses.append('EXISTS (SELECT 1 FROM '+qualified(target)+' AS '+quote(target_alias)+' WHERE '+' AND '.join('('+clause+')' for clause in joins)+')')
            return ' AND '.join('('+clause+')' for clause in clauses)
        predicate='1=1' if self.bypass else compile_rule(table,'__rls_b',[])
        if len(predicate)>20000:raise QueryError('Row-policy expression exceeds its size limit')
        return {'sql':predicate,'parameters':parameters,'mode':'bypass' if self.bypass else self.rules.get(table,{'mode':'deny'})['mode']}

    def describe(self,tables):
        dialect=getattr(self.database,'kind','sqlite');namespace=getattr(self.database,'namespace',None)
        return {'enabled':self.enabled,'admin_bypass':self.enabled and self.bypass,'policy_version':self.signature,
            'summary':('Administrator row-policy bypass is active.' if self.enabled and self.bypass else
                'Results include only rows allowed by your configured access attributes.' if self.enabled else 'Row-level security is disabled.'),
            'filters':[{**self.filter(table,dialect,namespace),'table':table} for table in sorted(tables)] if self.enabled else []}
