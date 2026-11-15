import { useEffect,useState } from 'react'
import Workspace from './App'
import { request,setCsrfToken,type Account } from './api'

type Session={user:Account;csrf_token:string;access_stamp:string}
type Options={sources:{id:string;name:string}[];domains:{id:string;name:string;description:string}[]}

export default function AuthenticatedApp(){
  const [ready,setReady]=useState(false)
  const [setup,setSetup]=useState(false)
  const [session,setSession]=useState<Session|null>(null)
  const [mode,setMode]=useState<'login'|'register'>('login')
  const [username,setUsername]=useState('')
  const [password,setPassword]=useState('')
  const [setupToken,setSetupToken]=useState('')
  const [source,setSource]=useState('sample')
  const [domain,setDomain]=useState('')
  const [options,setOptions]=useState<Options>({sources:[],domains:[]})
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  function accept(value:Session){setCsrfToken(value.csrf_token);setSession(value);setPassword('');setError('')}
  useEffect(()=>{
    request<{setup_required:boolean}>('/api/auth/status').then(async status=>{
      setSetup(status.setup_required)
      if(!status.setup_required){try{accept(await request<Session>('/api/auth/me'))}catch{/* Sign-in form is the expected response to an absent session. */}}
    }).catch(e=>setError(e.message)).finally(()=>setReady(true))
    const expired=()=>{setCsrfToken('');setSession(null);setError('Your session ended. Sign in again.')}
    window.addEventListener('querydesk-session-expired',expired)
    return()=>window.removeEventListener('querydesk-session-expired',expired)
  },[])
  useEffect(()=>{
    if(mode!=='register'||setup)return
    let active=true
    setDomain('');setError('')
    request<Options>(`/api/auth/onboarding-options?source=${encodeURIComponent(source)}`).then(data=>{if(active)setOptions(data)}).catch(e=>{if(active)setError(e.message)})
    return()=>{active=false}
  },[mode,source,setup])
  useEffect(()=>{
    if(!session)return
    let active=true
    const timer=setInterval(()=>{void request<Session>('/api/auth/me').then(value=>{if(active)accept(value)}).catch(()=>{if(active){setCsrfToken('');setSession(null)}})},60000)
    return()=>{active=false;clearInterval(timer)}
  },[session?.user.id])
  async function submit(){
    setBusy(true);setError('')
    try{
      const endpoint=setup?'setup':mode==='register'?'register':'login'
      const value=await request<Session>(`/api/auth/${endpoint}`,{method:'POST',body:JSON.stringify({username,password,...(endpoint==='setup'?{setup_token:setupToken}:{}),...(endpoint==='register'?{source,domain}:{})})})
      accept(value);setSetup(false);setSetupToken('')
    }catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  if(!ready)return <main className="auth-screen"><p role="status">Opening QueryDesk…</p></main>
  if(session)return <Workspace key={session.user.id+':'+session.access_stamp} user={session.user} onSignOut={async()=>{try{await request('/api/auth/logout',{method:'POST',body:'{}'})}finally{setCsrfToken('');setSession(null);setPassword('')}}}/>
  return <main className="auth-screen"><section className="auth-card"><span className="eyebrow">QUERYDESK · YOUR DATA, WITH CLARITY</span>
    <h1>{setup?'Create the administrator':mode==='register'?'Join your workspace':'Welcome back'}</h1>
    <p>{setup?'This is the first setup. Enter the token configured in the server .env. Keep the service private until the administrator account is created.':mode==='register'?'Choose one business domain to start. An administrator can grant more access later.':'Sign in to query the data you are permitted to use.'}</p>
    <form onSubmit={event=>{event.preventDefault();void submit()}}>
      <label>Account name<input required autoComplete="username" minLength={3} maxLength={80} pattern="[A-Za-z0-9][A-Za-z0-9_.@-]{2,79}" value={username} onChange={e=>setUsername(e.target.value)}/></label>
      <label>Password<input required type="password" autoComplete={setup||mode==='register'?'new-password':'current-password'} minLength={15} maxLength={128} value={password} onChange={e=>setPassword(e.target.value)}/></label>
      <small>Use 15–128 characters. Passwords are never stored in readable form.</small>
      {setup&&<label>Administrator setup token<input required type="password" autoComplete="off" minLength={32} maxLength={256} value={setupToken} onChange={e=>setSetupToken(e.target.value)}/></label>}
      {!setup&&mode==='register'&&<><label>Data source<select value={source} onChange={e=>setSource(e.target.value)}>{options.sources.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>Starting business domain<select required value={domain} onChange={e=>setDomain(e.target.value)}><option value="">Choose one domain</option>{options.domains.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label><p>{options.domains.find(item=>item.id===domain)?.description}</p></>}
      {error&&<p role="alert" className="send-error">{error}</p>}<button disabled={busy||(!setup&&mode==='register'&&!domain)} type="submit">{busy?'Please wait…':setup?'Create administrator':mode==='register'?'Create restricted account':'Sign in'}</button>
    </form>
    {!setup&&<button className="text-button auth-switch" onClick={()=>{setMode(mode==='login'?'register':'login');setError('');setPassword('')}}>{mode==='login'?'New here? Create an account':'Already registered? Sign in'}</button>}
  </section></main>
}
