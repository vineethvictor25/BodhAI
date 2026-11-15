import {useEffect,useState} from 'react'
import {request} from './api'

export type OperationsData={started_at:string;uptime_seconds:number;scope:string;counters:Record<string,number>;
  queue:Record<string,number>;
  durations:{category:string;name:string;outcome:string;count:number;mean_seconds:number;max_seconds:number;p95_upper_seconds:number|null}[];
  tokens:Record<string,{reported_calls:number;unknown_usage_calls:number;input_tokens:number;output_tokens:number;total_tokens:number}>}
const label=(value:string)=>value.replaceAll('_',' ')
export default function Operations(){
  const [data,setData]=useState<OperationsData|null>(null)
  const [error,setError]=useState('')
  const [refresh,setRefresh]=useState(0)
  useEffect(()=>{
    let alive=true;let timer:ReturnType<typeof setTimeout>;const controller=new AbortController()
    async function load(){
      try{const next=await request<OperationsData>('/api/admin/operations',{signal:controller.signal});if(alive){setData(next);setError('')}}
      catch(e){if(alive)setError((e as Error).message)}
      finally{if(alive)timer=setTimeout(()=>void load(),5000)}
    }
    void load();return()=>{alive=false;clearTimeout(timer);controller.abort()}
  },[refresh])
  return <div className="explorer operations"><span className="eyebrow">ADMINISTRATION</span><h1>Operations</h1>
    <p>Live totals for this API process since its last start. Refreshes every five seconds. Questions, SQL, result cells and credentials are excluded.</p>
    <button className="text-button" onClick={()=>setRefresh(value=>value+1)}>Refresh operations</button>
    {error&&<p role="alert">{error}</p>}
    {!data&&!error&&<p role="status">Loading operations…</p>}
    {data&&<><p>Process started {new Date(data.started_at).toLocaleString()}. Uptime: {Math.floor(data.uptime_seconds)} seconds.</p>
      <h2>Queue and capacity</h2><dl className="operations-counts">{Object.entries(data.queue).map(([key,value])=><div key={key}><dt>{label(key)}</dt><dd>{value}</dd></div>)}</dl>
      <h2>Job counters</h2><dl className="operations-counts">{Object.entries(data.counters).map(([key,value])=><div key={key}><dt>{label(key)}</dt><dd>{value}</dd></div>)}</dl>
      <h2>Latency and provider failures</h2><p>Warehouse timing is client-observed execution time, including access enforcement, preparation and fetching. P95 is a histogram bucket upper bound; “above range” means over 1,200 seconds. Human review waits are excluded from stage timing.</p>
      <div className="operations-table"><table><thead><tr>{['Measurement','Stage','Outcome','Calls','Mean seconds','P95 upper seconds','Max seconds'].map(name=><th key={name}>{name}</th>)}</tr></thead><tbody>{data.durations.map(row=><tr key={`${row.category}-${row.name}-${row.outcome}`}><td>{label(row.category)}</td><td>{row.name}</td><td>{label(row.outcome)}</td><td>{row.count}</td><td>{row.mean_seconds.toFixed(3)}</td><td>{row.p95_upper_seconds??'above range'}</td><td>{row.max_seconds.toFixed(3)}</td></tr>)}</tbody></table></div>
      <h2>Reported token usage</h2><p>Totals include only complete usage reported by the provider. Unknown usage is counted separately; it does not mean zero tokens or zero cost. These are token counts, not a billing estimate.</p>
      <div className="operations-table"><table><thead><tr>{['Agent','Reported calls','Unknown usage calls','Input tokens','Output tokens','Total tokens'].map(name=><th key={name}>{name}</th>)}</tr></thead><tbody>{Object.entries(data.tokens).map(([name,row])=><tr key={name}><td>{name}</td><td>{row.reported_calls}</td><td>{row.unknown_usage_calls}</td><td>{row.input_tokens}</td><td>{row.output_tokens}</td><td>{row.total_tokens}</td></tr>)}</tbody></table></div>
    </>}
  </div>
}
