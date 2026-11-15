import {useEffect,useRef,useState} from 'react'
import {request,type QueryJob} from './api'

type Figure={data:Record<string,unknown>[];layout:Record<string,unknown>}
export type ChartPlan={status:'chart'|'needs_data';kind:'bar'|'line'|'scatter'|'kpi'|'none';title:string;reason:string;
  library?:string;renderer?:string;interactive?:boolean;image?:{mime_type:'image/png';data:string};
  suggestions:string[];warnings:string[];figure?:Figure|null;kpi?:{label:string;value:string}|null}
type PlotlyRuntime={react:(element:HTMLElement,data:unknown[],layout:Record<string,unknown>,config:Record<string,unknown>)=>Promise<void>;purge:(element:HTMLElement)=>void;Plots:{resize:(element:HTMLElement)=>Promise<void>}}
declare global {interface Window {Plotly?:PlotlyRuntime}}
let loading:Promise<PlotlyRuntime>|undefined
export function loadPlotly():Promise<PlotlyRuntime>{
  if(window.Plotly)return Promise.resolve(window.Plotly)
  if(loading)return loading
  loading=new Promise((resolve,reject)=>{
    const script=document.createElement('script');script.src='/api/assets/plotly.js';script.async=true
    script.onload=()=>{if(window.Plotly)resolve(window.Plotly);else{loading=undefined;script.remove();reject(new Error('The chart renderer could not initialize.'))}}
    script.onerror=()=>{loading=undefined;script.remove();reject(new Error('The local chart renderer could not load. Check your session and refresh.'))}
    document.head.appendChild(script)
  })
  return loading
}

function Plot({figure,title}:{figure:Figure;title:string}){
  const element=useRef<HTMLDivElement>(null)
  const [error,setError]=useState('')
  useEffect(()=>{
    let alive=true;let observer:ResizeObserver|undefined
    const target=element.current!
    void loadPlotly().then(async plotly=>{
      if(!alive)return
      await plotly.react(target,figure.data,{...figure.layout,autosize:true},{responsive:true,displaylogo:false,scrollZoom:false,
        modeBarButtonsToRemove:['toImage','sendDataToCloud','editInChartStudio']})
      if(alive&&typeof ResizeObserver!=='undefined'){
        observer=new ResizeObserver(()=>{void plotly.Plots.resize(target).catch(()=>{})});observer.observe(target)
      }
      if(!alive)plotly.purge(target)
    }).catch(e=>{if(alive)setError((e as Error).message)})
    return()=>{alive=false;observer?.disconnect();window.Plotly?.purge(target)}
  },[figure])
  return <>{error&&<p role="alert">{error} The result table is still available.</p>}<div ref={element} className="interactive-chart" role="img" aria-label={title}/></>
}

export default function Visualization({job,onRefine,disabled=false}:{job:QueryJob;onRefine?:(suggestion:string)=>void;disabled?:boolean}){
  const [plan,setPlan]=useState<ChartPlan|null>(null)
  const [busy,setBusy]=useState(false)
  const [visible,setVisible]=useState(true)
  const [presentation,setPresentation]=useState<'auto'|'interactive'|'static'>('auto')
  const [error,setError]=useState('')
  const mounted=useRef(true)
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[])
  const result=job.result?.queries.at(-1)?.result
  if(!result)return null
  if(result.rows_retained===false)return <p className="visualization-note">Visualization needs the returned rows. Run the question again and approve its SQL to obtain fresh chart data.</p>
  async function visualize(){
    if(plan){setVisible(value=>!value);return}
    setBusy(true);setError('')
    try{const next=await request<ChartPlan>(`/api/queries/${job.id}/visualize`,{method:'POST',body:presentation==='auto'?'{}':JSON.stringify({presentation})});if(mounted.current){setPlan(next);setVisible(true)}}
    catch(e){if(mounted.current)setError((e as Error).message)}finally{if(mounted.current)setBusy(false)}
  }
  return <section className="visualization-panel" aria-label="Result visualization">
    <div className="visualization-heading"><div><h3>See the result visually</h3><p>The Visualization Agent recommends a chart from the result structure. No SQL is rerun.</p></div>
      <label>Chart presentation<select aria-label="Chart presentation" value={presentation} disabled={busy} onChange={event=>{setPresentation(event.target.value as 'auto'|'interactive'|'static');setPlan(null)}}><option value="auto">Let AI choose</option><option value="interactive">Interactive</option><option value="static">Static</option></select></label>
      <button className="visualize-button" disabled={busy} onClick={()=>void visualize()}>{busy?'Choosing a visualization…':plan?(visible?'Hide visualization':'Show visualization'):'Visualize this result'}</button></div>
    {error&&<p role="alert">{error}</p>}
    {plan&&visible&&<div className="visualization-content"><span className="eyebrow">{plan.status==='chart'?`${plan.kind} · ${plan.renderer||plan.library||'AI recommended'} · ${plan.interactive?'interactive':'static'}`:'A better chart starts with suitable data'}</span><h3>{plan.title}</h3><p>{plan.reason}</p>
      {plan.status==='chart'&&plan.kind==='kpi'&&plan.kpi&&<div className="visualization-kpi"><span>{plan.kpi.label}</span><strong>{plan.kpi.value}</strong></div>}
      {plan.status==='chart'&&plan.figure&&<Plot figure={plan.figure} title={plan.title}/>}
      {plan.status==='chart'&&plan.image?.mime_type==='image/png'&&plan.image.data.length<=262144&&/^[A-Za-z0-9+/=]+$/.test(plan.image.data)&&<img className="static-chart" src={`data:image/png;base64,${plan.image.data}`} alt={plan.title}/>}
      {plan.warnings.map((warning,index)=><p className="visualization-note" key={index}>{warning}</p>)}
      {plan.suggestions.length>0&&<div className="chart-suggestions"><h4>Suggested query improvements</h4><p>These are AI drafts. Confirm that proposed fields exist, then review tables and SQL again. Nothing runs automatically.</p><ul>{plan.suggestions.map((suggestion,index)=><li key={index}><span>{suggestion}</span>{onRefine&&<button className="text-button" disabled={disabled} onClick={()=>onRefine(suggestion)}>Use suggestion in a new question</button>}</li>)}</ul></div>}
    </div>}
  </section>
}
