"""Local typed handoffs and execution authorization; no external application imports."""
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from typing import Literal
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, model_validator


class Handoff(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class VisualizationOutput(Handoff):
    """A declarative chart recommendation using supplied zero-based column indices."""
    status:Literal['chart','needs_data']
    kind:Literal['bar','line','scatter','kpi','none']
    library:Literal['plotly','matplotlib','seaborn','none']
    title:str=Field(min_length=1,max_length=120)
    reason:str=Field(min_length=1,max_length=1200)
    x_column:StrictInt|None
    y_column:StrictInt|None
    suggestions:list[str]=Field(max_length=4)

    @model_validator(mode='after')
    def bounded_suggestions(self):
        if not self.title.strip() or not self.reason.strip():raise ValueError('Provide a meaningful title and explanation')
        if any(not text.strip() or len(text)>500 for text in self.suggestions):raise ValueError('Use short, non-empty suggestions')
        if self.status=='needs_data' and not self.suggestions:raise ValueError('Suggest how to improve unsuitable results')
        if (self.status=='needs_data')!=(self.library=='none'):raise ValueError('Choose a supported renderer only for a chart')
        if self.status=='needs_data' and (self.kind!='none' or self.x_column is not None or self.y_column is not None):raise ValueError('A needs-data response cannot define a chart')
        if self.status=='chart' and (self.kind=='none' or self.y_column is None or (self.kind!='kpi' and self.x_column is None)):raise ValueError('Chart axes are required')
        return self


class DomainDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid",str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,49}$")
    name: str = Field(min_length=1,max_length=100)
    description: str = Field(min_length=1,max_length=2000)
    tables: list[str] = Field(min_length=1,max_length=100)
    keywords: list[str] = Field(default_factory=list,max_length=30)

    @model_validator(mode="after")
    def validate_terms(self):
        if any(not value.strip() or len(value)>200 for value in self.tables+self.keywords):
            raise ValueError("Table names and business terms must be non-empty and at most 200 characters")
        self.tables=sorted(set(self.tables))
        self.keywords=list(dict.fromkeys(self.keywords))
        return self


class DomainSuggestions(Handoff):
    """Draft business domains inferred only from supplied permitted metadata; user review required."""
    domains: list[DomainDefinition] = Field(min_length=1,max_length=8)


class IntentOutput(Handoff):
    """An interpreted analytics question, clarification, or refusal."""
    status: Literal["ready", "clarification", "refused"]
    task: str
    assumptions: list[str]
    message: str

    @model_validator(mode="after")
    def meaningful(self):
        if not (self.task if self.status == "ready" else self.message).strip():
            raise ValueError("Provide a task or user-facing clarification/refusal")
        return self


class SchemaSelection(Handoff):
    """Select allowed tables needed to answer the question, including join bridges."""
    tables: list[str] = Field(min_length=1)
    reason: str


class ColumnSet(Handoff):
    table: str
    columns: list[str]


class ColumnSelection(Handoff):
    """Select output, filter, grouping, ordering and join columns for each table."""
    tables: list[ColumnSet]
    reason: str


class DecimalValue(Handoff):
    type:Literal['decimal']
    value:StrictStr=Field(max_length=2000)

    @model_validator(mode='after')
    def finite(self):
        from .value_codec import encode
        from decimal import InvalidOperation
        try:encode(Decimal(self.value))
        except (InvalidOperation,ValueError):raise ValueError('Provide a finite exact decimal string') from None
        return self


class Parameter(Handoff):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    value: StrictStr | StrictInt | StrictFloat | DecimalValue | None


class SQLSubmission(Handoff):
    """One read-only statement in the selected source dialect with named bound filter values."""
    sql: str = Field(min_length=1, max_length=20000)
    parameters: list[Parameter]

    @model_validator(mode="after")
    def check_parameters(self):
        import math
        if not self.sql.strip():
            raise ValueError("SQL cannot be blank")
        names = [p.name for p in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("Repeated parameter names are not allowed")
        for p in self.parameters:
            if type(p.value) is int and not -(2**63) <= p.value < 2**63:
                raise ValueError("Integer is outside SQLite's 64-bit range")
            if type(p.value) is float and not math.isfinite(p.value):
                raise ValueError("Nonfinite query values are not allowed")
        return self


class ReviewOutput(Handoff):
    """An independent semantic and security review of the SQL candidate."""
    approved: bool
    feedback: str


class AnswerOutput(Handoff):
    """A plain-language answer supported by the executed result."""
    answer: str = Field(min_length=1)
    reason: str = Field(min_length=1,description="Brief data-based justification: evidence, filters and units; never private chain-of-thought")


class Budget:
    def __init__(self, max_calls=40):
        self.limit, self.calls, self.trace = max_calls, 0, []

    def reserve(self):
        if self.calls == self.limit:
            raise RuntimeError("Model-call budget exhausted")
        self.calls += 1


@dataclass(frozen=True)
class Candidate:
    sql: str
    parameters: tuple

    @classmethod
    def parse(cls, value):
        parsed = SQLSubmission.model_validate(value)
        return cls(parsed.sql, tuple(sorted((p.name, Decimal(p.value.value) if isinstance(p.value,DecimalValue) else p.value) for p in parsed.parameters)))

    def payload(self):
        return {"sql": self.sql, "parameters": [{"name": k, "value": {'type':'decimal','value':format(v,'f')} if isinstance(v,Decimal) else v} for k, v in self.parameters]}


@dataclass(frozen=True)
class Approval:
    token: str = field(repr=False)


class ApprovalRegistry:
    def __init__(self):
        self.__tickets = {}

    @staticmethod
    def fingerprint(candidate, tables):
        payload = {"query": candidate.payload(), "tables": sorted(tables)}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()

    def issue(self, candidate, tables):
        ticket = Approval(secrets.token_hex(24))
        self.__tickets[ticket.token] = self.fingerprint(candidate, tables)
        return ticket

    def consume(self, ticket, candidate, tables):
        signature = self.__tickets.pop(ticket.token, "") if isinstance(ticket, Approval) else ""
        if not signature or not secrets.compare_digest(signature, self.fingerprint(candidate, tables)):
            raise RuntimeError("Approval does not authorize this query, parameters and table scope")
