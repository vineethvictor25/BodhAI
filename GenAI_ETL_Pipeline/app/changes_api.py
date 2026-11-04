from fastapi import APIRouter,Depends
from starlette.concurrency import run_in_threadpool
from . import store
from .pipeline_changes import CreatePipeline,ChangeRequest,ActivateChange,RollbackVersion,RunVersion,create_pipeline,get_pipeline,get_version,propose_change,get_change,preview_change,activate_change,rollback,run_active


def router(authorize):
    api=APIRouter(prefix="/api",dependencies=[Depends(authorize)])
    @api.get("/pipelines")
    def pipelines():
        with store.db() as conn: return [dict(r) for r in conn.execute("SELECT * FROM managed_pipelines ORDER BY created_at DESC LIMIT 100")]
    @api.post("/pipelines")
    def create(body:CreatePipeline): return create_pipeline(body)
    @api.get("/pipelines/{pipeline_id}")
    def detail(pipeline_id:str): return get_pipeline(pipeline_id)
    @api.get("/pipelines/{pipeline_id}/versions/{revision}")
    def version(pipeline_id:str,revision:int): return get_version(pipeline_id,revision)
    @api.post("/pipelines/{pipeline_id}/changes")
    async def propose(pipeline_id:str,body:ChangeRequest): return await run_in_threadpool(propose_change,pipeline_id,body)
    @api.get("/changes/{change_id}")
    def change(change_id:str): return get_change(change_id)
    @api.post("/changes/{change_id}/preview")
    async def preview(change_id:str): return await run_in_threadpool(preview_change,change_id)
    @api.post("/changes/{change_id}/activate")
    def activate(change_id:str,body:ActivateChange): return activate_change(change_id,body)
    @api.post("/pipelines/{pipeline_id}/rollback")
    def restore(pipeline_id:str,body:RollbackVersion): return rollback(pipeline_id,body)
    @api.post("/pipelines/{pipeline_id}/run")
    async def run(pipeline_id:str,body:RunVersion): return await run_in_threadpool(run_active,pipeline_id,body)
    @api.get("/notifications/status")
    def mail_status():
        from .notifications import status
        return status()
    @api.get("/notifications")
    def notifications():
        import json
        with store.db() as conn:
            rows=conn.execute("SELECT id,created_at,status,attempts,payload_json,last_error,sent_at FROM notification_outbox ORDER BY created_at DESC LIMIT 100").fetchall()
        return [{**dict(row),"payload":json.loads(row["payload_json"])} for row in rows]
    @api.post("/notifications/{notification_id}/retry")
    def retry_notification(notification_id:str):
        from .notifications import retry
        return retry(notification_id)
    return api
