import {cleanup,render,screen,waitFor} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import Visualization,{type ChartPlan} from './Visualization'
import QueryResults from './QueryResults'
import type {QueryJob} from './api'

const result={columns:['region','total'],rows:[['North',10],['South',20]],returned_rows:2,truncated:false}
const job:QueryJob={id:'a'.repeat(32),question:'Regional totals',workspace:'auto',model:null,status:'completed',created_at:'',elapsed_seconds:1,events:[],error:null,
  planned_tables:[],planned_sql:null,planned_parameters:{},result:{status:'answered',answer:'Done',reason:'Compared rows',tables:[],
    queries:[{sql:'SELECT ...',parameters:{},result}],assumptions:[],workspaces:[],attempts:1,model_calls:6,example_ids:[],pruning:{}}}
const plan:ChartPlan={status:'chart',kind:'bar',title:'Regional totals',reason:'Compare the groups.',suggestions:[],warnings:[],
  figure:{data:[{type:'bar',x:['North','South'],y:[10,20]}],layout:{height:420}}}
const react=vi.fn(async()=>{}),purge=vi.fn()
beforeEach(()=>{react.mockClear();purge.mockClear();window.Plotly={react,purge,Plots:{resize:vi.fn(async()=>{})}}})
afterEach(()=>{cleanup();vi.unstubAllGlobals();delete window.Plotly})

it('requests a chart only when clicked, sends no cells, and renders it in the browser',async()=>{
  const fetch=vi.fn(async(_url:string,_init?:RequestInit)=>({ok:true,json:async()=>plan}));vi.stubGlobal('fetch',fetch)
  render(<Visualization job={job}/>);expect(fetch).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button',{name:'Visualize this result'}))
  await waitFor(()=>expect(react).toHaveBeenCalledOnce())
  expect(fetch.mock.calls[0][0]).toBe(`/api/queries/${job.id}/visualize`)
  expect(fetch.mock.calls[0][1]?.body).toBe('{}')
  expect((react.mock.calls[0] as unknown[])[3]).toMatchObject({modeBarButtonsToRemove:['toImage','sendDataToCloud','editInChartStudio']})
  await userEvent.click(screen.getByRole('button',{name:'Hide visualization'}))
  await userEvent.click(screen.getByRole('button',{name:'Show visualization'}))
  expect(fetch).toHaveBeenCalledOnce()
})

it('shows a precise KPI value without converting it to a browser number',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({...plan,kind:'kpi',figure:null,kpi:{label:'Total','value':'12345678901234567890.12345'}})})))
  render(<Visualization job={job}/>);await userEvent.click(screen.getByRole('button',{name:'Visualize this result'}))
  expect(await screen.findByText('12345678901234567890.12345')).toBeTruthy();expect(react).not.toHaveBeenCalled()
})

it('offers query improvements without automatically submitting or executing them',async()=>{
  const fetch=vi.fn(async()=>({ok:true,json:async()=>({...plan,status:'needs_data',kind:'none',figure:null,suggestions:['Add total sales grouped by month.']})}));vi.stubGlobal('fetch',fetch)
  const refine=vi.fn();render(<Visualization job={job} onRefine={refine}/>)
  await userEvent.click(screen.getByRole('button',{name:'Visualize this result'}))
  expect(await screen.findByText('Add total sales grouped by month.')).toBeTruthy();expect(refine).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button',{name:'Use suggestion in a new question'}))
  expect(refine).toHaveBeenCalledWith('Add total sales grouped by month.');expect(fetch).toHaveBeenCalledOnce()
})

it('keeps the table intact if the visualization provider fails',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:false,status:503,json:async()=>({detail:'Visualization provider unavailable'})})))
  render(<><QueryResults result={result}/><Visualization job={job}/></>)
  await userEvent.click(screen.getByRole('button',{name:'Visualize this result'}))
  expect((await screen.findByRole('alert')).textContent).toContain('Visualization provider unavailable')
  expect(screen.getByRole('table',{name:'Query results'})).toBeTruthy()
})

it('does not send model requests for history whose rows were omitted',()=>{
  const fetch=vi.fn();vi.stubGlobal('fetch',fetch)
  const saved={...job,result:{...job.result!,queries:[{sql:'SELECT ...',parameters:{},result:{...result,rows_retained:false}}]}}
  render(<Visualization job={saved}/>);expect(screen.queryByRole('button')).toBeNull();expect(fetch).not.toHaveBeenCalled()
  expect(screen.getByText(/Visualization needs the returned rows/)).toBeTruthy()
})

it('sends a static preference and displays the chosen Python library image',async()=>{
  const fetch=vi.fn(async(_url:string,_init?:RequestInit)=>({ok:true,json:async()=>({...plan,renderer:'seaborn',interactive:false,figure:null,
    image:{mime_type:'image/png',data:'iVBORw0KGgo='}})}));vi.stubGlobal('fetch',fetch)
  render(<Visualization job={job}/>)
  await userEvent.selectOptions(screen.getByLabelText('Chart presentation'),'static')
  await userEvent.click(screen.getByRole('button',{name:'Visualize this result'}))
  expect(await screen.findByRole('img',{name:'Regional totals'})).toBeTruthy()
  expect(fetch.mock.calls[0][1]?.body).toBe('{"presentation":"static"}')
  expect(screen.getByText(/bar · seaborn · static/)).toBeTruthy();expect(react).not.toHaveBeenCalled()
})
