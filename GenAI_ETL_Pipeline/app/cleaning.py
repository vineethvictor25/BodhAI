"""Deterministic unit/format transforms with record-level provenance."""
import math
import re
from datetime import date,datetime,timezone
import hashlib
import uuid
from .models import Column


def number(value):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite numeric value")
    return result


def currency_number(value):
    if isinstance(value, (int, float)):
        return number(value)
    # Contract deliberately supports US grouping; ambiguous locale formats are rejected.
    text = re.sub(r"^(?:USD|EUR|INR|GBP|[$€£₹])\s*", "", str(value).strip(), flags=re.I)
    if not re.fullmatch(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", text):
        raise ValueError("Unsupported currency number format")
    return number(text.replace(",", ""))


def length_m(value):
    if isinstance(value, (int, float)):
        raise ValueError("Measurement requires an explicit unit")
    match = re.fullmatch(r"\s*(?:length\s*=\s*)?([+-]?\d+(?:\.\d+)?)\s*(mm|cm|m|in|ft)\s*", str(value), re.I)
    if not match:
        raise ValueError("Expected length with mm, cm, m, in, or ft")
    return number(match[1]) * {"mm": .001, "cm": .01, "m": 1, "in": .0254, "ft": .3048}[match[2].lower()]


def iso_timestamp(value):
    stamp=datetime.fromisoformat(str(value).strip().replace('Z','+00:00'))
    if stamp.tzinfo is None:raise ValueError('Timestamp needs explicit timezone')
    return stamp.astimezone(timezone.utc).isoformat()


def boolean_number(value):
    key=str(value).strip().lower()
    if key not in ('y','n','yes','no','true','false','0','1'):raise ValueError('Expected Y/N, yes/no, true/false or 1/0')
    return int(key in ('y','yes','true','1'))


TRANSFORMS = {"trim": lambda v: str(v).strip(), "lower": lambda v: str(v).lower(), "upper": lambda v: str(v).upper(), "number": number, "currency_number": currency_number, "length_m": length_m, 'iso_date':lambda v:date.fromisoformat(str(v).strip()).isoformat(), 'iso_timestamp':iso_timestamp, 'collapse_whitespace':lambda v:' '.join(str(v).split()), 'strip_controls':lambda v:''.join(c for c in str(v) if c.isprintable()), 'boolean_number':boolean_number, 'round_2':lambda v:round(number(v),2), 'sha256':lambda v:hashlib.sha256(str(v).encode()).hexdigest()}


def clean_value(value, column: Column):
    changes = []
    missing = value is None or (isinstance(value, str) and not value.strip())
    if missing:
        if column.default is None:
            if column.required:
                raise ValueError("Required value is missing")
            return None, changes
        value = column.default
        changes.append({"rule": "contract_default", "value": value})
    for transform in column.transforms:
        previous = value
        value = TRANSFORMS[transform](value)
        if previous != value or type(previous) is not type(value):
            changes.append({"rule": transform, "before": previous, "after": value})
    if column.value_map:
        if str(value) not in column.value_map and column.unmapped=='reject':raise ValueError('Value missing from reference mapping')
        previous=value;value=column.value_map.get(str(value),value)
        if previous!=value:changes.append({'rule':'reference_lookup','before':previous,'after':value})
    if column.dtype in ("number", "integer"):
        value = number(value)
        if column.dtype == "integer":
            if not value.is_integer():
                raise ValueError("Expected an integer without rounding")
            value = int(value)
        if column.minimum is not None and value < column.minimum:
            raise ValueError("Value below semantic minimum")
        if column.maximum is not None and value > column.maximum:
            raise ValueError("Value above semantic maximum")
    else:
        if isinstance(value, (list, dict)):
            raise ValueError("Nested collections cannot be cast to scalar text")
        value = str(value)
    if column.allowed and str(value) not in column.allowed:
        raise ValueError("Value is outside the contract's allowed set")
    if column.format=='email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',str(value)):raise ValueError('Invalid email format')
    if column.format=='iso_date':date.fromisoformat(str(value))
    if column.format=='uuid':uuid.UUID(str(value))
    from .privacy import protect_output
    value=protect_output(value,column)
    if column.protection!='none':changes=[{'rule':edit['rule'],'protected':True} for edit in changes]
    return value, changes
