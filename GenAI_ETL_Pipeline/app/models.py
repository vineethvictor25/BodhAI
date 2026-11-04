"""The executable intermediate representation: models never produce executable free text."""
from typing import Any, Literal
import re
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .models_base import StrictModel
from .destinations import Destination


Transform = Literal["trim", "lower", "upper", "length_m", "number", "currency_number", "iso_date", "iso_timestamp", "collapse_whitespace", "strip_controls", "boolean_number", "round_2", "sha256"]


class Column(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    dtype: Literal["string", "number", "integer"] = "string"
    required: bool = False
    aliases: list[str] = Field(default_factory=list, max_length=30)
    transforms: list[Transform] = Field(default_factory=list, max_length=8)
    minimum: float | None = None
    maximum: float | None = None
    allowed: list[str] = Field(default_factory=list, max_length=100)
    # Context fill is an explicit contract rule, not an LLM guess.
    default: str | float | int | None = None
    value_map: dict[str,str] = Field(default_factory=dict,max_length=200)
    unmapped: Literal['keep','reject']='reject'
    format: Literal['email','iso_date','uuid'] | None = None
    classification:Literal['internal','public','pii','sensitive']='internal'
    protection:Literal['none','mask','hmac_sha256','encrypt']='none'
    key_ref:str=Field(default='PII_ENCRYPTION_KEY',pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,120}$')

    @model_validator(mode="after")
    def valid_bounds(self):
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum exceeds maximum")
        if self.classification in ('pii','sensitive') and self.protection=='none':raise ValueError('PII/sensitive columns require masking, keyed tokenization or encryption')
        if self.protection!='none' and self.dtype!='string':raise ValueError('Protected columns use string output type; keep analytical numeric fields separate')
        if self.protection=='hmac_sha256' and self.key_ref=='PII_ENCRYPTION_KEY':raise ValueError('HMAC needs a separate key reference such as PII_HMAC_KEY')
        return self


class DerivedColumn(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    operation: Literal['add','subtract','multiply','divide','concat','coalesce','year','month']
    fields: list[str] = Field(min_length=1,max_length=8)
    separator: str = Field(default=' ',max_length=20)
    dtype: Literal['string','number','integer'] = 'number'


class RowFilter(StrictModel):
    column: str
    operator: Literal['eq','ne','gt','gte','lt','lte','not_null']
    value: str | float | bool | None = None


class Measure(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    operation: Literal['count','sum','avg','min','max']
    column: str | None = None


class GoldSpec(StrictModel):
    group_by: list[str] = Field(default_factory=list,max_length=8)
    measures: list[Measure] = Field(min_length=1,max_length=20)


class BusinessContext(StrictModel):
    grain:str=Field(min_length=5,max_length=500)
    owner:str=Field(min_length=1,max_length=200)
    consumers:list[str]=Field(default_factory=list,max_length=30)
    freshness_minutes:int=Field(default=1440,ge=1,le=525600)
    model:Literal['medallion','fact','dimension']='medallion'


class LoadStrategy(StrictModel):
    mode:Literal['upsert','append','full_refresh','partition_overwrite','scd2']='upsert'
    partition_column:str|None=None
    partition_values:list[str]=Field(default_factory=list,max_length=31)
    tracked_columns:list[str]=Field(default_factory=list,max_length=50)


class BatchCheck(StrictModel):
    name:str=Field(pattern=r'^[a-z][a-z0-9_]{0,62}$')
    kind:Literal['row_count','sum','freshness','volume_change','reference']
    severity:Literal['critical','warning']='critical'
    column:str|None=None
    minimum:float|None=None
    maximum:float|None=None
    reference_target:str|None=Field(default=None,pattern=r'^[a-z][a-z0-9_]{0,62}$')


class PipelineSpec(StrictModel):
    name: str = Field(default="product_catalog", pattern=r"^[a-z][a-z0-9_]{0,62}$")
    description: str = Field(default="", max_length=4000)
    target: str = Field(default="products", pattern=r"^[a-z][a-z0-9_]{0,62}$")
    columns: list[Column] = Field(min_length=1, max_length=50)
    primary_key: str
    max_reject_ratio: float = Field(default=0.4, ge=0, le=1)
    mapping_threshold: float = Field(default=0.82, ge=0.5, le=1)
    mapping_overrides: dict[str, str] = Field(default_factory=dict)
    derived_columns: list[DerivedColumn] = Field(default_factory=list,max_length=20)
    row_filters: list[RowFilter] = Field(default_factory=list,max_length=20)
    gold: GoldSpec | None = None
    business: BusinessContext | None = None
    load: LoadStrategy = Field(default_factory=LoadStrategy)
    batch_checks:list[BatchCheck]=Field(default_factory=list,max_length=30)
    deduplication:Literal['reject','latest']='reject'
    dedup_order_by:str|None=None
    destination:Destination=Field(default_factory=Destination)

    @model_validator(mode="after")
    def consistent(self):
        names = [c.name for c in self.columns]
        if len(names) != len(set(names)) or self.primary_key not in names:
            raise ValueError("Column names must be unique and include primary_key")
        if not next(c for c in self.columns if c.name == self.primary_key).required:
            raise ValueError("primary_key must be required")
        if next(c for c in self.columns if c.name==self.primary_key).protection in ('mask','encrypt'):raise ValueError('Business keys need stable keyed tokenization; masking/randomized encryption cannot preserve identity')
        if not set(self.mapping_overrides).issubset(names):
            raise ValueError("Mapping override contains an unknown target column")
        if len(set(self.mapping_overrides.values())) != len(self.mapping_overrides):
            raise ValueError("Mapping overrides must use distinct source columns")
        types={c.name:c.dtype for c in self.columns}
        for derived in self.derived_columns:
            if derived.name in types or not set(derived.fields).issubset(types):
                raise ValueError('Derived fields must reference earlier columns and use a unique output name')
            if derived.operation in ('subtract','divide') and len(derived.fields)!=2:
                raise ValueError('Subtract/divide require exactly two fields')
            if derived.operation in ('year','month') and len(derived.fields)!=1:
                raise ValueError('Date parts require one field')
            types[derived.name]=derived.dtype
        if any(f.column not in types for f in self.row_filters):raise ValueError('Filter references unknown column')
        if self.deduplication=='latest' and self.dedup_order_by not in types:raise ValueError('Latest deduplication requires an order column')
        encrypted={c.name for c in self.columns if c.protection=='encrypt'}
        if self.dedup_order_by in encrypted:raise ValueError('Randomly encrypted values cannot order deduplication')
        if self.load.mode=='scd2' and encrypted.intersection(self.load.tracked_columns or types):raise ValueError('SCD2 tracking needs stable HMAC companion fields, not randomized ciphertext')
        if self.load.mode=='partition_overwrite' and (self.load.partition_column not in types or not self.load.partition_values):raise ValueError('Partition overwrite needs a valid partition column and explicit partition values')
        if not set(self.load.tracked_columns).issubset(types):raise ValueError('Unknown SCD tracked column')
        if len({c.name for c in self.batch_checks})!=len(self.batch_checks):raise ValueError('Batch check names must be unique')
        for check in self.batch_checks:
            if check.kind in ('sum','freshness','reference') and check.column not in types:raise ValueError('Batch check needs a valid column')
            if check.kind=='sum' and types[check.column] not in ('number','integer'):raise ValueError('Sum check requires numeric column')
            if check.kind=='reference' and not check.reference_target:raise ValueError('Reference check requires target')
            if check.kind!='reference' and check.minimum is None and check.maximum is None:raise ValueError('Check requires minimum or maximum threshold')
            if check.minimum is not None and check.maximum is not None and check.minimum>check.maximum:raise ValueError('Check minimum exceeds maximum')
        if self.gold:
            g=self.gold; outputs=[*g.group_by,*[m.name for m in g.measures]]
            if set(g.group_by)&encrypted:raise ValueError('Use stable HMAC tokens to group sensitive values; ciphertext is randomized')
            if len(outputs)!=len(set(outputs)) or not set(g.group_by).issubset(types):raise ValueError('Gold output names must be unique and grouping columns must exist')
            for m in g.measures:
                if m.operation=='count' and m.column is None:continue
                if m.column not in types:raise ValueError('Gold measure references unknown column')
                if m.operation!='count' and types[m.column] not in ('number','integer'):raise ValueError('Gold numeric measures require numeric columns')
        return self


class PlanRequest(StrictModel):
    prompt: str = Field(min_length=10, max_length=12000)


class SourceInput(StrictModel):
    dataset_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class RunRequest(StrictModel):
    spec: PipelineSpec
    dataset_ids: list[str] = Field(min_length=1, max_length=20)
    idempotency_key: str = Field(min_length=1, max_length=120)
    approved_mapping_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    pipeline_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    pipeline_revision: int | None = Field(default=None,ge=1)

    @model_validator(mode="after")
    def version_reference(self):
        if (self.pipeline_id is None)!=(self.pipeline_revision is None):
            raise ValueError("Pipeline ID and revision must be provided together")
        if self.pipeline_id and self.approved_mapping_id:
            raise ValueError("Managed versions already pin their reviewed mappings")
        return self

    @field_validator("dataset_ids")
    @classmethod
    def valid_ids(cls, ids):
        if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[0-9a-f]{32}", x) for x in ids):
            raise ValueError("Unique dataset identifiers are required")
        return ids


class TranslateRequest(StrictModel):
    sql: str = Field(min_length=1, max_length=40000)
    source: Literal["tsql", "oracle", "mysql", "postgres", "spark", "hive"] = "tsql"
    target: Literal["snowflake", "bigquery", "redshift", "spark", "duckdb"] = "snowflake"


class ExtractedDocument(StrictModel):
    product_id: str | None = None
    product_name: str | None = None
    length: str | None = None
    price: float | None = None
    currency: str | None = None
    summary: str
    sentiment: Literal["positive", "negative", "neutral"]
    evidence: list[str] = Field(default_factory=list, max_length=10)


def demo_spec() -> PipelineSpec:
    return PipelineSpec(columns=[
        Column(name="product_id", required=True, aliases=["sku", "item_id", "product.code"], transforms=["trim", "upper"]),
        Column(name="product_name", required=True, aliases=["name", "title", "product.title"], transforms=["trim"]),
        Column(name="length_m", dtype="number", aliases=["length", "dimensions.length", "size"], transforms=["length_m"], minimum=0.001, maximum=100),
        Column(name="price", dtype="number", required=True, aliases=["cost", "pricing.amount", "amount"], transforms=["number"], minimum=0, maximum=1000000),
        Column(name="currency", aliases=["ccy", "pricing.currency"], transforms=["trim", "upper"], allowed=["USD", "EUR", "INR", "GBP"], default="USD"),
        Column(name="category", aliases=["type", "product.category"], transforms=["trim", "lower"]),
    ], primary_key="product_id")
