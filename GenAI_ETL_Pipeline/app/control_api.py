from fastapi import APIRouter,Depends
from starlette.concurrency import run_in_threadpool
from . import store
from .knowledge import KnowledgeInput,SearchRequest,AnswerRequest,index_document,search,answer
from .evaluations import seed_demo,run_evaluation


def router(authorize):
    api=APIRouter(prefix="/api",dependencies=[Depends(authorize)])
    @api.get("/knowledge")
    def list_knowledge():
        with store.db() as conn:
            return [dict(r) for r in conn.execute("SELECT d.*,count(c.id) AS chunks FROM kb_documents d LEFT JOIN kb_chunks c ON c.document_id=d.id GROUP BY d.id ORDER BY d.created_at DESC LIMIT 100")]
    @api.post("/knowledge")
    async def index(body:KnowledgeInput): return await run_in_threadpool(index_document,body)
    @api.post("/knowledge/search")
    async def retrieve(body:SearchRequest): return await run_in_threadpool(search,body)
    @api.post("/knowledge/answer")
    async def grounded_answer(body:AnswerRequest): return await run_in_threadpool(answer,body)
    @api.post("/knowledge/demo")
    async def demo_knowledge(): return await run_in_threadpool(seed_demo)
    @api.post("/evaluations")
    async def evaluate(): return await run_in_threadpool(run_evaluation)
    @api.get("/evaluations")
    def evaluations():
        import json
        with store.db() as conn:
            return [json.loads(r[0]) for r in conn.execute("SELECT result_json FROM evaluation_runs ORDER BY created_at DESC LIMIT 20")]
    return api
