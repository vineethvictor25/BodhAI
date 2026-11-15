import {cleanup,render,screen,waitFor} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {afterEach,expect,it,vi} from 'vitest'
import {QueryActions,SQLApproval} from './ReviewControls'
import type {QueryJob} from './api'

afterEach(()=>{cleanup();vi.unstubAllGlobals()})
const job:QueryJob={id:'a'.repeat(32),question:'Count orders',workspace:'auto',model:null,status:'awaiting_sql_approval',
  created_at:'',elapsed_seconds:1,events:[],result:null,error:null,planned_tables:['orders'],planned_sql:'SELECT COUNT(*) FROM orders',planned_parameters:{},
  sql_approval:{version:'b'.repeat(32),fingerprint:'c'.repeat(64),sql:'SELECT COUNT(*) FROM orders',parameters:{},tables:['orders'],row_limit:100,execution_timeout_seconds:3,timeout_seconds:600,read_scope:'Permitted rows',checks:'Read only',deadline:2000000000}}

it('cancels the old query before restoring the question for editing',async()=>{
  const events:string[]=[]
  const fetch=vi.fn(async()=>{events.push('cancel');return {ok:true,json:async()=>({...job,cancellation_requested:true,status:'running'})}})
  vi.stubGlobal('fetch',fetch)
  render(<QueryActions job={{...job,status:'running'}} onUpdated={()=>events.push('updated')} onEdit={()=>events.push('edit')}/>)
  await userEvent.click(screen.getByRole('button',{name:'Stop and edit question'}))
  await waitFor(()=>expect(events).toEqual(['cancel','updated','edit']))
})

it('does not restore question when cancellation request fails',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:false,status:503,json:async()=>({detail:'Try later'})})))
  const edit=vi.fn();render(<QueryActions job={job} onEdit={edit}/>)
  await userEvent.click(screen.getByRole('button',{name:'Stop and edit question'}))
  expect(await screen.findByRole('alert')).toBeTruthy();expect(edit).not.toHaveBeenCalled()
})

it('validates edited SQL and never sends the old approval',async()=>{
  const fetch=vi.fn(async(_url:string,_options?:RequestInit)=>({ok:true,json:async()=>({})}));vi.stubGlobal('fetch',fetch)
  render(<SQLApproval job={job}/>)
  await userEvent.click(screen.getByRole('button',{name:'Edit SQL'}))
  expect((screen.getByRole('button',{name:'Approve SQL and run'}) as HTMLButtonElement).disabled).toBe(true)
  await userEvent.clear(screen.getByLabelText('SQL to validate'))
  await userEvent.type(screen.getByLabelText('SQL to validate'),'SELECT COUNT(*) AS n FROM orders')
  await userEvent.click(screen.getByRole('button',{name:'Validate edited SQL'}))
  await waitFor(()=>expect(fetch).toHaveBeenCalledOnce())
  expect(fetch.mock.calls[0][0]).toContain('/revise-sql')
  expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toMatchObject({sql:'SELECT COUNT(*) AS n FROM orders',version:job.sql_approval!.version,parameters:[]})
  expect((screen.getByRole('button',{name:'Validate edited SQL'}) as HTMLButtonElement).disabled).toBe(true)
})

it('reports invalid bound value JSON without sending SQL',async()=>{
  const fetch=vi.fn();vi.stubGlobal('fetch',fetch);render(<SQLApproval job={job}/>)
  await userEvent.click(screen.getByRole('button',{name:'Edit SQL'}))
  await userEvent.clear(screen.getByLabelText('Bound values (JSON list)'))
  await userEvent.type(screen.getByLabelText('Bound values (JSON list)'),'invalid-json')
  await userEvent.click(screen.getByRole('button',{name:'Validate edited SQL'}))
  expect(await screen.findByRole('alert')).toBeTruthy();expect(fetch).not.toHaveBeenCalled()
})
