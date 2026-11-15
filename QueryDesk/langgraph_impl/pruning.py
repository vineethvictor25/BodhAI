"""Schema context optimization, with deterministic key and actual-read recovery."""
from copy import deepcopy


def column_count(schema):
    return sum(len(t["columns"]) for t in schema["tables"])


def apply_pruning(info, proposal, business, examples):
    result = deepcopy(info)
    full = info["schema"]
    available = {t["table"]: {c["name"] for c in t["columns"]} for t in full["tables"]}
    chosen = {}
    for entry in proposal["tables"]:
        table = entry["table"]
        if table not in available or table in chosen or not set(entry["columns"]) <= available[table]:
            raise ValueError("Column pruning selected a duplicate/unknown table or column")
        chosen[table] = set(entry["columns"])
    if chosen.keys() != available.keys():
        raise ValueError("Column pruning must account for every selected table")
    for table in full["tables"]:
        name = table["table"]
        chosen[name].update(c["name"] for c in table["columns"] if c["primary_key"])
        for link in table["foreign_keys"]:
            chosen[name].add(link["column"])
            if link["references_table"] in chosen and link["references_column"]:
                chosen[link["references_table"]].add(link["references_column"])
    for source in [m["columns"] for m in business["metrics"]] + [e["required_columns"] for e in examples]:
        for table, columns in source.items():
            if table in chosen:
                chosen[table].update(set(columns) & available[table])
    result["full_schema"] = deepcopy(full)
    result["schema"]["tables"] = [{**table, "columns": [c for c in table["columns"] if c["name"] in chosen[table["table"]]]}
                                 for table in full["tables"]]
    result["business_context"] = deepcopy(business)
    result["examples"] = deepcopy(examples)
    result["pruning"] = {"original_columns": column_count(full), "prompt_columns":column_count(result["schema"]),
                         "kept_columns": column_count(result["schema"]),
                         "recovered_columns": {}, "reason": proposal["reason"]}
    return result


def recover_required(info, referenced):
    """Restore any actual projection/filter/group/order/join columns found by SQLite."""
    if "full_schema" not in info:
        return info
    result = deepcopy(info)
    for table in result["schema"]["tables"]:
        name = table["table"]
        kept = {col["name"] for col in table["columns"]}
        required = set(referenced.get(name, []))-kept
        if required:
            original = next(t for t in result["full_schema"]["tables"] if t["table"] == name)
            table["columns"] = [c for c in original["columns"] if c["name"] in kept|required]
            result["pruning"]["recovered_columns"][name] = sorted(required)
    result["pruning"]["kept_columns"] = column_count(result["schema"])
    return result


def prompt_schema(info):
    """Do not accidentally reintroduce the full schema or retrieval internals to prompts."""
    return {"selected_tables":info["selected_tables"],"schema":info["schema"]}


def prompt_examples(info):
    return [{k:e[k] for k in ("id","question","sql","parameters")} for e in info.get("examples",[])]
