"""Local business context, domain routing and permission-filtered lexical retrieval."""
import json
import math
import re
from collections import Counter
from pathlib import Path
from copy import deepcopy

DATA_DIR = Path(__file__).resolve().parent / "knowledge"
STOP = {"the", "a", "an", "of", "in", "for", "to", "and", "is", "are", "what", "show", "all", "by", "with", "each"}


def tokens(text):
    return [word[:-1] if word.endswith("s") and len(word)>3 else word
            for word in re.findall(r"[a-z0-9_]+", text.lower()) if word not in STOP]


def rank(query, documents):
    """Small BM25 index: deterministic retrieval, no embeddings or external service."""
    terms = set(tokens(query))
    bags = [Counter(tokens(document)) for document in documents]
    average = sum(sum(b.values()) for b in bags) / max(len(bags),1) or 1
    result = []
    for bag in bags:
        score = 0.0
        for word in terms:
            frequency = bag[word]
            if frequency:
                df = sum(word in b for b in bags)
                idf = math.log(1 + (len(bags)-df+0.5)/(df+0.5))
                score += idf * frequency * 2.2 / (frequency + 1.2*(0.25+0.75*sum(bag.values())/average))
        result.append(score)
    return result


class KnowledgeBase:
    def with_domains(self,domains,allowed):
        result=deepcopy(self)
        for domain in domains:
            permitted=sorted(set(domain["tables"]) & set(allowed))
            if permitted:
                result.catalog["workspaces"][domain["id"]]={"name":domain["name"],"description":domain["description"],
                    "tables":permitted,"keywords":domain["keywords"],"revision":domain["revision"]}
        return result

    @classmethod
    def generic(cls,tables):
        """External sources never inherit sample-retail units, metrics or SQL examples."""
        instance=cls.__new__(cls)
        instance.directory=None
        instance.examples=[]
        instance.catalog={"workspaces":{"database":{"description":"Permitted tables in this data source",
            "tables":sorted(tables),"keywords":[]}},"metrics":[],"relationship_rules":[],"ambiguous_terms":{},
            "policies":["Use actual metadata and supplied business definitions. Do not assume units or currency scaling. Ask for missing business definitions."]}
        return instance

    def __init__(self, directory=DATA_DIR):
        self.directory = Path(directory)
        self.catalog = json.loads((self.directory / "catalog.json").read_text(encoding="utf-8"))
        self.examples = json.loads((self.directory / "examples.json").read_text(encoding="utf-8"))

    def route(self, question, allowed, requested=None, expand=False, soft=False):
        spaces = self.catalog["workspaces"]
        if requested:
            unknown = set(requested)-spaces.keys()
            if unknown:
                raise ValueError("Unknown workspace: " + ", ".join(sorted(unknown)))
            selected = list(dict.fromkeys(requested))
            reason = "explicit workspace selection"
        else:
            words = set(tokens(question))
            selected = [name for name, space in spaces.items()
                        if words.intersection(tokens(" ".join(space["keywords"])+" "+space.get("name",name)+" "+space["description"]))]
            if not selected or expand:
                selected = list(spaces)
                reason = "retry expansion" if expand else "no confident domain match; permitted-domain fallback"
            else:
                reason = "matched business-domain terms"
        scope = set().union(*(set(spaces[name]["tables"]) for name in selected)) & set(allowed)
        if not scope:
            raise ValueError("Selected workspaces have no tables within application permissions")
        return {"workspaces": selected, "tables": sorted(allowed if soft else scope),"preferred_tables":sorted(scope),
            "domain_hints":soft,"domain_descriptions":[{"id":name,"description":spaces[name]["description"]} for name in selected],
            "reason": reason, "explicit": bool(requested), "business_context":self.context(question,allowed)}

    def context(self, question, tables=None):
        lower = question.lower()
        metrics = [metric for metric in self.catalog["metrics"]
                   if any(alias.lower() in lower for alias in metric["aliases"])]
        if tables is not None:
            permitted = set(tables)
            metrics = [metric for metric in metrics if set(metric["columns"]) <= permitted]
        all_tables=set().union(*(set(s["tables"]) for s in self.catalog["workspaces"].values()))
        rules=[rule for rule in self.catalog["relationship_rules"] if tables is None or
               {t for t in all_tables if re.search(r"\b"+t+r"\b",rule)} <= set(tables)]
        return {"policies": self.catalog["policies"], "metrics": metrics,
                "domains":[{"name":space.get("name",name),"description":space["description"]} for name,space in self.catalog["workspaces"].items()
                    if tables is None or set(space["tables"]) & set(tables)],
                "relationship_rules": rules,
                "ambiguities": self.catalog["ambiguous_terms"],
                "domain_knowledge":[item for item in self.catalog.get("domain_knowledge",[])
                    if (tables is None or set(item["columns"])<=set(tables)) and
                    (item["kind"] in {"rule","relationship"} or any(alias.lower() in lower for alias in item["aliases"]+[item["title"]]))],
                "knowledge_versions":[item for item in self.catalog.get("knowledge_versions",[])
                    if tables is None or set(item["columns"])<=set(tables)]}

    def clarification(self, question):
        # An alias shared by independently published metrics is a decision, not a guess.
        matches={}
        for metric in self.catalog["metrics"]:
            for alias in set(a.lower() for a in metric["aliases"]):
                if re.search(r"\b"+re.escape(alias)+r"\b",question,re.I):
                    matches.setdefault(alias,[]).append(metric)
        for metrics in matches.values():
            unique={m["id"]:m for m in metrics}
            if len(unique)>1 and any(key.startswith("domain:") for key in unique):
                named=[m for m in unique.values() if m["name"].lower() in question.lower()]
                if len(named)!=1:
                    return "Which business definition do you mean: "+"; ".join(m["name"] for m in list(unique.values())[:5])+"? Use the intended definition's full name."
        for term, message in self.catalog["ambiguous_terms"].items():
            if re.search(r"\b"+re.escape(term)+r"\b",question,re.I):
                if term == "profit" or not self.context(question)["metrics"]:
                    return message
        return None

    def retrieve(self, question, database, selected_tables, limit=3):
        scope = set(selected_tables)
        candidates = []
        for example in self.examples:
            if not set(example["tables"]) <= scope:
                continue
            try:
                plan = database.plan(example["sql"], example["parameters"], scope)
            except ValueError:
                continue  # Stale examples never enter the prompt.
            candidates.append({**example, "required_columns": plan["referenced_columns"]})
        scores = rank(question, [e["question"]+" "+" ".join(e["tags"]) for e in candidates])
        ordered = sorted(zip(scores,candidates),key=lambda pair:(-pair[0],pair[1]["id"]))
        return [{**example, "retrieval_score": round(score,4)} for score,example in ordered[:limit] if score>0]
