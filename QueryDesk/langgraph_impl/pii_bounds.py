"""Shared limits for adapters that offer local, bounded PII handling."""
import math
import time
from dataclasses import asdict,dataclass

from .storage import QueryError


class PIIBoundError(QueryError):
    def details(self):return {'kind':'pii_bound'}


@dataclass(frozen=True)
class PIILimits:
    max_rows:int=100000
    max_copy_bytes:int=20000000
    max_input_bytes:int=40000000
    max_envelope_bytes:int=1000000
    max_cell_bytes:int=1000000
    max_decrypt_cells:int=500000
    timeout_seconds:float=10.0

    def __post_init__(self):
        ceilings={'max_rows':100000,'max_copy_bytes':20000000,'max_input_bytes':40000000,
            'max_envelope_bytes':1000000,'max_cell_bytes':1000000,'max_decrypt_cells':500000}
        for name,ceiling in ceilings.items():
            value=getattr(self,name)
            if type(value) is not int or not 1<=value<=ceiling:raise ValueError('Invalid bounded PII limit: '+name)
        if type(self.timeout_seconds) not in {int,float} or not math.isfinite(self.timeout_seconds) or not 0<self.timeout_seconds<=60:
            raise ValueError('PII preparation timeout must be positive and at most 60 seconds')

    @classmethod
    def load(cls,values):
        defaults=cls()
        names={'max_rows':'PII_MAX_COPY_ROWS','max_copy_bytes':'PII_MAX_COPY_BYTES','max_input_bytes':'PII_MAX_INPUT_BYTES',
            'max_envelope_bytes':'PII_MAX_ENVELOPE_BYTES','max_cell_bytes':'PII_MAX_CELL_BYTES','max_decrypt_cells':'PII_MAX_DECRYPT_CELLS',
            'timeout_seconds':'PII_COPY_TIMEOUT_SECONDS'}
        try:return cls(**{field:(float(values.get(key,getattr(defaults,field))) if field=='timeout_seconds' else int(str(values.get(key,getattr(defaults,field))))) for field,key in names.items()})
        except (ValueError,TypeError):raise ValueError('Invalid bounded PII processing configuration') from None

    def payload(self):return asdict(self)


def value_size(value):
    if value is None:return 0
    if isinstance(value,str):return len(value.encode('utf-8'))
    if isinstance(value,(bytes,bytearray,memoryview)):return len(value)
    if type(value) in {int,float,bool}:return len(str(value).encode('ascii'))
    raise PIIBoundError('PII processing requires scalar SQLite values.')


class PIIBudget:
    def __init__(self,limits,clock=time.monotonic):
        self.limits,self.clock=limits,clock
        self.deadline=clock()+limits.timeout_seconds
        self.rows=self.input_bytes=self.copy_bytes=self.decrypt_cells=0

    def check_time(self):
        if self.clock()>=self.deadline:raise PIIBoundError('Local PII preparation exceeded its approved time limit. No partial result was returned.')

    def before_row(self,values):
        self.check_time();self.rows+=1
        if self.rows>self.limits.max_rows:raise PIIBoundError('Local PII preparation exceeded its approved row limit. Narrow the query.')
        self.input_bytes+=sum(value_size(value) for value in values)
        if self.input_bytes>self.limits.max_input_bytes:raise PIIBoundError('Local PII preparation exceeded its approved input-byte limit. Narrow the query.')

    def before_decrypt(self):
        self.check_time();self.decrypt_cells+=1
        if self.decrypt_cells>self.limits.max_decrypt_cells:raise PIIBoundError('Local PII preparation exceeded its approved decryption-count limit.')

    def copy_row(self,values):
        self.check_time();self.copy_bytes+=sum(value_size(value) for value in values)
        if self.copy_bytes>self.limits.max_copy_bytes:raise PIIBoundError('Local PII preparation exceeded its approved plaintext-copy limit. Narrow the query.')
