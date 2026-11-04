import re
from difflib import SequenceMatcher
from typing import Any
from .models import PipelineSpec


def flatten(record: dict, prefix="") -> dict[str, Any]:
    out = {}
    for key, value in record.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(flatten(value, path))
        else:
            out[path] = value
    return out


def canonical(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())


def infer_mapping(rows: list[dict], spec: PipelineSpec):
    # Inspect all bounded rows so sparse fields do not disappear from inference.
    keys = sorted({key for row in rows for key in flatten(row)})
    result = []
    used = set()
    reserved = set(spec.mapping_overrides.values())
    for column in spec.columns:
        if column.name in spec.mapping_overrides:
            source = spec.mapping_overrides[column.name]
            if source not in keys or source in used:
                raise ValueError(f"Invalid or reused mapping override: {column.name} -> {source}")
            result.append(dict(target=column.name, source=source, confidence=1.0, reason="explicit override", candidates=[]))
            used.add(source)
            continue
        aliases = [canonical(x) for x in [column.name, *column.aliases]]
        ranked = sorted([(max(1.0 if canonical(key) == alias else SequenceMatcher(None, canonical(key), alias).ratio() * 0.93 for alias in aliases), key) for key in keys if key not in used and key not in reserved], reverse=True)
        score, source = ranked[0] if ranked else (0.0, None)
        ambiguous = len(ranked) > 1 and score - ranked[1][0] < 0.08
        accepted = score >= spec.mapping_threshold and not ambiguous
        result.append(dict(target=column.name, source=source if accepted else None, confidence=round(score, 3), reason="alias/name match" if accepted else "ambiguous; provide override" if ambiguous else "below confidence threshold", candidates=[{"source": key, "score": round(s, 3)} for s, key in ranked[:3]]))
        if accepted:
            used.add(source)
    return result
