import { useEffect, useState } from 'react'
import { request } from './api'

export type Domain = {id: string; name: string; description: string; keywords: string[]; tables: string[]; revision: number}
type Directory = {domains: Domain[]; available_tables: string[]}
const blank = (): Domain => ({id:`domain_${crypto.randomUUID().replaceAll('-','').slice(0,16)}`,name:'',description:'',keywords:[],tables:[],revision:0})

export default function Domains({source,onSaved}:{source:string;onSaved:()=>void}) {
  const [directory,setDirectory]=useState<Directory>({domains:[],available_tables:[]})
  const [editing,setEditing]=useState<Domain|null>(null)
  const [terms,setTerms]=useState('')
  const [drafts,setDrafts]=useState<Domain[]>([])
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [message,setMessage]=useState('')
  const endpoint=`/api/sources/${encodeURIComponent(source)}/domains`
  useEffect(() => {
    let alive=true
    setError(''); setMessage(''); setEditing(null); setDrafts([])
    request<Directory>(endpoint).then(data=>{if(alive)setDirectory(data)}).catch(e=>{if(alive)setError(e.message)})
    return ()=>{alive=false}
  },[endpoint])
  function edit(domain:Domain) {setEditing({...domain,tables:[...domain.tables]});setTerms(domain.keywords.join(', '));setError('');setMessage('')}
  async function save() {
    if(!editing)return
    setBusy(true);setError('')
    try {
      await request(endpoint,{method:'POST',body:JSON.stringify({...editing,keywords:terms.split(',').map(t=>t.trim()).filter(Boolean)})})
      setDirectory(await request<Directory>(endpoint));setEditing(null);setMessage('Domain saved. It will guide new questions.');onSaved()
    } catch(e) {setError((e as Error).message)} finally {setBusy(false)}
  }
  async function suggest() {
    setBusy(true);setError('');setMessage('')
    try {
      const result=await request<{domains:Omit<Domain,'revision'>[]}>(`${endpoint}/suggest`,{method:'POST',body:'{}'})
      setDrafts(result.domains.map(domain=>({...domain,id:blank().id,revision:0})))
      setMessage('Draft suggestions only. Review a draft and save it to create a domain.')
    } catch(e) {setError((e as Error).message)} finally {setBusy(false)}
  }
  return <div className="explorer domains-page"><span className="eyebrow">BUSINESS CONTEXT FOR YOUR DATA</span><h1>Define your domains.</h1>
    <p>Describe how your business sees its data. Domains guide table selection; other permitted tables can still be proposed when needed.</p>
    <div className="domain-actions"><button disabled={busy} onClick={()=>edit(blank())}>Create domain</button><button disabled={busy} onClick={()=>void suggest()}>{busy?'Working…':'Suggest domains with AI'}</button></div>
    {message&&<p role="status" className="domain-message">{message}</p>}{error&&<p role="alert" className="send-error">{error}</p>}
    {!!drafts.length&&<section className="domain-drafts" aria-label="Draft domain suggestions"><h2>Suggested domains · review required</h2>{drafts.map(domain=><article key={domain.id}><h3>{domain.name}</h3><p>{domain.description}</p><p>{domain.tables.join(', ')}</p><button disabled={busy} onClick={()=>edit(domain)}>Review {domain.name}</button></article>)}</section>}
    {editing&&<form className="domain-form" aria-label="Domain details" onSubmit={event=>{event.preventDefault();void save()}}><h2>{editing.revision?'Edit domain':'Review domain'}</h2>
      <label>Domain name<input required maxLength={100} value={editing.name} onChange={e=>setEditing({...editing,name:e.target.value})}/></label>
      <label>Business description<textarea required maxLength={2000} placeholder="For example: customer purchasing patterns, order amounts and discounts." value={editing.description} onChange={e=>setEditing({...editing,description:e.target.value})}/></label>
      <label>Business terms, separated by commas<input value={terms} onChange={e=>setTerms(e.target.value)} placeholder="sales, customers, purchases"/></label>
      <fieldset><legend>Relevant tables</legend><p>Include tables needed to join your business data. This selection does not change database permissions.</p><div className="confirmation-options">{directory.available_tables.map(table=><label key={table}><input type="checkbox" checked={editing.tables.includes(table)} onChange={e=>setEditing({...editing,tables:e.target.checked?[...editing.tables,table]:editing.tables.filter(t=>t!==table)})}/>{table}</label>)}</div></fieldset>
      <div className="review-actions"><button disabled={busy||!editing.name.trim()||!editing.description.trim()||!editing.tables.length} type="submit">Save domain</button><button disabled={busy} type="button" onClick={()=>setEditing(null)}>Close editor</button></div>
    </form>}
    <div className="domain-directory">{directory.domains.map(domain=><article className="metric-card" key={domain.id}><span className="metric-unit">Relevance hint</span><h2>{domain.name}</h2><p>{domain.description}</p><div className="table-tags">{domain.tables.map(table=><span key={table}>{table}</span>)}</div><p>{domain.keywords.join(' · ')}</p><button className="text-button" disabled={busy} onClick={()=>edit(domain)}>Edit {domain.name}</button></article>)}</div>
  </div>
}
