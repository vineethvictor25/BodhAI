"""Lossless JSON boundary for exact decimals and integers beyond browser safe range."""
import json
from decimal import Decimal

SAFE_INTEGER=2**53-1


def encode(value):
    if isinstance(value,Decimal):
        if not value.is_finite():
            raise ValueError('Non-finite exact decimal result is unsupported')
        if len(value.as_tuple().digits)>2000 or abs(value.as_tuple().exponent)>2000 or value.adjusted()>1999:
            raise ValueError('Exact decimal exceeds the supported representation size')
        text=format(value,'f')
        if len(text)>2000:
            raise ValueError('Exact decimal exceeds the supported representation size')
        return {'$qd_type':'decimal','value':text}
    if type(value) is int and abs(value)>SAFE_INTEGER:
        if value.bit_length()>6600:raise ValueError('Exact integer exceeds the supported representation size')
        return {'$qd_type':'integer','value':str(value)}
    if isinstance(value,dict):
        return {key:encode(item) for key,item in value.items()}
    if isinstance(value,(list,tuple)):
        return [encode(item) for item in value]
    return value


def decode(value):
    if isinstance(value,dict):
        if set(value)=={'$qd_type','value'} and isinstance(value['value'],str):
            if value['$qd_type']=='decimal':
                if len(value['value'])>2000:raise ValueError('Oversized decimal')
                result=Decimal(value['value'])
                encode(result)
                return result
            if value['$qd_type']=='integer':
                if len(value['value'])>2000:raise ValueError('Oversized integer')
                result=int(value['value'])
                encode(result)
                return result
        return {key:decode(item) for key,item in value.items()}
    if isinstance(value,list):return [decode(item) for item in value]
    return value


def dumps(value,**kwargs):
    return json.dumps(encode(value),allow_nan=False,**kwargs)


def loads(value):
    def invalid(constant):raise ValueError('Non-finite JSON numeric value')
    return decode(json.loads(value,parse_constant=invalid))


def display(value):
    if isinstance(value,Decimal):return encode(value)['value']
    if isinstance(value,str):return value
    return dumps(value)
