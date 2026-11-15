import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App, { QueryCard } from './App'
import Domains from './Domains'
import { displayValue,type AppConfig,type QueryJob } from './api'

const CONFIG: AppConfig = {
  app_name: 'QueryDesk', dataset: 'Retail sample', table_count: 19,
  model: { configured: true, default: 'openai/gpt-oss-20b', models: ['openai/gpt-oss-20b'] },
  workspaces: [{ id: 'sales', description: 'Sales' }],
  suggestions: [{ title: 'The big picture', question: 'How many orders are there?', workspace: 'sales', icon: 'trend' }],
}
const JOB: QueryJob = {
  id: 'a'.repeat(32), question: 'How many orders are there?', workspace: 'sales', model: null,
  status: 'completed', created_at: '2026-11-13T10:00:00Z', elapsed_seconds: 1.2, events: [],
  error: null, planned_tables: ['orders'], planned_sql: 'SELECT COUNT(*) FROM orders;', planned_parameters: {},
  result: { status: 'answered', answer: 'There are 25 orders.', reason: 'The query counted every row in orders, without a date or status filter.',
    tables: ['orders'], queries: [{ sql: 'SELECT COUNT(*) FROM orders;', parameters: {}, result: { columns: ['count'], rows: [[25]], returned_rows: 1, truncated: false } }],
    assumptions: [], workspaces: ['sales'], attempts: 1, model_calls: 6, example_ids: [], pruning: {} },
}

beforeEach(() => { localStorage.clear(); HTMLElement.prototype.scrollIntoView = vi.fn() })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Query workspace', () => {
  it('shows the approved encrypted-PII bounds and no-partial-result behavior',()=>{
    render(<QueryCard job={{...JOB,status:'awaiting_sql_approval',result:null,sql_approval:{version:'b'.repeat(32),fingerprint:'c'.repeat(64),sql:'SELECT email FROM customers',parameters:{},tables:['customers'],row_limit:100,execution_timeout_seconds:3,timeout_seconds:600,execution_mode:'materialized',read_scope:'Local PII preparation.',checks:'Metadata checks.',deadline:Date.now()/1000+600,pii_limits:{max_rows:100,max_copy_bytes:2000,max_input_bytes:4000,max_envelope_bytes:1000,max_cell_bytes:500,max_decrypt_cells:200,timeout_seconds:10}}}} retry={vi.fn()} disabled={true}/>)
    expect(screen.getByText('Local PII limits')).toBeTruthy()
    expect(screen.getByText(/Preparation: 100 rows/)).toBeTruthy()
    expect(screen.getByText(/Exceeding a limit stops this attempt without a partial result/)).toBeTruthy()
  })

  it('shows the approved selective-copy scope for local PII queries',()=>{
    render(<QueryCard job={{...JOB,status:'awaiting_sql_approval',result:null,sql_approval:{version:'b'.repeat(32),fingerprint:'c'.repeat(64),sql:'SELECT email FROM customers WHERE customer_id=:id',parameters:{id:1},tables:['customers'],row_limit:100,execution_timeout_seconds:3,timeout_seconds:600,execution_mode:'materialized',read_scope:'Selective PII copy.',checks:'Metadata checks.',deadline:Date.now()/1000+600,copy_scope:[{table:'customers',columns:['customer_id','email'],decrypt_columns:['email'],row_marker_only:false,source_sql:'SELECT customer_id,email FROM customers WHERE customer_id=:__copy_0',parameters:{__copy_0:1}}]}}} retry={vi.fn()} disabled={true}/>)
    expect(screen.getByText('Source copy scope')).toBeTruthy()
    expect(screen.getByText('customers · customer_id, email')).toBeTruthy()
    expect(screen.getByText('Read method: Bounded local PII processing.')).toBeTruthy()
    expect(screen.getByText('__copy_0 = 1')).toBeTruthy()
  })

  it('shows warehouse cost limits before SQL approval',()=>{
    render(<QueryCard job={{...JOB,status:'awaiting_sql_approval',result:null,sql_approval:{version:'b'.repeat(32),fingerprint:'c'.repeat(64),sql:'SELECT COUNT(*) FROM orders',parameters:{},tables:['orders'],row_limit:100,execution_timeout_seconds:30,timeout_seconds:600,read_scope:'BigQuery protected read.',checks:'Metadata checks.',deadline:Date.now()/1000+600,resource_limits:{maximum_bytes_billed:1000000000,location:'US'}}}} retry={vi.fn()} disabled={true}/>)
    expect(screen.getByText(/Warehouse limits:/).textContent).toContain((1000000000).toLocaleString())
    expect(screen.getByText(/Warehouse limits:/).textContent).toContain('location US')
  })

  it('discloses development transport before SQL approval',()=>{
    render(<QueryCard job={{...JOB,status:'awaiting_sql_approval',result:null,sql_approval:{version:'b'.repeat(32),fingerprint:'c'.repeat(64),sql:'SELECT COUNT(*) FROM orders',parameters:{},tables:['orders'],row_limit:100,execution_timeout_seconds:30,timeout_seconds:600,read_scope:'Development source.',checks:'Metadata checks.',deadline:Date.now()/1000+600,adapter_capabilities:{tls:{encryption_required:false,tls12_enforced:false,certificate_verification_when_encrypted:true,minimum_version:'driver_default'}}}}} retry={vi.fn()} disabled={true}/>)
    expect(screen.getByText(/unencrypted development connections permitted/)).toBeTruthy()
  })

  it('shows enforced row filters separately from the generated SQL',()=>{
    render(<QueryCard job={{...JOB,status:'awaiting_sql_approval',result:null,sql_approval:{version:'b'.repeat(32),fingerprint:'c'.repeat(64),sql:'SELECT COUNT(*) FROM orders',parameters:{},tables:['orders'],row_limit:100,execution_timeout_seconds:3,timeout_seconds:600,execution_mode:'direct',read_scope:'Protected local view.',checks:'Metadata checks.',deadline:Date.now()/1000+600,row_security:{enabled:true,admin_bypass:false,summary:'Only permitted regional data is included.',filters:[{table:'orders',mode:'filtered',sql:'region IN (:region_scope)',parameters:{region_scope:'South'}}]}}}} retry={vi.fn()} disabled={true}/>)
    expect(screen.getByText('Row-level access')).toBeTruthy()
    expect(screen.getByText('Only permitted regional data is included.')).toBeTruthy()
    expect(screen.getByText('region_scope = South')).toBeTruthy()
    expect(screen.getByText('orders · filtered')).toBeTruthy()
    expect(screen.getByText('Read method: Direct protected read.')).toBeTruthy()
  })

  it('restores a saved review without submitting SQL or an approval',async()=>{
    const restored={...JOB,status:'awaiting_confirmation',result:null}
    const fetchMock=vi.fn(async(_url:string,_options?:RequestInit)=>({ok:true,json:async()=>restored}))
    vi.stubGlobal('fetch',fetchMock)
    const onRestored=vi.fn()
    render(<QueryCard job={{...JOB,status:'recoverable',result:null,recovery_available:true}} retry={vi.fn()} disabled={false} onRestored={onRestored}/>)
    expect(screen.getByText('Restoring progress does not run SQL. Review and approve again before any data is read.')).toBeTruthy()
    expect(screen.queryByText('No answer was returned.')).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button',{name:'Restore review'}))
    await waitFor(()=>expect(onRestored).toHaveBeenCalledWith(restored))
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/queries/${JOB.id}/restore-review`)
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({})
  })

  it('keeps the restore control available when recovery is refused',async()=>{
    vi.stubGlobal('fetch',vi.fn(async()=>({ok:false,status:409,json:async()=>({detail:'The saved plan changed. Run the question again.'})})))
    const onRestored=vi.fn()
    const retry=vi.fn()
    render(<QueryCard job={{...JOB,status:'recoverable',result:null}} retry={retry} disabled={false} onRestored={onRestored}/>)
    await userEvent.click(screen.getByRole('button',{name:'Restore review'}))
    await screen.findByRole('alert')
    expect(onRestored).not.toHaveBeenCalled()
    expect((screen.getByRole('button',{name:'Restore review'}) as HTMLButtonElement).disabled).toBe(false)
    await userEvent.click(screen.getByRole('button',{name:'Run question again'}))
    expect(retry).toHaveBeenCalledOnce()
  })

  it('requires an explicit version-bound SQL decision before execution',async()=>{
    const fetchMock=vi.fn(async(_url:string,_options?:RequestInit)=>({ok:true,json:async()=>({})}))
    vi.stubGlobal('fetch',fetchMock)
    render(<QueryCard job={{...JOB,status:'awaiting_sql_approval',result:null,sql_approval:{version:'b'.repeat(32),fingerprint:'c'.repeat(64),sql:'SELECT COUNT(*) FROM orders;',parameters:{},tables:['orders'],row_limit:100,execution_timeout_seconds:3,timeout_seconds:600,read_scope:'Only confirmed tables are copied.',checks:'Approval permits data reads.',deadline:Date.now()/1000+600}}} retry={vi.fn()} disabled={true}/>)
    expect(screen.getByText('The query has not run. Review its tables, filters and bound values.')).toBeTruthy()
    expect(screen.queryByText('Executed against your database')).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button',{name:'Approve SQL and run'}))
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/queries/${JOB.id}/approve-sql`)
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({version:'b'.repeat(32),fingerprint:'c'.repeat(64),decision:'approve'})
    await screen.findByText('Your SQL decision was received.')
  })

  it('renders exact decimal strings without converting them to JavaScript numbers',()=>{
    expect(displayValue({$qd_type:'decimal',value:'9007199254740993.01000000'})).toBe('9007199254740993.01000000')
    expect(displayValue({$qd_type:'integer',value:'9223372036854775807'})).toBe('9223372036854775807')
    render(<QueryCard job={{...JOB,planned_parameters:{amount:{$qd_type:'decimal',value:'9007199254740993.01000000'}},result:null,status:'running'}} retry={vi.fn()} disabled={true}/>)
    expect(screen.getByText('amount = 9007199254740993.01000000')).toBeTruthy()
  })

  it('deletes history on the server only after explicit confirmation',async()=>{
    const fetchMock=vi.fn(async(url:string,_options?:RequestInit)=>({ok:true,json:async()=>url==='/api/config'?CONFIG:url==='/api/history/clear'?{deleted_count:1}:JOB}))
    vi.stubGlobal('fetch',fetchMock)
    const confirm=vi.spyOn(window,'confirm').mockReturnValue(false)
    render(<App/> )
    await screen.findByRole('button',{name:'Delete saved history'})
    await userEvent.click(screen.getByRole('button',{name:'Delete saved history'}))
    expect(fetchMock.mock.calls.some(([url])=>url==='/api/history/clear')).toBe(false)
    confirm.mockReturnValue(true)
    await userEvent.click(screen.getByRole('button',{name:'Delete saved history'}))
    await waitFor(()=>expect(fetchMock.mock.calls.some(([url])=>url==='/api/history/clear')).toBe(true))
    confirm.mockRestore()
  })

  it('shows model policy blocking separately from missing credentials',async()=>{
    vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({...CONFIG,model:{...CONFIG.model,policy:{allowed:false,message:'Administrator approval required.'}}})})))
    render(<App/> )
    await screen.findByText(/Model processing is blocked by policy/)
    expect(screen.queryByText(/Configure a model and API key/)).toBeNull()
  })
  it('lets a business user create a persisted relevance domain', async () => {
    const directory={domains:[],available_tables:['orders','customers']}
    const saved=vi.fn()
    const fetchMock=vi.fn(async (_url:string,options?:RequestInit)=>({ok:true,json:async()=>options?.method==='POST'?{revision:1}:directory}))
    vi.stubGlobal('fetch',fetchMock)
    render(<Domains source="sample" onSaved={saved} />)
    await waitFor(()=>expect(fetchMock).toHaveBeenCalledOnce())
    await userEvent.click(screen.getByRole('button',{name:'Create domain'}))
    await userEvent.type(screen.getByLabelText('Domain name'),'Customer sales')
    await userEvent.type(screen.getByLabelText('Business description'),'Purchases and customer orders')
    await userEvent.type(screen.getByLabelText('Business terms, separated by commas'),'buying, sales')
    await userEvent.click(screen.getByRole('checkbox',{name:'orders'}))
    await userEvent.click(screen.getByRole('button',{name:'Save domain'}))
    await screen.findByText('Domain saved. It will guide new questions.')
    const payload=JSON.parse(String(fetchMock.mock.calls.find(([,options])=>options?.method==='POST')?.[1]?.body))
    expect(payload).toMatchObject({name:'Customer sales',tables:['orders'],keywords:['buying','sales'],revision:0})
    expect(saved).toHaveBeenCalledOnce()
  })

  it('requires review and explicit saving of AI domain suggestions', async () => {
    const proposal={id:'sales',name:'Customer purchases',description:'Purchasing behavior',tables:['orders'],keywords:['buying']}
    const fetchMock=vi.fn(async (url:string,_options?:RequestInit)=>({ok:true,json:async()=>url.endsWith('/suggest')?{domains:[proposal],draft:true}:{domains:[],available_tables:['orders']}}))
    vi.stubGlobal('fetch',fetchMock)
    render(<Domains source="sample" onSaved={vi.fn()} />)
    await userEvent.click(screen.getByRole('button',{name:'Suggest domains with AI'}))
    await userEvent.click(await screen.findByRole('button',{name:'Review Customer purchases'}))
    expect((screen.getByLabelText('Domain name') as HTMLInputElement).value).toBe('Customer purchases')
    expect((screen.getByRole('checkbox',{name:'orders'}) as HTMLInputElement).checked).toBe(true)
    expect(fetchMock.mock.calls.filter(([,options])=>options?.method==='POST')).toHaveLength(1)
    expect(screen.getByText(/other permitted tables can still be proposed/i)).toBeTruthy()
  })

  it('switches data sources, tests connectivity and submits the selected source', async () => {
    const sources=[{id:'sample',name:'Retail sample',kind:'sqlite',configured:true},{id:'analytics',name:'Analytics',kind:'postgresql',configured:true}]
    const fetchMock=vi.fn(async (url:string,_options?:RequestInit) => ({ok:true,json:async()=>url==='/api/config' ? {...CONFIG,sources} : url==='/api/config?source=analytics' ? {...CONFIG,sources,source:'analytics',dataset:'Analytics',suggestions:[],workspaces:[{id:'database',description:'External source'}]} : url.endsWith('/test') ? {connected:true} : {...JOB,source:'analytics'}}))
    vi.stubGlobal('fetch',fetchMock)
    render(<App />)
    await screen.findByRole('option',{name:'Analytics · postgresql'})
    await userEvent.selectOptions(screen.getByLabelText('Data source'),'analytics')
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url==='/api/config?source=analytics')).toBe(true))
    await userEvent.click(screen.getByRole('button',{name:'Test connection'}))
    await screen.findByText('Connection verified')
    await userEvent.type(screen.getByLabelText('Ask your data a question'),'Count orders')
    await userEvent.click(screen.getByRole('button',{name:'Run question'}))
    await screen.findByText('There are 25 orders.')
    const payload=fetchMock.mock.calls.find(([url]) => url==='/api/queries')?.[1]
    expect(JSON.parse(String(payload?.body)).source).toBe('analytics')
    expect(fetchMock.mock.calls.some(([url]) => url==='/api/sources/analytics/test')).toBe(true)
  })

  it('requires a table decision before continuation and supports table edits', async () => {
    const fetchMock=vi.fn(async (_url:string,_options?:RequestInit) => ({ok:true,json:async()=>({})}))
    vi.stubGlobal('fetch',fetchMock)
    render(<QueryCard job={{...JOB,status:'awaiting_confirmation',result:null,planned_sql:null,
      confirmation:{version:'c'.repeat(32),tables:['orders'],available_tables:['orders','customers'],workspaces:['sales'],reason:'Count order records',timeout_seconds:600}}} retry={vi.fn()} disabled={true} />)
    expect(screen.queryByText('SELECT COUNT(*) FROM orders;')).toBeNull()
    const confirm=screen.getByRole('button',{name:'Confirm tables and continue'})
    await userEvent.click(screen.getByRole('checkbox',{name:'orders'}))
    expect((confirm as HTMLButtonElement).disabled).toBe(true)
    await userEvent.click(screen.getByRole('checkbox',{name:'customers'}))
    await userEvent.click(confirm)
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({version:'c'.repeat(32),decision:'confirm',tables:['customers']})
    await screen.findByText('Your decision was received.')
  })

  it('saves feedback with the query reference and shows review status', async () => {
    const fetchMock=vi.fn(async (_url:string,_options?:RequestInit) => ({ok:true,json:async()=>({review_status:'pending'})}))
    vi.stubGlobal('fetch',fetchMock)
    render(<QueryCard job={JOB} retry={vi.fn()} disabled={false} />)
    await userEvent.click(screen.getByRole('button',{name:'Give feedback'}))
    await userEvent.selectOptions(screen.getByLabelText('Was this useful?'),'needs_work')
    await userEvent.selectOptions(screen.getByLabelText('Feedback about'),'tables')
    await userEvent.type(screen.getByLabelText('Comments or suggested correction'),'Check the customer join.')
    await userEvent.click(screen.getByRole('button',{name:'Save feedback'}))
    await screen.findByText(/Feedback saved for review/)
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/queries/${JOB.id}/feedback`)
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({rating:'needs_work',category:'tables',comment:'Check the customer join.'})
  })

  it('shows an answer, reason, tables and SQL without download/export controls', () => {
    render(<QueryCard job={JOB} retry={vi.fn()} disabled={false} />)
    expect(screen.getByText('There are 25 orders.')).toBeTruthy()
    expect(screen.getByText('Why this answer')).toBeTruthy()
    expect(screen.getByText(/counted every row/)).toBeTruthy()
    expect(screen.getByText('orders', { selector: '.table-tags span' })).toBeTruthy()
    expect(screen.getByText('SELECT COUNT(*) FROM orders;')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /download|export/i })).toBeNull()
  })

  it('displays tables and prepared SQL while the workflow is still running', () => {
    render(<QueryCard job={{ ...JOB, status: 'running', result: null, events: [{ stage: 'validation', status: 'running', label: 'Checking the query', at: '' }] }} retry={vi.fn()} disabled={true} />)
    expect(screen.getByText('Selected tables')).toBeTruthy()
    expect(screen.getByText('Prepared · checked before execution')).toBeTruthy()
    expect(screen.getByText('Checking the query')).toBeTruthy()
  })

  it('reports rate limits and retries only after an explicit click', async () => {
    const retry = vi.fn()
    render(<QueryCard job={{ ...JOB, status: 'failed', result: null, error: 'Model provider rate limit reached (HTTP 429).' }} retry={retry} disabled={false} />)
    expect(screen.getByText(/HTTP 429/)).toBeTruthy()
    expect(retry).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Retry question' }))
    expect(retry).toHaveBeenCalledOnce()
  })

  it('uses examples as drafts and submits only the new question and preferences', async () => {
    const fetchMock = vi.fn(async (url: string, options?: RequestInit) => ({ ok: true, json: async () => url === '/api/config' ? CONFIG : url === '/api/queries' ? JOB : JOB }))
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: /The big picture/ }))
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/queries')).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: 'Run question' }))
    await screen.findByText('There are 25 orders.')
    const options = fetchMock.mock.calls.find(([url]) => url === '/api/queries')?.[1]
    expect(JSON.parse(String(options?.body))).toEqual({ question: 'How many orders are there?', workspace: 'sales', model: null, source: 'sample' })
  })

  it('shows service errors with a working reconnect action', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Service unavailable')))
    render(<App />)
    await screen.findByText(/The data service is unavailable/)
    await userEvent.click(screen.getByRole('button', { name: 'Reconnect' }))
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
  })
})
