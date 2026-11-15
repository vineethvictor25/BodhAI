import { useState } from 'react'
import { request, displayValue, type QueryJob } from './api'
import TablePicker from './TablePicker'

export function RestoreReview({job,onRestored,onRetry,disabled}:{job:QueryJob;onRestored?:(job:QueryJob)=>void;onRetry?:()=>void;disabled:boolean}){
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  async function restore(){
    setBusy(true);setError('')
    try{const restored=await request<QueryJob>(`/api/queries/${job.id}/restore-review`,{method:'POST',body:'{}'});onRestored?.(restored)}
    catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  return <section className="table-confirmation" aria-label="Restore saved review">
    <h3>Saved progress is available</h3>
    <p>The server restarted. Restore the saved review without rewriting your question. Current permissions and the saved plan will be checked.</p>
    <p>Restoring progress does not run SQL. Review and approve again before any data is read.</p>
    <button className="text-button" disabled={busy||disabled} onClick={()=>void restore()}>Restore review</button>
    {onRetry&&<button className="text-button" disabled={busy||disabled} onClick={onRetry}>Run question again</button>}
    {error&&<p role="alert">{error}</p>}
  </section>
}

export function SQLApproval({job}:{job:QueryJob}){
  const proposal=job.sql_approval!
  const [busy,setBusy]=useState(false)
  const [sent,setSent]=useState(false)
  const [error,setError]=useState('')
  const [editing,setEditing]=useState(false)
  async function decide(decision:'approve'|'cancel'){
    setBusy(true);setError('')
    try{
      await request(`/api/queries/${job.id}/approve-sql`,{method:'POST',body:JSON.stringify({version:proposal.version,fingerprint:proposal.fingerprint,decision})})
      setSent(true)
    }catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  return <section className="table-confirmation" aria-label="Approve SQL before data reads">
    <h3>Review SQL before reading data</h3>
    <p>The query has not run. Review its tables, filters and bound values.</p>
    <p>Other users can continue querying while you review. Approve here to continue; no server restart is needed.</p>
    <pre><code>{proposal.sql}</code></pre>
    {Object.entries(proposal.parameters).map(([name,value])=><p key={name}><code>{name} = {displayValue(value)}</code></p>)}
    <p>Tables: {proposal.tables.join(', ')}. Result limit: {proposal.row_limit} rows. Query time budget: {proposal.execution_timeout_seconds} seconds.</p>
    <p>{proposal.read_scope}</p><p>{proposal.checks}</p>
    {proposal.adapter_capabilities?.tls&&<p>Connection policy: {proposal.adapter_capabilities.tls.encryption_required?'encrypted connections required':'unencrypted development connections permitted'}. {proposal.adapter_capabilities.tls.tls12_enforced?'TLS 1.2 or newer is required.':'Driver TLS defaults apply when encryption is used.'}</p>}
    {proposal.resource_limits&&<p>Warehouse limits: maximum {proposal.resource_limits.maximum_bytes_billed.toLocaleString()} billed bytes; location {proposal.resource_limits.location}.</p>}
    {proposal.execution_mode&&<p>Read method: {proposal.execution_mode==='direct'?'Direct protected read':proposal.execution_mode==='materialized'?'Bounded local PII processing':'Operator adapter'}.</p>}
    {proposal.pii_limits&&<div><h4>Local PII limits</h4><p>Preparation: {proposal.pii_limits.max_rows.toLocaleString()} rows, {proposal.pii_limits.max_copy_bytes.toLocaleString()} plaintext bytes, {proposal.pii_limits.max_input_bytes.toLocaleString()} input bytes and {proposal.pii_limits.timeout_seconds} seconds.</p><p>Each encrypted envelope: {proposal.pii_limits.max_envelope_bytes.toLocaleString()} bytes. Each plaintext cell: {proposal.pii_limits.max_cell_bytes.toLocaleString()} bytes. PII cells processed: {proposal.pii_limits.max_decrypt_cells.toLocaleString()}. Exceeding a limit stops this attempt without a partial result.</p></div>}
    {proposal.copy_scope&&<div><h4>Source copy scope</h4><p>Only these columns and matching permitted rows are copied. The complete user query is still evaluated locally.</p>{proposal.copy_scope.map(step=><details key={step.table}><summary>{step.table} · {step.row_marker_only?'row markers':step.columns.join(', ')}</summary><pre><code>{step.source_sql}</code></pre>{Object.entries(step.parameters).map(([key,value])=><p key={key}><code>{key} = {displayValue(value)}</code></p>)}</details>)}</div>}
    {proposal.row_security?.enabled&&<div><h4>Row-level access</h4><p>{proposal.row_security.summary}</p>{proposal.row_security.filters.map(filter=><details key={filter.table}><summary>{filter.table} · {filter.mode}</summary><pre><code>{filter.sql}</code></pre>{Object.entries(filter.parameters).map(([key,value])=><p key={key}><code>{key} = {displayValue(value)}</code></p>)}</details>)}</div>}
    <p>Approval expires {new Date(proposal.deadline*1000).toLocaleString()}.</p>
    {editing&&<SQLEditor job={job} onSent={()=>setSent(true)} onBusyChange={setBusy}/>}
    <div className="review-actions"><button disabled={busy||sent||editing} onClick={()=>void decide('approve')}>Approve SQL and run</button><button disabled={busy||sent} onClick={()=>setEditing(value=>!value)}>{editing?'Close SQL editor':'Edit SQL'}</button><button disabled={busy||sent} onClick={()=>void decide('cancel')}>Cancel without reading data</button></div>
    {sent&&<p role="status">Your SQL decision was received.</p>}{error&&<p role="alert">{error}</p>}
  </section>
}

export function SQLEditor({job,onSent,onBusyChange}:{job:QueryJob;onSent:()=>void;onBusyChange?:(busy:boolean)=>void}){
  const proposal=job.sql_approval!
  const [sql,setSql]=useState(proposal.sql)
  const [parameters,setParameters]=useState(()=>JSON.stringify(Object.entries(proposal.parameters).map(([name,value])=>({name,value:typeof value==='object'&&value&&'$qd_type' in value&&value.$qd_type==='decimal'?{type:'decimal',value:(value as Record<string,unknown>).value}:value})),null,2))
  const [busy,setBusy]=useState(false)
  const [sent,setSent]=useState(false)
  const [error,setError]=useState('')
  async function revise(){
    setBusy(true);onBusyChange?.(true);setError('')
    try{
      const parsed=JSON.parse(parameters)
      if(!Array.isArray(parsed))throw new Error('Bound values must be a JSON list of name and value objects.')
      if(parsed.some(item=>typeof item?.value==='number'&&Number.isInteger(item.value)&&!Number.isSafeInteger(item.value)))throw new Error('An integer exceeds browser precision. Use an exact typed decimal string or regenerate the query; no SQL was submitted.')
      await request(`/api/queries/${job.id}/revise-sql`,{method:'POST',body:JSON.stringify({version:proposal.version,fingerprint:proposal.fingerprint,sql,parameters:parsed})})
      setSent(true);onSent()
    }catch(e){setError((e as Error).message)}finally{setBusy(false);onBusyChange?.(false)}
  }
  return <div className="query-editor"><p>Edits invalidate the previous approval. Validation and a fresh SQL approval are required before execution. Table and access restrictions still apply.</p><label>SQL to validate<textarea rows={8} value={sql} onChange={event=>setSql(event.target.value)} disabled={busy||sent}/></label><label>Bound values (JSON list)<textarea rows={4} value={parameters} onChange={event=>setParameters(event.target.value)} disabled={busy||sent}/></label><button disabled={busy||sent||!sql.trim()} onClick={()=>void revise()}>Validate edited SQL</button>{error&&<p role="alert">{error}</p>}</div>
}

export function QueryActions({job,onUpdated,onEdit}:{job:QueryJob;onUpdated?:(job:QueryJob)=>void;onEdit?:()=>void}){
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const active=['queued','running','awaiting_confirmation','awaiting_sql_approval'].includes(job.status)
  async function stop(edit=false){
    setBusy(true);setError('')
    try{
      if(active){const next=await request<QueryJob>(`/api/queries/${job.id}/cancel`,{method:'POST',body:'{}'});onUpdated?.(next)}
      if(edit)onEdit?.()
    }catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  return <div className="query-actions">{active&&<button className="text-button" disabled={busy||job.cancellation_requested} onClick={()=>void stop()}>Stop query</button>}{onEdit&&<button className="text-button" disabled={busy||job.cancellation_requested} onClick={()=>void stop(true)}>{active?'Stop and edit question':'Edit question'}</button>}{job.cancellation_requested&&active&&<p role="status">Cancellation requested. Waiting for the active call to return; late results will be discarded. Warehouse cancellation is best effort.</p>}{error&&<p role="alert">{error}</p>}</div>
}

export function TableConfirmation({ job }: { job: QueryJob }) {
  const proposal = job.confirmation!
  const [selected, setSelected] = useState(proposal.tables)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [sent, setSent] = useState(false)
  async function decide(decision: 'confirm' | 'cancel') {
    setBusy(true); setError('')
    try {
      await request(`/api/queries/${job.id}/confirm`, { method: 'POST', body: JSON.stringify({ version: proposal.version, decision, tables: selected }) })
      setSent(true)
    } catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  return <section className="table-confirmation" aria-label="Confirm source tables">
    <h3>Review the tables before we write SQL</h3>
    <p>Other users can continue querying while you review. Confirm here to continue; no server restart is needed.</p>
    <p>{proposal.reason}</p>
    <p>Suggested domain: {proposal.workspaces.join(', ')}. This is a relevance hint. Add or remove permitted source tables below. You have {Math.round(proposal.timeout_seconds / 60)} minutes to confirm.</p>
    <fieldset disabled={busy || sent}><legend>Permitted source tables</legend><TablePicker available={proposal.available_tables} options={proposal.table_options} selected={selected} onChange={setSelected}/></fieldset>
    <div className="review-actions"><button disabled={busy || sent || !selected.length} onClick={() => void decide('confirm')}>Confirm tables and continue</button><button disabled={busy || sent} onClick={() => void decide('cancel')}>Cancel query</button></div>
    {sent && <p role="status">Your decision was received.</p>}{error && <p role="alert">{error}</p>}
  </section>
}

export function Feedback({ job }: { job: QueryJob }) {
  const [open, setOpen] = useState(false)
  const [rating, setRating] = useState('helpful')
  const [category, setCategory] = useState('answer')
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  async function save() {
    setBusy(true); setError('')
    try {
      await request(`/api/queries/${job.id}/feedback`, { method: 'POST', body: JSON.stringify({ rating, category, comment }) })
      setSaved(true)
    } catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  if (saved) return <p className="feedback-saved" role="status">Feedback saved for review. It will not change approved examples automatically.</p>
  return <section className="feedback-control" aria-label="Query feedback">
    <button className="text-button" aria-expanded={open} onClick={() => setOpen(!open)}>Give feedback</button>
    {open && <div className="feedback-form"><label>Was this useful?<select value={rating} onChange={e => setRating(e.target.value)}><option value="helpful">Helpful</option><option value="needs_work">Needs work</option></select></label>
      <label>Feedback about<select value={category} onChange={e => setCategory(e.target.value)}><option value="answer">Answer</option><option value="sql">SQL</option><option value="tables">Selected tables</option><option value="other">Other</option></select></label>
      <label>Comments or suggested correction<textarea maxLength={4000} value={comment} onChange={e => setComment(e.target.value)} /></label>
      <button disabled={busy} onClick={() => void save()}>Save feedback</button>{error && <p role="alert">{error}</p>}
    </div>}
  </section>
}
