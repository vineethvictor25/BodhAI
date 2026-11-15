"""LangGraph orchestration for the standalone application."""
from functools import wraps
import time
from operator import add
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from .storage import QueryError
from .pii_bounds import PIIBoundError
from .agents import (
    ColumnPruneAgent, ExecutionAgent, IntentAgent, ResponseAgent, SchemaAgent, SQLAgent, ValidationAgent,
)
from .contracts import ApprovalRegistry, Budget, Candidate
from .models import LangChainModel
from .knowledge import KnowledgeBase
from .telemetry import instrument_stage


class GraphState(TypedDict, total=False):
    question: str
    status: str
    answer: str
    reason: str
    completed: str
    next_node: str
    attempts: int
    intent_info: dict
    schema_info: dict | None
    candidate: dict | None
    validation_ok: bool
    rows: dict | None
    feedback: dict | None
    queries: list
    node_path: Annotated[list[str], add]
    result: dict
    routing: dict
    error_info: dict
    budget_calls: int
    pause: str
    manual_sql: bool


class LangGraphWorkflow:
    def table_proposal(self,info,routing):
        available=routing['tables']
        proposal={'tables':info['selected_tables'],'reason':info['reason'],
            'workspaces':routing['workspaces'],'available_tables':available}
        describe=getattr(self.database,'table_options',None)
        options=describe(available) if callable(describe) else None
        if options is not None:
            if len(options)!=len(set(available)) or {entry['id'] for entry in options}!=set(available):
                raise ValueError('Table selection metadata does not match the permitted scope')
            proposal['table_options']=options
        return proposal

    def __init__(self, database, model_factory=None, max_attempts=3, max_model_calls=40, workspaces=None, knowledge=None,domain_hints=False):
        if not 1 <= max_attempts <= 10 or not 1 <= max_model_calls <= 200:
            raise ValueError("Use 1–10 SQL attempts and 1–200 model calls")
        self.database = database
        from .config import ModelConfig
        self.model_factory = model_factory or (lambda role: LangChainModel(config=ModelConfig.for_role(role)))
        self.max_attempts, self.max_model_calls = max_attempts, max_model_calls
        self.workspaces = workspaces
        self.domain_hints=domain_hints
        self.knowledge = knowledge or KnowledgeBase()
        if workspaces:
            self.knowledge.route("",database.allowed,requested=workspaces,soft=domain_hints)

    def build_graph(self, on_event=None, confirm_tables=None,confirm_sql=None,*,review_mode=False,budget_calls=0,approved_manifest=None,model_cache=None):
        """Build a fresh request graph, including its private approval registry and budget.

        No keys, models, connections or approval capabilities are stored in graph state.
        This graph intentionally has no checkpointer: execution approvals cannot be resumed.
        """
        budget, approvals = Budget(self.max_model_calls), ApprovalRegistry()
        if not 0<=budget_calls<=self.max_model_calls:raise ValueError('Invalid resumed model-call budget')
        budget.calls=budget_calls
        pending_approval = [None]
        models = {} if model_cache is None else model_cache

        def model(role):
            if role not in models:
                models[role] = self.model_factory(role)
            return models[role]

        def node(name):
            def decorate(handler):
                @wraps(handler)
                @instrument_stage(name)
                def guarded(state):
                    from .cancellation import check
                    check()
                    access_check=getattr(self.database,'check',None)
                    if callable(access_check):access_check()
                    if on_event:
                        on_event({"stage":name,"status":"running"})
                    try:
                        updates = handler(state)
                    except (RuntimeError, ValueError, OSError) as exc:
                        pending_approval[0] = None
                        updates = {"status": "failed", "answer": str(exc)}
                        if hasattr(exc,"details"):
                            updates["error_info"] = exc.details()
                    if callable(access_check):access_check()
                    if on_event:
                        event={"stage":name,"status":"failed" if updates.get("status")=="failed" else "completed"}
                        if name=="schema" and updates.get("schema_info"):
                            event["tables"]=updates["schema_info"]["selected_tables"]
                        if name=="sql" and updates.get("candidate"):
                            event["sql"]=updates["candidate"]["sql"]
                            event["parameters"]={p["name"]:p["value"] for p in updates["candidate"]["parameters"]}
                        on_event(event)
                    return {**updates, "completed": name, "node_path": [name],"budget_calls":budget.calls}
                return guarded
            return decorate

        def schedule_schema(state):
            pending_approval[0] = None
            if state["attempts"] >= self.max_attempts:
                return {"next_node": "finalize", "status": "failed",
                        "answer": "SQL attempt limit exhausted; no query could be safely completed"}
            routing = state["routing"]
            if state["attempts"] > 0 and not self.workspaces:
                routing = self.knowledge.route(state["question"],self.database.allowed,expand=True,soft=self.domain_hints)
                budget.trace.append({"agent":"workspace","tool":"retry_expansion","result":routing})
            return {"next_node": "schema", "attempts": state["attempts"] + 1, "routing":routing,
                    "schema_info": None, "candidate": None, "validation_ok": False, "rows": None}

        def orchestrator(state):
            if state["status"] != "running":
                updates = {"next_node": "finalize"}
            else:
                completed = state["completed"]
                if completed == "start":
                    updates = {"next_node": "intent"}
                elif completed == "intent":
                    updates = {"next_node": "workspace"}
                elif completed == "workspace":
                    updates = schedule_schema(state)
                elif completed == "schema":
                    updates = {"next_node": "pause_tables" if review_mode else "columns"}
                elif completed == 'tables_review':
                    updates = {'next_node':'columns'}
                elif completed == "columns":
                    updates = {"next_node": "sql"}
                elif completed == "sql":
                    updates = {"next_node": "validation"}
                elif completed == "validation":
                    if state.get('manual_sql') and not state['validation_ok']:
                        updates={'next_node':'finalize','status':'failed','answer':'Edited SQL was rejected: '+str((state.get('feedback') or {}).get('error','Validation failed'))}
                    else:
                        updates = {"next_node": "pause_sql" if review_mode else "execution"} if state["validation_ok"] else schedule_schema(state)
                elif completed == 'sql_review' and review_mode and approved_manifest:
                    updates = {'next_node':'execution'}
                elif completed == "execution":
                    updates = {"next_node": "response"} if state["rows"] is not None else schedule_schema(state)
                else:
                    updates = {"next_node": "finalize", "status": "failed", "answer": "Invalid graph transition"}
            return {**updates, "node_path": ["orchestrator"]}

        @node("intent")
        def intent(state):
            clarification = self.knowledge.clarification(state["question"])
            if clarification:
                return {"intent_info":{"status":"clarification","assumptions":[]},
                        "status":"clarification","answer":clarification}
            result = IntentAgent(model("intent"), budget).understand(state["question"],
                self.knowledge.context(state["question"],self.database.allowed))
            updates = {"intent_info": result}
            if result["status"] != "ready":
                updates.update(status=result["status"], answer=result["message"])
            return updates

        @node("workspace")
        def workspace(state):
            routing = self.knowledge.route(state["question"],self.database.allowed,requested=self.workspaces,soft=self.domain_hints)
            budget.trace.append({"agent":"workspace","tool":"route_domains","result":routing})
            return {"routing":routing}

        @node("schema")
        def schema(state):
            result = SchemaAgent(self.database, model("schema"), budget).select(
                state["question"], state["intent_info"], state["feedback"], state["routing"])
            extra=set(result["selected_tables"])-set(state["routing"].get("preferred_tables",[]))
            if self.domain_hints and extra:
                result["reason"]+=" Additional tables outside the suggested domain: "+", ".join(sorted(extra))+"."
            if confirm_tables:
                selected = confirm_tables(self.table_proposal(result,state['routing']))
                if not selected or not set(selected) <= set(state["routing"]["tables"]) & self.database.allowed:
                    raise ValueError("Confirmed tables must stay within workspace and table permissions")
                result = {"selected_tables":sorted(set(selected)),"schema":self.database.schema(selected),
                    "reason":"User confirmed table selection"}
            return {"schema_info": result}

        @node("columns")
        def columns(state):
            info = state["schema_info"]
            business = self.knowledge.context(state["question"],state["routing"]["tables"])
            examples = self.knowledge.retrieve(state["question"],self.database,info["selected_tables"])
            budget.trace.append({"agent":"retrieval","tool":"approved_sql_examples",
                "result":{"ids":[e["id"] for e in examples]}})
            result = ColumnPruneAgent(model("columns"),budget).prune(state["question"],state["intent_info"],info,business,examples)
            return {"schema_info":result}

        @node("sql")
        def sql(state):
            result = SQLAgent(model("sql"), budget).generate(
                state["question"], state["intent_info"], state["schema_info"], state["feedback"])
            return {"candidate": result.payload()}

        @node("validation")
        def validation(state):
            candidate = Candidate.parse(state["candidate"])
            result = ValidationAgent(self.database, model("validation"), budget, approvals).validate(
                state["question"], state["intent_info"], state["schema_info"], candidate)
            pending_approval[0] = result.approval
            return {"validation_ok": result.approval is not None,
                    "schema_info": result.schema or state["schema_info"],
                    "feedback": None if result.approval else {
                        "stage": "validation", "candidate": candidate.payload(), "error": result.feedback}}

        @node("execution")
        def execution(state):
            approval, pending_approval[0] = pending_approval[0], None
            candidate = Candidate.parse(state["candidate"])
            try:
                if review_mode:
                    from .planning import manifest
                    current=manifest(self.database,candidate,state['schema_info']['selected_tables'])
                    if not approved_manifest or current['fingerprint']!=approved_manifest['fingerprint']:
                        raise RuntimeError('The execution plan changed after approval. Run the question again to review it.')
                    if time.time()>=approved_manifest.get('deadline',float('inf')):
                        raise RuntimeError('SQL approval expired before execution. Run the question again for fresh review.')
                    # Never serialize execution capabilities. Reissue only inside the approved segment.
                    approval=approvals.issue(candidate,state['schema_info']['selected_tables'])
                if confirm_sql:
                    from .planning import manifest
                    proposal=manifest(self.database,candidate,state['schema_info']['selected_tables'])
                    if confirm_sql(proposal) is not True:
                        raise RuntimeError('Query cancelled before database reads.')
                    current=manifest(self.database,candidate,state['schema_info']['selected_tables'])
                    if current['fingerprint']!=proposal['fingerprint']:
                        raise RuntimeError('The execution plan changed after approval. Run the question again to review it.')
                result = ExecutionAgent(self.database, budget, approvals).execute(
                    candidate, state["schema_info"]["selected_tables"], approval)
            except PIIBoundError:
                raise
            except QueryError as exc:
                if state.get('manual_sql'):raise RuntimeError('Edited SQL execution failed. Review a fresh query before trying again.') from None
                return {"rows": None, "validation_ok": False, "feedback": {
                    "stage": "execution", "candidate": candidate.payload(), "error": str(exc)}}
            return {"rows": result, "feedback": None, "validation_ok": False,
                    "queries": state["queries"] + [{"sql": result.get("executed_sql",candidate.sql),
                        "parameters": result.get('executed_parameters',dict(candidate.parameters)), "result": result}]}

        @node("response")
        def response(state):
            rows,restore=(self.database.model_rows(state["rows"]) if hasattr(self.database,"model_rows") else (state["rows"],lambda value:value))
            explanation = ResponseAgent(model("response"), budget).explain(
                state["question"], state["intent_info"], Candidate.parse(state["candidate"]), rows,
                state["schema_info"].get("business_context",{}))
            answer=restore(explanation["answer"])
            access_note=state['rows'].get('access_note')
            if access_note:
                answer+='\n\nAccess scope: '+access_note
            if state["rows"]["truncated"]:
                answer += "\n\nResult limit: returned data is partial or contains clipped values; do not treat it as the complete result."
            return {"status": "answered", "answer": answer,"reason":restore(explanation["reason"])+(('\n\n'+access_note) if access_note else '')}

        def finalize(state):
            result = {"architecture": "langgraph", "status": state["status"], "answer": state["answer"],
                      "reason":state.get("reason",""),
                      "error_info":state.get("error_info",{}),
                      "tables":(state.get("schema_info") or {}).get("selected_tables",[]),
                      "assumptions": state.get("intent_info", {}).get("assumptions", []),
                      "attempts": state["attempts"], "model_calls": budget.calls,
                      "queries": state["queries"], "trace": list(budget.trace),
                      "workspaces":state.get("routing",{}).get("workspaces",[]),
                      "example_ids":[e["id"] for e in (state.get("schema_info") or {}).get("examples",[])],
                      "pruning":(state.get("schema_info") or {}).get("pruning",{}),
                      "node_path": state["node_path"] + ["finalize"]}
            return {"result": result, "node_path": ["finalize"]}

        builder = StateGraph(GraphState)
        builder.add_node('pause_tables',lambda state:{'pause':'tables'})
        builder.add_node('pause_sql',lambda state:{'pause':'sql'})
        builder.add_edge('pause_tables',END)
        builder.add_edge('pause_sql',END)
        builder.add_node("orchestrator", orchestrator)
        for name, handler in {"intent": intent, "workspace":workspace,"schema": schema, "columns":columns,"sql": sql, "validation": validation,
                              "execution": execution, "response": response}.items():
            builder.add_node(name, handler)
            builder.add_edge(name, "orchestrator")
        builder.add_node("finalize", finalize)
        builder.add_edge(START, "orchestrator")
        builder.add_conditional_edges("orchestrator", lambda state: state["next_node"],
            {name: name for name in ("intent", "workspace", "schema", "columns", "sql", "validation", "execution", "response", "finalize",'pause_tables','pause_sql')})
        builder.add_edge("finalize", END)
        return builder.compile()

    def ask(self, question, on_event=None, confirm_tables=None,confirm_sql=None):
        from .model_policy import guard_tracing
        guard_tracing()
        if not isinstance(question, str) or not question.strip() or len(question) > 8000:
            raise ValueError("Question must contain 1–8,000 characters")
        graph = self.build_graph(on_event=on_event,confirm_tables=confirm_tables,confirm_sql=confirm_sql)
        initial = {"question": question, "status": "running", "answer": "", "completed": "start",
                   "attempts": 0, "feedback": None, "queries": [], "node_path": [],
                   "schema_info": None, "candidate": None, "rows": None, "validation_ok": False}
        final = graph.invoke(initial, config={"recursion_limit": 16 * self.max_attempts + 24})
        return final["result"]

    def recover_review(self,question,checkpoint):
        """Validate a durable review artifact without model requests or business-row reads."""
        from copy import deepcopy
        from .planning import manifest
        if type(checkpoint.get('version')) is not int or checkpoint['version']!=1 or checkpoint.get('limits')!={'attempts':self.max_attempts,'model_calls':self.max_model_calls}:
            raise ValueError('Checkpoint format or workflow limits changed; run the question again')
        state=deepcopy(checkpoint['state'])
        kind=checkpoint['kind']
        if not isinstance(state,dict) or set(state)-set(GraphState.__annotations__):raise ValueError('Unknown checkpoint state fields')
        if kind not in {'tables','sql'} or state.get('pause')!=kind or state.get('status')!='running' or state.get('question')!=question or state.get('rows') is not None or type(state.get('queries')) is not list or state['queries'] or state.get('result') is not None:
            raise ValueError('Invalid recovery checkpoint')
        from .contracts import IntentOutput
        if IntentOutput.model_validate(state.get('intent_info')).status!='ready':raise ValueError('Invalid saved intent')
        if not isinstance(state.get('node_path'),list) or any(not isinstance(node,str) for node in state['node_path']) or not isinstance(state.get('schema_info'),dict) or not isinstance(state.get('routing'),dict):
            raise ValueError('Incomplete checkpoint state')
        for values in (state['schema_info'].get('selected_tables'),state['routing'].get('tables'),state['routing'].get('workspaces')):
            if not isinstance(values,list) or not values or any(not isinstance(value,str) for value in values):raise ValueError('Invalid saved table/domain scope')
        if type(state.get('attempts')) is not int or not 1<=state['attempts']<=self.max_attempts or type(state.get('budget_calls')) is not int or not 0<=state['budget_calls']<=self.max_model_calls:
            raise ValueError('Invalid saved workflow budget')
        if not set(state['routing']['tables'])<=self.database.allowed:
            raise ValueError('Saved table permissions changed')
        tables=checkpoint.get('selection') or state['schema_info']['selected_tables']
        schema=self.database.schema(tables)
        if kind=='tables':
            if state.get('completed')!='schema':raise ValueError('Invalid table checkpoint')
            state['schema_info']={'selected_tables':tables,'schema':schema,'reason':'Restored table proposal; confirm the current tables.'}
            proposal=self.table_proposal(state['schema_info'],state['routing'])
        else:
            if state.get('completed')!='validation' or state.get('validation_ok') is not True:raise ValueError('Invalid SQL checkpoint')
            proposal=manifest(self.database,Candidate.parse(state['candidate']),tables)
            if proposal['fingerprint']!=checkpoint['proposal']['fingerprint']:
                raise ValueError('The saved execution plan changed; run the question again')
        return {'state':state,'proposal':proposal,'kind':kind}

    def advance(self,question,*,state=None,decision=None,on_event=None):
        """Run until review or completion, retaining only plain artifacts between segments."""
        from copy import deepcopy
        from .model_policy import guard_tracing
        from .planning import manifest
        guard_tracing()
        if not isinstance(question,str) or not question.strip() or len(question)>8000:
            raise ValueError('Question must contain 1–8,000 characters')
        approved=None
        if state is None:
            if decision is not None:raise ValueError('No review is pending')
            initial={'question':question,'status':'running','answer':'','completed':'start','attempts':0,
                'feedback':None,'queries':[],'node_path':[],'schema_info':None,'candidate':None,'rows':None,
                'validation_ok':False,'budget_calls':0,'pause':''}
        else:
            initial=deepcopy(state)
            if initial['question']!=question or initial.get('rows') is not None or initial.get('queries'):
                raise ValueError('Invalid review continuation')
            if initial.get('pause')=='tables' and decision and decision.get('kind')=='tables':
                selected=sorted(set(decision['tables']))
                if not selected or not set(selected)<=set(initial['routing']['tables'])&self.database.allowed:
                    raise ValueError('Confirmed tables must stay within current permissions')
                initial.update(completed='tables_review',schema_info={'selected_tables':selected,
                    'schema':self.database.schema(selected),'reason':'User confirmed table selection'})
            elif initial.get('pause')=='sql' and decision and decision.get('kind')=='sql':
                approved=decision['proposal']
                initial['completed']='sql_review'
            elif initial.get('pause')=='sql' and decision and decision.get('kind')=='edit_sql':
                candidate=Candidate.parse(decision['candidate'])
                initial.update(candidate=candidate.payload(),completed='sql',validation_ok=False,feedback=None,manual_sql=True)
            else:raise ValueError('Invalid review decision')
            initial['pause']=''
        models={}
        try:
            graph=self.build_graph(on_event=on_event,review_mode=True,budget_calls=initial['budget_calls'],
                approved_manifest=approved,model_cache=models)
            final=graph.invoke(initial,config={'recursion_limit':16*self.max_attempts+24})
            if final.get('pause')=='tables':
                info=final['schema_info']
                proposal=self.table_proposal(info,final['routing'])
            elif final.get('pause')=='sql':
                proposal=manifest(self.database,Candidate.parse(final['candidate']),final['schema_info']['selected_tables'])
            else:return {'result':final['result']}
            return {'waiting':final['pause'],'state':final,'proposal':proposal}
        finally:
            for model in models.values():
                close=getattr(model,'close',None)
                if callable(close):close()
