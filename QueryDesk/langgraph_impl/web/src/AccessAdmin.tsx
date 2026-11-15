import { useEffect,useState } from 'react'
import { request,type Account,type AccessGrant } from './api'

type Group={id:string;name:string;pii:'inherit'|'allow'|'deny';grants:AccessGrant[];revision:number}
type Directory={users:Account[];groups:Group[];pii_encryption_enabled:boolean;row_policy_support:boolean;row_policy_enabled?:boolean;row_policy_admin_bypass?:boolean}
type Choice=AccessGrant&{label:string}
const contains=(grants:AccessGrant[],choice:AccessGrant)=>grants.some(g=>g.source===choice.source&&g.domain===choice.domain)
const toggle=(grants:AccessGrant[],choice:AccessGrant)=>contains(grants,choice)?grants.filter(g=>g.source!==choice.source||g.domain!==choice.domain):[...grants,{source:choice.source,domain:choice.domain}]

export default function AccessAdmin(){
  const [data,setData]=useState<Directory|null>(null)
  const [choices,setChoices]=useState<Choice[]>([])
  const [member,setMember]=useState<Account|null>(null)
  const [group,setGroup]=useState<Group|null>(null)
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [message,setMessage]=useState('')
  async function load(){
    setData(await request<Directory>('/api/admin/access'))
    const {sources}=await request<{sources:{id:string;name:string;configured:boolean}[]}>('/api/sources')
    const batches=await Promise.all(sources.filter(s=>s.configured).map(async source=>{
      try{const value=await request<{domains:{id:string;name:string}[]}>(`/api/sources/${source.id}/domains`);return value.domains.map(domain=>({source:source.id,domain:domain.id,label:source.name+' · '+domain.name}))}catch{return []}
    }))
    setChoices(batches.flat())
  }
  useEffect(()=>{void load().catch(e=>setError(e.message))},[])
  async function saveMember(){
    if(!member)return
    setBusy(true);setError('')
    try{await request(`/api/admin/users/${member.id}/access`,{method:'POST',body:JSON.stringify({grants:member.direct_grants,groups:member.groups,pii:!!member.pii,enabled:!!member.enabled,revision:member.revision})});setMember(null);await load();setMessage('Member access saved. Their sessions were revoked; they must sign in again.')}catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  async function saveGroup(){
    if(!group)return
    setBusy(true);setError('')
    try{await request('/api/admin/groups',{method:'POST',body:JSON.stringify(group)});setGroup(null);await load();setMessage('Group policy saved. Members inherit the updated permissions.')}catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  const grantBoxes=(grants:AccessGrant[],change:(value:AccessGrant[])=>void)=><div className="confirmation-options">{choices.map(choice=><label key={choice.source+':'+choice.domain}><input type="checkbox" checked={contains(grants,choice)} onChange={()=>change(toggle(grants,choice))}/>{choice.label}</label>)}</div>
  return <div className="explorer"><span className="eyebrow">ADMINISTRATOR</span><h1>Access and security.</h1><p>Manage direct grants and inherited group policies. An explicit group PII denial overrides other member grants.</p>
    <p className="domain-message">Local PII encryption: {data?.pii_encryption_enabled?'enabled':'disabled'}. PII viewing permissions remain enforced in either mode.</p>
    <p>Row-level access: {data?.row_policy_enabled?'enabled':'disabled'}. Rules and allowed region/country/tenant values are maintained in the server policy file. Members inherit the attributes of their assigned groups. The administrator bypass is controlled by that file.</p>
    {message&&<p role="status">{message}</p>}{error&&<p role="alert" className="send-error">{error}</p>}
    <h2>Members</h2><div className="member-directory">{data?.users.map(user=><article className="metric-card" key={user.id}><h3>{user.username}</h3><p>Account ID: <code>{user.id}</code></p><p>{user.role==='admin'?(data?.row_policy_enabled&&data?.row_policy_admin_bypass===false?'Administrator · configured row restrictions · PII permitted':'Administrator · all configured data and PII'):`${user.enabled?'Active':'Disabled'} · ${user.grants.length} effective domain grants · ${user.can_view_pii?'PII permitted':'PII restricted'}`}</p>{user.role!=='admin'&&<button className="text-button" onClick={()=>{setMember(structuredClone(user));setGroup(null);setMessage('')}}>Manage {user.username}</button>}</article>)}</div>
    {member&&<form className="domain-form" aria-label="Member access" onSubmit={e=>{e.preventDefault();void saveMember()}}><h2>Access for {member.username}</h2><label className="check-label"><input type="checkbox" checked={!!member.enabled} onChange={e=>setMember({...member,enabled:Number(e.target.checked)})}/>Account enabled</label><label className="check-label"><input type="checkbox" checked={!!member.pii} onChange={e=>setMember({...member,pii:Number(e.target.checked)})}/>Direct permission to view decrypted PII (group denial still wins)</label><fieldset><legend>Direct domain grants</legend>{grantBoxes(member.direct_grants,value=>setMember({...member,direct_grants:value}))}</fieldset><fieldset><legend>Group membership</legend><div className="confirmation-options">{data?.groups.map(item=><label key={item.id}><input type="checkbox" checked={member.groups.includes(item.id)} onChange={e=>setMember({...member,groups:e.target.checked?[...member.groups,item.id]:member.groups.filter(id=>id!==item.id)})}/>{item.name} · PII {item.pii}</label>)}</div></fieldset><div className="review-actions"><button disabled={busy} type="submit">Save member access</button><button type="button" onClick={()=>setMember(null)}>Close</button></div></form>}
    <h2>User groups</h2><div className="domain-actions"><button onClick={()=>{setGroup({id:'group_'+crypto.randomUUID().replaceAll('-','').slice(0,16),name:'',pii:'inherit',grants:[],revision:0});setMember(null)}}>Create group</button></div><div className="member-directory">{data?.groups.map(item=><article className="metric-card" key={item.id}><h3>{item.name}</h3><p>Policy ID: <code>{item.id}</code></p><p>PII: {item.pii} · {item.grants.length} inherited domain grants</p><button className="text-button" onClick={()=>{setGroup(structuredClone(item));setMember(null);setMessage('')}}>Edit {item.name}</button></article>)}</div>
    {group&&<form className="domain-form" aria-label="Group policy" onSubmit={e=>{e.preventDefault();void saveGroup()}}><h2>Group policy</h2><label>Group name<input required maxLength={100} value={group.name} onChange={e=>setGroup({...group,name:e.target.value})}/></label><label>PII policy<select value={group.pii} onChange={e=>setGroup({...group,pii:e.target.value as Group['pii']})}><option value="inherit">Inherit / no extra grant</option><option value="allow">Allow decrypted PII</option><option value="deny">Deny decrypted PII</option></select></label><fieldset><legend>Inherited domain grants</legend>{grantBoxes(group.grants,value=>setGroup({...group,grants:value}))}</fieldset><div className="review-actions"><button disabled={busy||!group.name.trim()} type="submit">Save group policy</button><button type="button" onClick={()=>setGroup(null)}>Close</button></div></form>}
  </div>
}
