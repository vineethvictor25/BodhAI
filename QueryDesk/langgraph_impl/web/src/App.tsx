import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { ArrowDown, ArrowRight, ArrowUp, BookOpen, Box, Check, ChevronDown, ChevronRight, CircleHelp, Clock3, Database, Layers3, Lightbulb, LoaderCircle, MessageSquareText, Plus, RotateCcw, Search, Settings2, ShieldCheck, Sparkles, Table2, TrendingUp, Users, Wallet, X } from 'lucide-react'
import { request, RequestError, displayValue, type Account, type AppConfig, type Metric, type QueryJob, type Table } from './api'
import { TableConfirmation, SQLApproval, RestoreReview, Feedback, QueryActions } from './ReviewControls'
import Domains from './Domains'
import KnowledgeWorkspace from './KnowledgeWorkspace'
import AccessAdmin from './AccessAdmin'
import QueryResults from './QueryResults'
import Operations from './Operations'
import Visualization from './Visualization'

type View = 'query' | 'data' | 'metrics' | 'domains' | 'access' | 'operations' | 'knowledge'
type Job = QueryJob & { expired?: boolean }
const label = (value: string) => value.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
const modelLabel = (value: string | null) => value?.replace('openai/', '').replace('gpt-oss-', 'GPT-OSS ').replace(/(\d+)b$/, '$1B') || 'Configured model'

function initialJobs(historyKey:string): Job[] {
  try {
    const values: string[] = JSON.parse(localStorage.getItem(historyKey) || '[]')
    if (!Array.isArray(values)) return []
    return values.filter(v => typeof v === 'string' && /^[a-f0-9]{32}$/.test(v)).slice(-20).map(id => ({
      id,question:'Loading saved question…',workspace:'auto',model:null,created_at:'',source:'sample',status: 'queued', elapsed_seconds: 0, events: [], result: null, error: null, planned_tables: [], planned_sql: null, planned_parameters: {},
    }))
  } catch { return [] }
}

export function QueryCard({ job, retry, disabled,onRestored,onEdit,onRefine }: { job: Job; retry: (job: Job) => void; disabled: boolean;onRestored?:(job:QueryJob)=>void;onEdit?:()=>void;onRefine?:(suggestion:string)=>void }) {
  const active = ['queued', 'running', 'awaiting_confirmation','awaiting_sql_approval'].includes(job.status)
  const result = job.result
  const failed = job.status === 'failed'
  const recoverable=job.status==='recoverable'
  const lastQuery = result?.queries?.at(-1)
  const tables = result?.tables?.length ? result.tables : job.planned_tables
  const sql = lastQuery?.sql || job.planned_sql
  const parameters = lastQuery?.parameters || job.planned_parameters
  const current = job.events.at(-1)
  const clarification = result?.status === 'clarification'
  const badge = recoverable ? 'Restore review' : job.status === 'awaiting_sql_approval' ? 'Review SQL' : job.status === 'awaiting_confirmation' ? 'Review tables' : active ? 'In progress' : failed ? 'Not completed' : clarification ? 'Needs clarification' : result?.status === 'refused' ? 'Cannot run' : 'Complete'
  return <article className="query-entry" id={`query-${job.id}`}>
    <div className="question-row"><div className="question-bubble">{job.question}</div></div>
    <section className={`answer-card ${failed ? 'has-error' : ''}`} aria-label={`Result for ${job.question}`}>
      <header className="answer-header"><span className="answer-symbol">{active ? <LoaderCircle className="spin" size={17} /> : failed ? <CircleHelp size={17} /> : <Sparkles size={17} />}</span>
        <strong>{recoverable ? 'Restore saved progress' : active ? 'Working with your data' : failed ? 'Query update' : clarification ? 'A little more detail' : 'Your answer'}</strong>
        <span className={`status-pill ${active ? 'pending' : failed || clarification || recoverable ? 'warning' : ''}`}>{!active && !failed && !clarification && !recoverable && <Check size={11} />}{badge}</span>
        {job.elapsed_seconds > 0 && <span className="elapsed"><Clock3 size={12} />{job.elapsed_seconds.toFixed(1)}s</span>}
      </header>
      {job.source && job.source !== 'sample' && <p className="query-source">Data source: {job.source}</p>}
      {!job.expired&&!recoverable&&<QueryActions job={job} onUpdated={onRestored} onEdit={onEdit}/>}
      {active && !['awaiting_confirmation','awaiting_sql_approval'].includes(job.status) && <div className="progress-block" role="status" aria-live="polite"><span className="pulse-dot" />{job.status === 'queued' ? 'Your question is in the queue…' : current?.label || 'Starting your query…'}
        <div className="progress-line"><span style={{ width: `${Math.min(94, 8 + job.events.filter(e => e.status === 'completed').length * 10)}%` }} /></div>
      </div>}
      {job.status === 'awaiting_confirmation' && job.confirmation && <TableConfirmation key={job.confirmation.version} job={job} />}
      {job.status === 'awaiting_sql_approval' && job.sql_approval && <SQLApproval key={job.sql_approval.version} job={job} />}
      {recoverable&&<RestoreReview job={job} disabled={disabled} onRestored={onRestored} onRetry={()=>retry(job)}/>}
      {!active && !recoverable && <div className="answer-text"><ReactMarkdown>{failed ? job.error || result?.answer || 'This query could not be completed.' : result?.answer || 'No answer was returned.'}</ReactMarkdown></div>}
      {failed && lastQuery && <p className="partial-note">The database query completed, but the explanation could not be finished. The executed SQL is shown below.</p>}
      {!active && !failed && result?.reason && <div className="reason-box"><Lightbulb size={17} /><div><h3>Why this answer</h3><ReactMarkdown>{result.reason}</ReactMarkdown></div></div>}
      {!active&&!recoverable&&lastQuery?.result&&<QueryResults result={lastQuery.result}/>}
      {job.status==='completed'&&result?.status==='answered'&&lastQuery?.result&&<Visualization job={job} onRefine={onRefine} disabled={disabled}/>}
      {!!tables?.length && <div className="sources"><div className="section-label"><Database size={13} />Selected tables</div><div className="table-tags">{tables.map(table => <span key={table}><Table2 size={12} />{table}</span>)}</div></div>}
      {sql && <div className="sql-section"><div className="sql-header"><span><span className="code-mark">&lt;/&gt;</span> SQL query</span><small>{lastQuery ? 'Executed against your database' : 'Prepared · checked before execution'}</small></div><pre><code>{sql}</code></pre>
        {!!parameters && Object.keys(parameters).length > 0 && <div className="parameters"><span>Bound values</span>{Object.entries(parameters).map(([key, value]) => <code key={key}>{key} = {displayValue(value)}</code>)}</div>}
      </div>}
      {!active && <footer className="answer-footer"><span><ShieldCheck size={13} />Read-only access</span>{failed ? <button onClick={() => retry(job)} disabled={disabled} className="text-button"><RotateCcw size={13} />{job.expired ? 'Run again' : 'Retry question'}</button> : <span>{result?.workspaces?.map(label).join(' · ')}</span>}</footer>}
      {!active && !recoverable && !job.expired && <Feedback job={job} />}
      {job.expires_at&&<p className="query-source">Saved history expires {new Date(job.expires_at).toLocaleString()}</p>}
    </section>
  </article>
}

export default function App({user,onSignOut}:{user?:Account;onSignOut?:()=>Promise<void>}={}) {
  const historyKey='querydesk-history-v2:'+(user?.id||'component-test')
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [source, setSource] = useState(user?.role==='member' ? user.grants[0]?.source||'sample':'sample')
  const [sources, setSources] = useState<NonNullable<AppConfig['sources']>>([])
  const [sourceStatus, setSourceStatus] = useState('')
  const [testingSource, setTestingSource] = useState(false)
  const [connectionError, setConnectionError] = useState('')
  const [reload, setReload] = useState(0)
  const [jobs, setJobs] = useState<Job[]>(()=>initialJobs(historyKey))
  const [view, setView] = useState<View>('query')
  const [draft, setDraft] = useState('')
  const [scope, setScope] = useState('auto')
  const [model, setModel] = useState('')
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState('')
  const [settings, setSettings] = useState(false)
  const [deletingHistory,setDeletingHistory]=useState(false)
  const [tables, setTables] = useState<Table[]>([])
  const [metrics, setMetrics] = useState<Metric[]>([])
  const [explorerError, setExplorerError] = useState('')
  const [search, setSearch] = useState('')
  const input = useRef<HTMLTextAreaElement>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const pollDelay=useRef(850)
  const submissionAttempt=useRef<{body:string;key:string}|null>(null)
  const pending = jobs.some(job => ['queued','running','awaiting_confirmation','awaiting_sql_approval'].includes(job.status))

  useEffect(() => {
    let alive = true
    setConfig(null); setSourceStatus('')
    request<AppConfig>(source === 'sample' ? '/api/config' : `/api/config?source=${encodeURIComponent(source)}`).then(value => { if (alive) { setConfig(value); setSources(value.sources || []); setConnectionError('') } }).catch(error => { if (alive) setConnectionError(error.message) })
    return () => { alive = false }
  }, [reload, source])

  useEffect(() => {
    try { localStorage.setItem(historyKey, JSON.stringify(jobs.slice(-20).map(job=>job.id))) } catch { /* Private browsing can disable storage. */ }
  }, [jobs])

  useEffect(()=>{
    const times=jobs.filter(job=>job.expires_at).map(job=>Date.parse(job.expires_at!)).filter(Number.isFinite)
    if(!times.length)return
    const timer=setTimeout(()=>setJobs(old=>old.filter(job=>!job.expires_at||Date.parse(job.expires_at)>Date.now())),Math.max(0,Math.min(2147483647,Math.min(...times)-Date.now()+5)))
    return()=>clearTimeout(timer)
  },[jobs])

  async function deleteHistory(){
    if(!window.confirm('Delete your saved query history and its feedback from this app? Backups are managed separately.'))return
    setDeletingHistory(true);setSendError('')
    try{await request('/api/history/clear',{method:'POST',body:'{}'});setJobs([])}catch(error){setSendError((error as Error).message)}finally{setDeletingHistory(false)}
  }

  useEffect(() => {
    const ids = jobs.filter(job => ['queued', 'running', 'awaiting_confirmation','awaiting_sql_approval'].includes(job.status)).map(job => job.id)
    if (!ids.length) return
    let stopped = false
    const timer = setTimeout(async () => {
      await Promise.all(ids.map(async id => {
        try {
          const next = await request<Job>(`/api/queries/${id}`)
          pollDelay.current=850
          if (!stopped) setJobs(old => old.map(job => job.id === id ? next : job))
        } catch (error) {
          const terminal=error instanceof RequestError&&error.status>=400&&error.status<500&&error.status!==429
          pollDelay.current=Math.min(10000,pollDelay.current*2)
          if (!stopped) setJobs(old => old.map(job => job.id === id ? { ...job, ...(terminal?{status:'failed' as const,expired:true}:{}), error: (error as Error).message } : job))
        }
      }))
    }, pollDelay.current+Math.random()*150)
    return () => { stopped = true; clearTimeout(timer) }
  }, [jobs])

  useEffect(() => {
    if (view === 'query' || view === 'domains' || view === 'access' || view === 'operations') return
    let alive = true
    setExplorerError(''); setSearch('')
    setTables([]); setMetrics([])
    const query = source === 'sample' ? '' : `?source=${encodeURIComponent(source)}`
    const promise = view === 'data' ? request<{ tables: Table[] }>(`/api/schema${query}`).then(data => { if (alive) setTables(data.tables) }) : request<{ metrics: Metric[] }>(`/api/metrics${query}`).then(data => { if (alive) setMetrics(data.metrics) })
    promise.catch(error => { if (alive) setExplorerError(error.message) })
    return () => { alive = false }
  }, [view, reload, source])

  useEffect(() => { if (jobs.length && view === 'query') bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [jobs.length, view])
  useEffect(() => { const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setSettings(false) }; window.addEventListener('keydown', close); return () => window.removeEventListener('keydown', close) }, [])

  const submit = useCallback(async (question = draft, workspace = scope, chosenModel: string | null = model || null, chosenSource = source) => {
    if (!question.trim() || sending || pending) return
    setSending(true); setSendError(''); setView('query')
    try {
      const body=JSON.stringify({ question: question.trim(), workspace, model: chosenModel, source: chosenSource })
      if(submissionAttempt.current?.body!==body)submissionAttempt.current={body,key:crypto.randomUUID()}
      const job = await request<Job>('/api/queries', { method: 'POST',headers:{'Idempotency-Key':submissionAttempt.current.key},body })
      submissionAttempt.current=null
      setJobs(old => old.some(item=>item.id===job.id)?old.map(item=>item.id===job.id?job:item):[...old, job]); setDraft('')
    } catch (error) { setSendError((error as Error).message) }
    finally { setSending(false) }
  }, [draft, scope, model, sending, pending, source])

  const selectSuggestion = (question: string, workspace: string) => { setDraft(question); setScope(workspace); setView('query'); setTimeout(() => input.current?.focus(), 0) }
  const newQuestion = () => { submissionAttempt.current=null;setDraft(''); setSendError(''); setView('query'); setTimeout(() => input.current?.focus(), 0) }
  const filteredTables = tables.filter(table => `${table.table} ${table.columns.map(c => c.name).join(' ')}`.toLowerCase().includes(search.toLowerCase()))
  const filteredMetrics = metrics.filter(metric => `${metric.name} ${metric.definition}`.toLowerCase().includes(search.toLowerCase()))

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#" onClick={event => { event.preventDefault(); setView('query') }} aria-label="QueryDesk home"><span className="brand-mark"><MessageSquareText size={23} strokeWidth={1.8} /></span><span>QueryDesk<span className="brand-caption">YOUR DATA, WITH CLARITY</span></span></a>
      <button className="new-query" onClick={newQuestion}><Plus size={17} />New question<span className="shortcut">↵</span></button>
      <div className="nav-label">WORKSPACE</div>
      <nav aria-label="Main navigation">
        <button className={view === 'query' ? 'active' : ''} onClick={() => setView('query')}><MessageSquareText size={17} />Query workspace</button>
        <button className={view === 'data' ? 'active' : ''} onClick={() => setView('data')}><Database size={17} />Data explorer<span className="nav-count">{config?.table_count ?? '—'}</span></button>
        <button className={view === 'metrics' ? 'active' : ''} onClick={() => setView('metrics')}><BookOpen size={17} />Metric guide</button>
        <button className={view==='knowledge'?'active':''} onClick={()=>setView('knowledge')}><BookOpen size={17}/>Domain knowledge</button>
        {user?.role==='admin'&&<><button className={view === 'domains' ? 'active' : ''} onClick={() => setView('domains')}><Layers3 size={17} />Business domains</button><button className={view === 'access' ? 'active' : ''} onClick={()=>setView('access')}><ShieldCheck size={17}/>Access and security</button><button className={view === 'operations' ? 'active' : ''} onClick={()=>setView('operations')}><Clock3 size={17}/>Operations</button></>}
      </nav>
      <div className="recent-heading"><span className="nav-label">RECENT QUESTIONS</span>{jobs.length > 0 && <button className="clear-history" disabled={pending} onClick={() => setJobs([])}>Hide list</button>}</div>
      <button className="text-button history-delete" disabled={pending||deletingHistory} onClick={()=>void deleteHistory()}>Delete saved history</button>
      <div className="recent-list">{jobs.length ? jobs.slice(-12).reverse().map(job => <button key={job.id} title={job.question} onClick={() => { setView('query'); setTimeout(() => document.getElementById(`query-${job.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60) }}><MessageSquareText size={13} /><span>{job.question}</span></button>) : <p>Your questions will appear here.<br />A useful thought is a good start.</p>}</div>
      <div className="sidebar-bottom"><div className="connection-card"><span className="database-icon"><Database size={18} /></span><div><strong>{config?.dataset || 'Your data'}</strong><span>{config ? `${config.table_count} connected tables` : 'Connecting to your data…'}</span></div><span className={`connection-dot ${connectionError ? 'offline' : ''}`} /></div><div className="sidebar-note"><ShieldCheck size={12} />Read-only by design</div></div>
    </aside>

    <main className="main-panel">
      <header className="topbar"><div className="breadcrumb"><Layers3 size={15} /><span>Workspace</span><ChevronRight size={13} /><strong>{view === 'query' ? 'Ask your data' : view === 'data' ? 'Data explorer' : view === 'domains' ? 'Business domains' : view === 'access' ? 'Access and security' : view === 'operations' ? 'Operations' : view === 'knowledge' ? 'Domain knowledge' : 'Metric guide'}</strong></div>
        <div className="topbar-actions"><span className="data-status"><span className={`connection-dot ${!config ? 'offline' : ''}`} />{user?user.username:config ? 'Data connected' : 'Connecting'}</span>{onSignOut&&<button className="text-button" onClick={()=>void onSignOut()}>Sign out</button>}<button className="icon-button" aria-label="Query preferences" onClick={() => setSettings(!settings)}><Settings2 size={17} /></button></div>
        {settings && <section className="settings-popover" aria-label="Query preferences"><div><strong>Query preferences</strong><button className="icon-button" aria-label="Close preferences" onClick={() => setSettings(false)}><X size={16} /></button></div><label htmlFor="model-choice">Model</label><select id="model-choice" value={model} onChange={e => setModel(e.target.value)}><option value="">Default · {modelLabel(config?.model.default || null)}</option>{config?.model.models.map(value => <option key={value} value={value}>{modelLabel(value)}</option>)}</select><p>Applies to new questions. Your existing answers stay unchanged.</p></section>}
      </header>
      <div className="source-toolbar"><label htmlFor="data-source">Data source</label><select id="data-source" value={source} disabled={pending || sending} onChange={event => { setSource(event.target.value); setScope('auto') }}><option value="sample">Retail sample · SQLite</option>{sources.filter(item => item.id !== 'sample').map(item => <option key={item.id} value={item.id}>{item.name} · {item.kind}{item.configured ? '' : ' (not configured)'}</option>)}</select><button disabled={testingSource || !config} onClick={async () => { setTestingSource(true); setSourceStatus(''); try { await request(`/api/sources/${source}/test`, { method: 'POST', body: '{}' }); setSourceStatus('Connection verified') } catch (error) { setSourceStatus((error as Error).message) } finally { setTestingSource(false) } }}>Test connection</button>{sourceStatus && <span role="status">{sourceStatus}</span>}</div>

      {connectionError && <div className="connection-alert" role="alert"><CircleHelp size={17} /><span>The data service is unavailable. {connectionError}</span><button onClick={() => setReload(value => value + 1)}>Reconnect</button></div>}
      {config?.model.policy&&!config.model.policy.allowed&&<div className="connection-alert" role="status">Model processing is blocked by policy. {config.model.policy.message}</div>}
      {config && !config.model.configured && <div className="connection-alert" role="alert"><CircleHelp size={17} /><span>Configure a model and API key in the app’s local .env file before running a question.</span></div>}

      {view === 'query' ? <>
        <div className={`query-scroll ${jobs.length ? 'with-results' : ''}`}>
          {!jobs.length ? <div className="welcome">
            <div className="welcome-badge"><span className="tiny-spark"><Sparkles size={13} /></span>A little curiosity. A lot of clarity.</div>
            <h1>Ask a question.<br />Find your <em>answer.</em></h1>
            <p className="welcome-description">Explore your business data in plain English.<br />See the answer, understand why, and follow the SQL.</p>
            <div className="suggestions-heading">A few places to start<span>Or ask your own question below</span></div>
            <div className="suggestion-grid">{(config?.suggestions || []).map(suggestion => {
              const Icon = suggestion.icon === 'money' ? Wallet : suggestion.icon === 'box' ? Box : suggestion.icon === 'users' ? Users : TrendingUp
              return <button className="suggestion" key={suggestion.title} onClick={() => selectSuggestion(suggestion.question, suggestion.workspace)}><span className={`suggestion-icon ${suggestion.icon}`}><Icon size={18} /></span><span className="suggestion-title">{suggestion.title}</span><span className="suggestion-question">{suggestion.question}</span><ArrowRight className="suggestion-arrow" size={16} /></button>
            })}</div>
            <div className="welcome-trust"><ShieldCheck size={14} />Your questions explore data. They never change it.</div>
          </div> : <div className="query-feed"><div className="feed-intro"><span className="eyebrow">YOUR QUERY WORKSPACE</span><h1>From questions to clarity.</h1><p>Answers grounded in your data, with the work shown.</p></div>{jobs.map(job => <QueryCard key={job.id} job={job} onRefine={suggestion=>{setDraft(job.question+' '+suggestion);setScope(job.workspace);setModel(job.model||'');setSource(job.source||'sample');setTimeout(()=>input.current?.focus(),0)}} onRestored={restored=>setJobs(old=>old.map(item=>item.id===restored.id?restored:item))} onEdit={()=>{setDraft(job.question);setScope(job.workspace);setModel(job.model||'');setSource(job.source||'sample');setTimeout(()=>input.current?.focus(),0)}} disabled={pending || sending} retry={old => submit(old.question, old.workspace, old.model, old.source || 'sample')} />)}<div ref={bottom} /></div>}
        </div>
        <div className="composer-area"><form className="composer" onSubmit={event => { event.preventDefault(); void submit() }}><label className="sr-only" htmlFor="question">Ask your data a question</label><textarea id="question" ref={input} value={draft} maxLength={8000} onChange={event => setDraft(event.target.value)} placeholder="What would you like to know about your data?" onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void submit() } }} rows={2} />
          <div className="composer-bottom"><div className="scope-control"><Layers3 size={14} /><select aria-label="Business domain" title="A relevance hint; other permitted tables can be proposed" value={scope} onChange={e => setScope(e.target.value)}><option value="auto">Auto domain</option>{config?.workspaces.map(item => <option key={item.id} value={item.id}>{item.name || label(item.id)}</option>)}</select><ChevronDown size={12} /></div><div className="send-controls"><span>{pending ? 'Finishing your current question' : 'Enter to ask'}</span><button className="send-button" type="submit" disabled={!draft.trim() || pending || sending || !config?.model.configured} aria-label="Run question">{sending ? <LoaderCircle className="spin" size={17} /> : <ArrowUp size={19} />}</button></div></div>
        </form>{sendError && <p className="send-error" role="alert">{sendError}</p>}<p className="composer-note">Each question is independent. Include the dates and measures you need.<span>Powered by your connected data</span></p></div>
      </> : view === 'knowledge' ? <KnowledgeWorkspace key={source} source={source} onPublished={()=>setReload(old=>old+1)}/> : view === 'operations' && user?.role==='admin' ? <Operations/> : view === 'access' && user?.role==='admin' ? <AccessAdmin/> : view === 'domains' && user?.role==='admin' ? <Domains key={source} source={source} onSaved={()=>setReload(old=>old+1)} /> : <div className="explorer"><span className="eyebrow">{view === 'data' ? 'THE DATA BEHIND YOUR ANSWERS' : 'A SHARED BUSINESS LANGUAGE'}</span><h1>{view === 'data' ? 'Know your sources.' : 'Know your measures.'}</h1><p>{view === 'data' ? 'Explore connected tables, fields, and relationships.' : 'Understand how common measures are calculated before you ask.'}</p><div className="explorer-search"><Search size={16} /><input aria-label={view === 'data' ? 'Search tables and columns' : 'Search business metrics'} placeholder={view === 'data' ? 'Find a table or field…' : 'Find a measure…'} value={search} onChange={e => setSearch(e.target.value)} /></div>
        {explorerError && <p role="alert" className="send-error">{explorerError}</p>}
        {view === 'data' ? <div className="table-directory">{filteredTables.map(table => <details key={table.table} className="table-definition"><summary><span className="table-definition-icon"><Table2 size={17} /></span><strong>{table.table}</strong><span>{table.columns.length} fields</span><ChevronDown size={15} /></summary><div className="column-list">{table.columns.map(column => <div key={column.name}><code>{column.name}</code>{column.primary_key && <b>PK</b>}<span>{column.type}</span></div>)}</div>{table.foreign_keys.length > 0 && <div className="relationship-list"><span className="section-label">RELATIONSHIPS</span>{table.foreign_keys.map((link, index) => <p key={index}><code>{link.column}</code><ArrowRight size={12} /><code>{link.references_table}.{link.references_column}</code></p>)}</div>}</details>)}</div> : <div className="metric-grid">{filteredMetrics.map(metric => <article className="metric-card" key={metric.id}><span className="metric-unit">{metric.unit}</span><h2>{metric.name}</h2><p>{metric.definition}</p><dl><div><dt>Grouped at</dt><dd>{metric.grain}</dd></div><div><dt>Date basis</dt><dd>{metric.date}</dd></div></dl>{metric.filters.length > 0 && <div className="metric-filters">{metric.filters.map(filter => <code key={filter}>{filter}</code>)}</div>}</article>)}</div>}
        {!explorerError && (view === 'data' ? !filteredTables.length : !filteredMetrics.length) && <p className="empty-search">{search ? 'No matches. Try a different search.' : 'Loading your data…'}</p>}
      </div>}
    </main>
  </div>
}
