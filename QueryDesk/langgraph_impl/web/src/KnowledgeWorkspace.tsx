import {useCallback,useEffect,useState} from 'react'
import {request,type Table} from './api'

type Kind='concept'|'metric'|'relationship'|'rule'|'example'
type Definition={kind:Kind;title:string;description:string;aliases:string[];columns:Record<string,string[]>;unit:string;date_basis:string;grain:string;filters:string[];relationship:{left_table:string;right_table:string;keys:Record<string,string>;cardinality:'one_to_one'|'one_to_many'|'many_to_one'|'many_to_many'|'unknown'}|null;sql:string;parameters:{name:string;value:unknown}[];questions:string[]}
type Entry={domain:string;id:string;revision:number;content:Definition;author:string;state:string;head_revision:number;published_revision:number|null}
type Domain={id:string;name:string;role:string;tables:string[]}
type Directory={domains:Domain[];entries:Entry[];assignments:{domain:string;user_id:string;role:string}[];users:{id:string;username:string}[];schema:{tables:Table[]};can_assign:boolean}
type Editing={id:string;expected_revision:number;content:Definition}
const fresh=():Definition=>({kind:'concept',title:'',description:'',aliases:[],columns:{},unit:'',date_basis:'',grain:'',filters:[],relationship:null,sql:'',parameters:[],questions:[]})
const lines=(value:string)=>value.split('\n').map(s=>s.trim()).filter(Boolean)

export default function KnowledgeWorkspace({source,onPublished}:{source:string;onPublished:()=>void}){
  const [data,setData]=useState<Directory|null>(null)
  const [domain,setDomain]=useState('')
  const [editing,setEditing]=useState<Editing|null>(null)
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [message,setMessage]=useState('')
  const [importText,setImportText]=useState('')
  const [parameters,setParameters]=useState('[]')
  const [goal,setGoal]=useState('')
  const [suggestions,setSuggestions]=useState<Definition[]>([])
  const [assignee,setAssignee]=useState('')
  const [assignmentRole,setAssignmentRole]=useState('owner')
  const [reviewNote,setReviewNote]=useState('')
  const [confirmed,setConfirmed]=useState(false)
  const endpoint=`/api/sources/${encodeURIComponent(source)}/knowledge`
  const load=useCallback(async(selectedDomain?:string)=>{
    const scope=selectedDomain||domain;const result=await request<Directory>(endpoint+(scope?'?domain='+encodeURIComponent(scope):''));setData(result)
    setDomain(old=>result.domains.some(d=>d.id===old)?old:result.domains[0]?.id||'')
  },[endpoint,domain])
  useEffect(()=>{let alive=true;request<Directory>(endpoint).then(result=>{if(alive){setData(result);setDomain(result.domains[0]?.id||'')}}).catch(e=>{if(alive)setError(e.message)});return()=>{alive=false}},[endpoint])
  const selected=data?.domains.find(d=>d.id===domain)
  const canWrite=!!selected&&['admin','owner','contributor'].includes(selected.role)
  const canReview=!!selected&&['admin','reviewer'].includes(selected.role)
  const tables=data?.schema.tables.filter(t=>selected?.tables.includes(t.table))||[]
  const versions=data?.entries.filter(e=>e.domain===domain)||[]
  const heads=versions.filter(e=>e.revision===e.head_revision||(!versions.some(v=>v.id===e.id&&v.revision===e.head_revision)&&e.revision===e.published_revision))
  function open(content=fresh(),id='entry_'+crypto.randomUUID().replaceAll('-','').slice(0,16),expected_revision=0){setEditing({id,expected_revision,content:structuredClone(content)});setParameters(JSON.stringify(content.parameters));setError('')}
  function change(values:Partial<Definition>){if(editing)setEditing({...editing,content:{...editing.content,...values}})}
  async function perform(action:()=>Promise<void>){setBusy(true);setError('');setMessage('');try{await action()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  async function save(){if(!editing)return;await perform(async()=>{
    await request(endpoint+'/entries',{method:'POST',body:JSON.stringify({domain,...editing,content:{...editing.content,parameters:JSON.parse(parameters)}})})
    setEditing(null);await load();setMessage('Draft saved. It will not influence generation until reviewed and published.')
  })}
  async function review(entry:Entry,action:string){await perform(async()=>{
    const result=await request<{validation?:{metadata_valid:boolean};metadata_valid?:boolean}>(`${endpoint}/${encodeURIComponent(domain)}/${encodeURIComponent(entry.id)}/review`,{method:'POST',body:JSON.stringify({revision:entry.revision,action,confirm_business_review:confirmed,review_note:reviewNote})})
    await load();setMessage(action==='validate'&&result.metadata_valid?'Metadata checks passed. No query was executed; business correctness and join cardinality are not proven.':`Knowledge ${action} completed.`)
    if(['publish','rollback','retire'].includes(action)){setConfirmed(false);setReviewNote('');onPublished()}
  })}
  async function importDefinition(){await perform(async()=>{const value=JSON.parse(importText);const result=await request<{content:Definition}>(endpoint+'/preview',{method:'POST',body:JSON.stringify({domain,id:'import_preview',expected_revision:0,content:value.content||value})});open(result.content);setImportText('')})}
  const content=editing?.content
  return <div className="explorer knowledge-page"><span className="eyebrow">BUSINESS-OWNED, PERMISSION-AWARE CONTEXT</span><h1>Domain knowledge.</h1>
    <p>Define what your data means. Only published knowledge guides future questions. Maintaining knowledge never grants access to data.</p>
    {error&&<p role="alert" className="send-error">{error}</p>}{message&&<p role="status" className="domain-message">{message}</p>}
    <label>Business domain<select aria-label="Knowledge domain" disabled={busy} value={domain} onChange={e=>{const next=e.target.value;setDomain(next);void perform(async()=>{await load(next)});setEditing(null);setSuggestions([]);setConfirmed(false);setError('');setReviewNote('')}}>{data?.domains.map(d=><option key={d.id} value={d.id}>{d.name} · {d.role}</option>)}</select></label>
    {!data&&<p>Loading permitted knowledge…</p>}
    {selected&&<p>Your knowledge role: <strong>{selected.role}</strong>. Contributors draft and submit; a separate reviewer or administrator publishes. Administrators may perform both roles for small teams.</p>}
    {data?.can_assign&&<details className="knowledge-section"><summary>Assign knowledge owners and reviewers</summary><p>Members must already have access to this source and domain. Assignment changes no data permissions.</p>
      <label>Account<select aria-label="Knowledge account" value={assignee} onChange={e=>setAssignee(e.target.value)}><option value="">Choose an account</option>{data.users.map(u=><option key={u.id} value={u.id}>{u.username}</option>)}</select></label>
      <label>Knowledge role<select value={assignmentRole} onChange={e=>setAssignmentRole(e.target.value)}>{['owner','contributor','reviewer','remove'].map(role=><option key={role}>{role}</option>)}</select></label>
      <button disabled={busy||!assignee||!domain} onClick={()=>void perform(async()=>{await request(endpoint+'/assignments',{method:'POST',body:JSON.stringify({domain,user_id:assignee,role:assignmentRole})});await load();setMessage('Knowledge assignment saved; data access is unchanged.');onPublished()})}>Save assignment</button>
      <ul>{data.assignments.filter(a=>a.domain===domain).map(a=><li key={a.user_id}>{data.users.find(u=>u.id===a.user_id)?.username||'Account'} · {a.role}</li>)}</ul>
    </details>}
    {canWrite&&<><div className="domain-actions"><button disabled={busy} onClick={()=>open()}>Create definition</button></div>
      <details className="knowledge-section"><summary>Import a definition from JSON</summary><p>Paste a definition object or an object containing <code>content</code>. Review and save it as a new draft; imported approval states are never trusted.</p><textarea aria-label="Knowledge JSON" maxLength={30000} value={importText} onChange={e=>setImportText(e.target.value)}/><button disabled={busy||!importText.trim()} onClick={()=>void importDefinition()}>Review imported definition</button></details>
      <details className="knowledge-section"><summary>Draft suggestions with AI</summary><p>Uses permitted metadata only. Suggestions may contain assumptions and are neither saved nor published.</p><label>What should this domain explain?<textarea maxLength={2000} value={goal} onChange={e=>setGoal(e.target.value)}/></label><button disabled={busy||!goal.trim()} onClick={()=>void perform(async()=>{const result=await request<{entries:Definition[]}>(endpoint+'/suggest',{method:'POST',body:JSON.stringify({domain,goal})});setSuggestions(result.entries)})}>Suggest draft knowledge</button>{suggestions.map((entry,i)=><article key={i}><h3>{entry.title}</h3><p>{entry.description}</p><button disabled={busy} onClick={()=>open(entry)}>Review suggestion</button></article>)}</details></>}
    {content&&canWrite&&<form className="domain-form knowledge-editor" aria-label="Knowledge definition" onSubmit={e=>{e.preventDefault();void save()}}><h2>{editing.expected_revision?'Create a new revision':'New draft definition'}</h2>
      <label>Definition type<select value={content.kind} onChange={e=>{const kind=e.target.value as Kind;change({kind,relationship:kind==='relationship'?{left_table:'',right_table:'',keys:{'':''},cardinality:'unknown'}:null,sql:'',parameters:[]});setParameters('[]')}}>{['concept','metric','relationship','rule','example'].map(kind=><option key={kind}>{kind}</option>)}</select></label>
      <label>Name<input required maxLength={100} value={content.title} onChange={e=>change({title:e.target.value})}/></label>
      <label>Business definition<textarea required maxLength={2000} value={content.description} onChange={e=>change({description:e.target.value})} placeholder="Explain the meaning, accepted calculation and any unresolved decisions. Do not paste personal data or credentials."/></label>
      <label>Synonyms, separated by commas<input value={content.aliases.join(', ')} onChange={e=>change({aliases:e.target.value.split(',').map(s=>s.trim()).filter(Boolean)})}/></label>
      <fieldset><legend>Permitted column mappings</legend><p>Map every required measure, date, filter and join column. Hidden columns cannot be introduced through knowledge.</p>{tables.map(table=><details key={table.table}><summary>{table.table}</summary><div className="confirmation-options">{table.columns.map(column=><label key={column.name}><input type="checkbox" checked={Object.hasOwn(content.columns,table.table)&&content.columns[table.table].includes(column.name)} onChange={e=>{const previous=Object.hasOwn(content.columns,table.table)?content.columns[table.table]:[];const next=e.target.checked?[...previous,column.name]:previous.filter(c=>c!==column.name);const columns={...content.columns,[table.table]:next};if(!next.length)delete columns[table.table];change({columns})}}/>{column.name}</label>)}</div></details>)}</fieldset>
      {content.kind==='metric'&&<><label>Units<input required maxLength={100} value={content.unit} onChange={e=>change({unit:e.target.value})} placeholder="For example: INR, employees, percentage"/></label><label>Aggregation grain<input required maxLength={300} value={content.grain} onChange={e=>change({grain:e.target.value})} placeholder="What does one contributing record represent?"/></label><label>Reporting date<select value={content.date_basis} onChange={e=>change({date_basis:e.target.value})}><option value="">No date basis / not applicable</option>{Object.entries(content.columns).flatMap(([table,cols])=>cols.map(col=><option key={`${table}.${col}`}>{table}.{col}</option>))}</select></label><label>Required business filters, one per line<textarea value={content.filters.join('\n')} onChange={e=>change({filters:lines(e.target.value)})}/></label></>}
      {content.relationship&&<fieldset><legend>Accepted relationship</legend>{(['left_table','right_table'] as const).map(side=><label key={side}>{side==='left_table'?'From table':'To table'}<select value={content.relationship![side]} onChange={e=>change({relationship:{...content.relationship!,[side]:e.target.value,keys:{'':''}}})}><option value="">Choose a table</option>{tables.map(t=><option key={t.table}>{t.table}</option>)}</select></label>)}
        {Object.entries(content.relationship.keys).map(([left,right],index)=><div className="knowledge-key-pair" key={index}>{(['left','right'] as const).map(side=><label key={side}>{side==='left'?'From column':'To column'}<select value={side==='left'?left:right} onChange={e=>{const pairs=Object.entries(content.relationship!.keys);pairs[index]=side==='left'?[e.target.value,right]:[left,e.target.value];change({relationship:{...content.relationship!,keys:Object.fromEntries(pairs)}})}}><option value="">Choose a column</option>{tables.find(t=>t.table===content.relationship![side==='left'?'left_table':'right_table'])?.columns.map(c=><option key={c.name}>{c.name}</option>)}</select></label>)}</div>)}<button type="button" disabled={Object.keys(content.relationship.keys).length>=8||'' in content.relationship.keys} onClick={()=>change({relationship:{...content.relationship!,keys:{...content.relationship!.keys,'':''}}})}>Add join key pair</button>
        <label>Declared cardinality<select value={content.relationship.cardinality} onChange={e=>change({relationship:{...content.relationship!,cardinality:e.target.value as NonNullable<Definition['relationship']>['cardinality']}})}>{['unknown','one_to_one','one_to_many','many_to_one','many_to_many'].map(value=><option key={value}>{value}</option>)}</select></label><p>Cardinality is a business declaration, not a result proven by metadata validation.</p></fieldset>}
      {content.kind==='example'&&<><label>Reviewed read-only SQL<textarea required maxLength={20000} value={content.sql} onChange={e=>change({sql:e.target.value})}/></label><label>Named parameters as JSON list<textarea value={parameters} onChange={e=>setParameters(e.target.value)}/></label><p>Planning validates syntax and permitted references. Saving or validating never executes this SQL.</p></>}
      <label>Representative business review questions, one per line<textarea value={content.questions.join('\n')} onChange={e=>change({questions:lines(e.target.value)})} placeholder="Required before publishing. Review against independent business expectations."/></label>
      <div className="review-actions"><button disabled={busy} type="submit">Save draft</button><button type="button" disabled={busy} onClick={()=>setEditing(null)}>Close editor</button></div>
    </form>}
    {canReview&&<section className="knowledge-section"><h2>Publication review</h2><label>Review evidence, independent checks and limitations<textarea maxLength={1000} value={reviewNote} onChange={e=>setReviewNote(e.target.value)}/></label><label className="check-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>I reviewed the business meaning and representative questions. Metadata checks alone do not establish result correctness.</label></section>}
    <div className="domain-directory">{heads.map(entry=><article className="metric-card" key={entry.id}><span className="metric-unit">{entry.content.kind} · {entry.state} · revision {entry.revision}</span><h2>{entry.content.title}</h2><p>{entry.content.description}</p><p>{entry.published_revision?`Active published revision: ${entry.published_revision}`:'Not published; excluded from generation.'}</p>
      {canWrite&&<button disabled={busy} onClick={()=>open(entry.content,entry.id,entry.head_revision)}>Create revision</button>}
      <details onToggle={event=>{if(event.currentTarget.open)void perform(async()=>{const result=await request<{entries:Entry[]}>(`${endpoint}/${encodeURIComponent(domain)}/${encodeURIComponent(entry.id)}/history`);setData(old=>old?{...old,entries:[...old.entries.filter(e=>e.domain!==domain||e.id!==entry.id),...result.entries]}:old)})}}><summary>Version history and review actions</summary>{versions.filter(v=>v.id===entry.id).map(version=><section className="knowledge-version" key={version.revision}><h3>Revision {version.revision} · {version.state}</h3><p>{version.content.description}</p>{version.content.questions.map((q,i)=><p key={i}>Review question: {q}</p>)}
        {selected?.role!=='reader'&&<button disabled={busy} onClick={()=>void review(version,'validate')}>Validate metadata</button>}
        {canWrite&&version.state==='draft'&&version.revision===version.head_revision&&<button disabled={busy} onClick={()=>void review(version,'submit')}>Submit for review</button>}
        {canReview&&version.state==='in_review'&&version.revision===version.head_revision&&<><button disabled={busy||!confirmed||!reviewNote.trim()} onClick={()=>void review(version,'publish')}>Publish revision</button><button disabled={busy} onClick={()=>void review(version,'reject')}>Request changes</button></>}
        {canReview&&version.state==='published'&&version.published_revision!==version.revision&&<button disabled={busy||!confirmed||!reviewNote.trim()} onClick={()=>void review(version,'rollback')}>Restore published revision</button>}
        {canReview&&version.published_revision===version.revision&&<button disabled={busy} onClick={()=>void review(version,'retire')}>Retire published revision</button>}
      </section>)}</details>
    </article>)}</div>
    {data&&!heads.length&&<p>No visible definitions in this domain. Assigned owners can create the first draft.</p>}
  </div>
}
