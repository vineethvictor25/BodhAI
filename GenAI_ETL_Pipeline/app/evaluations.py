"""Repeatable local regression evaluation; no opaque LLM-as-judge scores."""
import json
import time
import uuid
from pathlib import Path
from . import store
from .guardrails import require_safe,validate_claims
from .knowledge import KnowledgeInput, SearchRequest, AnswerRequest, Claim, index_document,search,answer

ROOT=Path(__file__).resolve().parent.parent


def seed_demo():
    registered=[]
    for path in sorted((ROOT/"samples"/"knowledge").glob("*.md")):
        with store.db() as conn:
            found=conn.execute("SELECT id FROM kb_documents WHERE title=? AND trusted=1",(path.stem,)).fetchone()
        if found: registered.append({"id":found[0],"title":path.stem,"existing":True})
        else: registered.append(index_document(KnowledgeInput(title=path.stem,text=path.read_text(encoding="utf-8"),trusted=True)))
    return registered


def run_evaluation():
    cases=json.loads((ROOT/"samples"/"evaluation_cases.json").read_text())
    retrieval=[]; citations=[]; latencies=[]
    for case in cases["retrieval"]:
        start=time.perf_counter()
        result=search(SearchRequest(query=case["query"],top_k=3))
        latencies.append((time.perf_counter()-start)*1000)
        ranks=[i for i,h in enumerate(result["hits"],1) if h["title"]==case["expected_title"]]
        retrieval.append({"query":case["query"],"expected_title":case["expected_title"],"hit":bool(ranks),"reciprocal_rank":1/min(ranks) if ranks else 0})
        response=answer(AnswerRequest(query=case["query"],top_k=3,mode="extractive"))
        valid=bool(response["claims"]) and all(c["quote"] in next(h["text"] for h in response["retrieval"]["hits"] if h["chunk_id"]==c["chunk_id"]) for c in response["claims"])
        citations.append(valid)
    guard=[]
    for text in cases["injection"]:
        try: require_safe(text); blocked=False
        except ValueError: blocked=True
        guard.append(blocked)
    absent=answer(AnswerRequest(query=cases["unanswerable"],mode="extractive"))["abstain"]
    try:
        validate_claims([Claim(statement="Unsupported fact",chunk_id="invented",quote="fabricated")],[])
        fabricated_blocked=False
    except ValueError: fabricated_blocked=True
    metrics={"recall_at_3":sum(c["hit"] for c in retrieval)/len(retrieval),"mrr_at_3":sum(c["reciprocal_rank"] for c in retrieval)/len(retrieval),"exact_citation_rate":sum(citations)/len(citations),"injection_signal_rejection_rate":sum(guard)/len(guard),"unanswerable_abstention":absent,"fabricated_citation_blocked":fabricated_blocked,"mean_retrieval_ms":sum(latencies)/len(latencies)}
    result={"id":uuid.uuid4().hex,"created_at":store.now(),"metrics":metrics,"cases":retrieval,"passed":metrics["recall_at_3"]>=.8 and metrics["exact_citation_rate"]==1 and all(guard) and absent and fabricated_blocked,"scope":"Small synthetic regression set, extractive answers. These scores do not establish general model safety, semantic entailment or production accuracy."}
    with store.db() as conn:
        conn.execute("INSERT INTO evaluation_runs VALUES(?,?,?)",(result["id"],result["created_at"],json.dumps(result)))
    return result
