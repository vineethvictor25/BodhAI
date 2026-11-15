import {cleanup,render,screen,waitFor} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {afterEach,expect,it,vi} from 'vitest'
import Operations,{type OperationsData} from './Operations'
import App from './App'
import type {QueryJob,AppConfig} from './api'

afterEach(()=>{cleanup();vi.unstubAllGlobals();localStorage.clear()})

it('shows queue, latency, provider failures and reported versus unknown token usage',async()=>{
  const metrics:OperationsData={scope:'single_api_process_since_start',started_at:'2026-11-15T00:00:00Z',uptime_seconds:42,
    counters:{submitted:8,idempotency_replayed:2},queue:{queued:3,running:2,workers:2},
    durations:[{category:'provider',name:'intent',outcome:'rate_limit',count:1,mean_seconds:.1,max_seconds:.1,p95_upper_seconds:.1}],
    tokens:{intent:{reported_calls:4,unknown_usage_calls:1,input_tokens:100,output_tokens:50,total_tokens:150}}}
  const fetch=vi.fn(async()=>({ok:true,json:async()=>metrics}));vi.stubGlobal('fetch',fetch)
  render(<Operations/>)
  expect(await screen.findByText('Reported token usage')).toBeTruthy()
  expect(screen.getByText('rate limit')).toBeTruthy();expect(screen.getByText('150')).toBeTruthy()
  expect(screen.getByText(/Unknown usage is counted separately/)).toBeTruthy()
  expect(screen.getByText('Queue and capacity')).toBeTruthy()
  expect(fetch.mock.calls.length).toBe(1)
})

it('shows an operations request failure without inventing empty metrics',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:false,status:503,json:async()=>({detail:'Temporarily unavailable'})})))
  render(<Operations/>);expect((await screen.findByRole('alert')).textContent).toContain('Temporarily unavailable')
  expect(screen.queryByText('Queue and capacity')).toBeNull()
})

it('reuses the submission key after response loss and changes it for a new successful run',async()=>{
  Object.defineProperty(HTMLElement.prototype,'scrollIntoView',{configurable:true,value:vi.fn()})
  const config:AppConfig={app_name:'QueryDesk',dataset:'Sample',table_count:1,model:{configured:true,default:'test',models:['test']},
    workspaces:[{id:'sales',description:'Orders'}],suggestions:[]}
  const job:QueryJob={id:'a'.repeat(32),question:'Count orders',workspace:'auto',model:null,source:'sample',status:'completed',
    created_at:'',elapsed_seconds:1,events:[],error:null,planned_tables:[],planned_sql:null,planned_parameters:{},
    result:{status:'answered',answer:'Done',reason:'Counted rows',tables:[],queries:[],assumptions:[],workspaces:[],attempts:1,model_calls:1,example_ids:[],pruning:{}}}
  const submissions:RequestInit[]=[]
  vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
    if(url==='/api/config')return {ok:true,json:async()=>config}
    if(url==='/api/queries'){
      submissions.push(init!)
      if(submissions.length===1)throw new TypeError('Simulated response loss')
      return {ok:true,json:async()=>({...job,id:submissions.length===2?job.id:'b'.repeat(32)})}
    }
    throw new Error('Unexpected request')
  }))
  render(<App/>);await screen.findByText('Sample')
  await userEvent.type(screen.getByLabelText('Ask your data a question'),'Count orders')
  await userEvent.click(screen.getByRole('button',{name:'Run question'}))
  await screen.findByText('Simulated response loss')
  await userEvent.click(screen.getByRole('button',{name:'Run question'}))
  await waitFor(()=>expect(submissions.length).toBe(2))
  await screen.findByText('Done')
  const key=(item:RequestInit)=>new Headers(item.headers).get('Idempotency-Key')
  expect(key(submissions[0])).toBeTruthy();expect(key(submissions[0])).toBe(key(submissions[1]))
  await userEvent.type(screen.getByLabelText('Ask your data a question'),'Count orders')
  await userEvent.click(screen.getByRole('button',{name:'Run question'}))
  await waitFor(()=>expect(submissions.length).toBe(3))
  expect(key(submissions[2])).not.toBe(key(submissions[1]))
})
