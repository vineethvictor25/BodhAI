"""Build a conservative, editable pipeline draft from a staged source schema."""
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import store
from .mapping import flatten
from .models import Column, PipelineSpec
from .privacy import sensitive_source_paths


IDENTIFIER_NAME = re.compile(
    r"(?:^|_)(?:id|key|uuid|code|zip|postal|phone|account)(?:_|$)|(?:id|key)$",
    re.IGNORECASE,
)
PROVISIONAL_IDENTIFIER_NAME = re.compile(
    r"^(?:(?:transaction|record|event|row)_)?(?:id|uuid|key)$|"
    r"(?:^|_)(?:transaction|record|event|row)_id(?:_|$)",
    re.IGNORECASE,
)


def _column_name(path: str, index: int, used: set[str]) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")
    if not name or not name[0].isalpha():
        name = f"column_{name}" if name else f"column_{index}"
    name = name[:63].rstrip("_") or f"column_{index}"
    base = name
    suffix = 2
    while name in used:
        ending = f"_{suffix}"
        name = base[: 63 - len(ending)].rstrip("_") + ending
        suffix += 1
    used.add(name)
    return name


def _numeric_values(values: list[object]) -> list[Decimal] | None:
    populated = [value for value in values if value is not None and str(value).strip()]
    if not populated or any(isinstance(value, (dict, list, bool)) for value in populated):
        return None
    numbers = []
    try:
        for value in populated:
            number = Decimal(str(value).strip())
            if not number.is_finite():
                return None
            numbers.append(number)
    except (InvalidOperation, ValueError):
        return None
    return numbers


def _is_unique(values: list[object]) -> bool:
    if any(isinstance(value, (dict, list)) for value in values):
        return False
    keys = [str(value).strip() for value in values]
    return bool(keys) and all(keys) and len(set(keys)) == len(keys)


def suggest_spec(dataset_id: str) -> dict:
    dataset = store.get_dataset(dataset_id)
    rows = dataset["rows"]
    paths = list(dict.fromkeys(path for row in rows for path in flatten(row)))
    if not paths:
        raise ValueError("The uploaded source has no scalar columns to build a pipeline from")
    if len(paths) > 50:
        raise ValueError("Automatic pipeline drafts support at most 50 source columns")

    values_by_path = {
        path: [flatten(row).get(path) for row in rows]
        for path in paths
    }
    protected_paths = sensitive_source_paths(dataset.get("metadata", {}))
    eligible_paths = [path for path in paths if path not in protected_paths]
    candidates = [
        path for path in eligible_paths
        if IDENTIFIER_NAME.search(path) and _is_unique(values_by_path[path])
    ]
    if not candidates:
        candidates = [path for path in eligible_paths if _is_unique(values_by_path[path])]
    if candidates:
        primary_path = candidates[0]
    else:
        provisional_identifiers = [
            path for path in eligible_paths if PROVISIONAL_IDENTIFIER_NAME.search(path)
        ]
        primary_path = provisional_identifiers[0] if provisional_identifiers else paths[0]

    used: set[str] = set()
    columns = []
    for index, path in enumerate(paths, start=1):
        name = _column_name(path, index, used)
        numbers = None if IDENTIFIER_NAME.search(path) else _numeric_values(values_by_path[path])
        if numbers is None:
            dtype = "string"
            transforms = ["trim"]
        else:
            dtype = "integer" if all(number == number.to_integral_value() for number in numbers) else "number"
            transforms = ["number"]
        columns.append(Column(
            name=name,
            dtype=dtype,
            required=path == primary_path,
            aliases=[path],
            transforms=transforms,
        ))

    primary_key = next(column.name for column in columns if column.aliases[0] == primary_path)
    source_stem = Path(dataset["name"]).stem
    target = re.sub(r"[^a-z0-9]+", "_", source_stem.lower()).strip("_")
    if not target or not target[0].isalpha():
        target = f"dataset_{target}" if target else "dataset"
    target = target[:63].rstrip("_") or "dataset"

    spec = PipelineSpec(
        name=target,
        description=f"Auto-generated draft from the staged source {dataset['name']}. Review fields, key, and quality rules before execution.",
        target=target,
        columns=columns,
        primary_key=primary_key,
    )
    warnings = []
    if not candidates:
        warnings.append(
            f"No unique nonempty key was detected; {primary_path} was selected as a provisional primary key. Verify it before running."
        )
    return {
        "dataset_id": dataset_id,
        "source_name": dataset["name"],
        "spec": spec.model_dump(),
        "warnings": warnings,
    }
