import { cleanup,render,screen,waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach,describe,expect,it,vi } from 'vitest'
import { TableConfirmation } from './ReviewControls'
import type { QueryJob,TableOption } from './api'

afterEach(()=>{cleanup();vi.unstubAllGlobals()})
const options:TableOption[]=[
  {id:'crm_customers',database:'DatabaseA',schema:'dbo',table:'Customers'},
  {id:'archive_customers',database:'DatabaseA',schema:'archive',table:'Customers'},
  {id:'sales_orders',database:'DatabaseB',schema:'sales',table:'Orders'},
  {id:'forbidden',database:'PrivateDatabase',schema:'dbo',table:'Secrets'},
]
const job=(selected=['crm_customers'],tree=true):QueryJob=>({
  id:'a'.repeat(32),question:'Join customer and order records',workspace:'auto',source:'reporting',model:null,
  status:'awaiting_confirmation',created_at:'2026-11-15T00:00:00Z',elapsed_seconds:1,events:[],result:null,error:null,
  planned_tables:selected,planned_sql:null,planned_parameters:{},
  confirmation:{version:'v1',tables:selected,available_tables:['crm_customers','archive_customers','sales_orders'],
    table_options:tree?options:undefined,workspaces:['crm','sales'],reason:'Review the registered databases',timeout_seconds:600},
})

describe('Database and schema table selection',()=>{
  it('shows permitted physical identities and sends logical IDs for selected tables',async()=>{
    const fetch=vi.fn(async(_url:string,_options?:RequestInit)=>({ok:true,json:async()=>({})}));vi.stubGlobal('fetch',fetch)
    render(<TableConfirmation job={job()}/>)
    expect(screen.getByRole('group',{name:'Database DatabaseA'})).toBeTruthy()
    expect(screen.getByRole('group',{name:'Schema DatabaseB.sales'})).toBeTruthy()
    expect(screen.queryByText('PrivateDatabase')).toBeNull();expect(screen.queryByText('Secrets')).toBeNull()
    const suggested=screen.getByRole('checkbox',{name:'DatabaseA.dbo.Customers (crm_customers)'}) as HTMLInputElement
    expect(suggested.checked).toBe(true)
    await userEvent.click(screen.getByRole('checkbox',{name:'DatabaseB.sales.Orders (sales_orders)'}))
    await userEvent.click(screen.getByRole('button',{name:'Confirm tables and continue'}))
    await waitFor(()=>expect(fetch).toHaveBeenCalledOnce())
    const body=JSON.parse(String(fetch.mock.calls[0][1]?.body))
    expect(body).toEqual({version:'v1',decision:'confirm',tables:['crm_customers','sales_orders']})
  })

  it('supports mixed database selections and schema/database toggles without extra permissions',async()=>{
    render(<TableConfirmation job={job()}/>)
    const database=screen.getByRole('checkbox',{name:'Select permitted tables in database DatabaseA'}) as HTMLInputElement
    expect(database.indeterminate).toBe(true)
    await userEvent.click(database)
    expect((screen.getByRole('checkbox',{name:'DatabaseA.archive.Customers (archive_customers)'}) as HTMLInputElement).checked).toBe(true)
    await userEvent.click(screen.getByRole('checkbox',{name:'Select permitted tables in schema DatabaseA.dbo'}))
    expect((screen.getByRole('checkbox',{name:'DatabaseA.dbo.Customers (crm_customers)'}) as HTMLInputElement).checked).toBe(false)
    expect(database.indeterminate).toBe(true)
    expect((screen.getByRole('checkbox',{name:'DatabaseB.sales.Orders (sales_orders)'}) as HTMLInputElement).checked).toBe(false)
    expect(screen.getByText(/1 of 3 permitted tables selected/)).toBeTruthy()
  })

  it('retains flat selection for older reviews and ordinary single-source tables',async()=>{
    render(<TableConfirmation job={job([],false)}/>)
    expect(screen.queryByRole('group',{name:'Database DatabaseA'})).toBeNull()
    const button=screen.getByRole('button',{name:'Confirm tables and continue'}) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    await userEvent.click(screen.getByRole('checkbox',{name:'sales_orders'}))
    expect(button.disabled).toBe(false)
  })

  it('locks the tree after a confirmation has been submitted',async()=>{
    const fetch=vi.fn(async(_url:string,_options?:RequestInit)=>({ok:true,json:async()=>({})}));vi.stubGlobal('fetch',fetch)
    render(<TableConfirmation job={job()}/>)
    await userEvent.click(screen.getByRole('button',{name:'Confirm tables and continue'}))
    await screen.findByText('Your decision was received.')
    const leaf=screen.getByRole('checkbox',{name:'DatabaseB.sales.Orders (sales_orders)'}) as HTMLInputElement
    await userEvent.click(leaf)
    expect(leaf.checked).toBe(false);expect(fetch).toHaveBeenCalledOnce()
  })
})
