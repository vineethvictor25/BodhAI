import { cleanup,render,screen,waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach,beforeEach,expect,it,vi } from 'vitest'
import AuthenticatedApp from './Auth'
import AccessAdmin from './AccessAdmin'
import { setCsrfToken,type Account } from './api'

const PASSWORD='Only-for-local-ui-tests-7843!'
const ADMIN:Account={id:'a'.repeat(32),username:'admin',role:'admin',pii:1,enabled:1,revision:1,can_view_pii:true,groups:[],grants:[],direct_grants:[]}
const MEMBER:Account={...ADMIN,id:'b'.repeat(32),username:'analyst',role:'member',pii:0,can_view_pii:false,groups:['business_users'],grants:[{source:'sample',domain:'sales'}],direct_grants:[{source:'sample',domain:'sales'}]}
const config={app_name:'QueryDesk',dataset:'Retail sample',table_count:19,model:{configured:true,default:'fixture',models:['fixture']},workspaces:[{id:'sales',name:'Sales',description:'Orders'}],suggestions:[],sources:[{id:'sample',name:'Retail sample',kind:'sqlite',configured:true}]}
beforeEach(()=>{localStorage.clear();setCsrfToken('');HTMLElement.prototype.scrollIntoView=vi.fn()})
afterEach(()=>{cleanup();vi.unstubAllGlobals();setCsrfToken('')})

it('shows first administrator setup without fetching business data before authentication',async()=>{
  const fetchMock=vi.fn(async(url:string,options?:RequestInit)=>({ok:true,json:async()=>url==='/api/auth/status'?{setup_required:true}:url==='/api/auth/setup'?{user:ADMIN,csrf_token:'csrf',access_stamp:'1:1'}:config}))
  vi.stubGlobal('fetch',fetchMock)
  render(<AuthenticatedApp/> )
  await screen.findByRole('heading',{name:'Create the administrator'})
  expect(fetchMock.mock.calls.map(([url])=>url)).toEqual(['/api/auth/status'])
  await userEvent.type(screen.getByLabelText('Account name'),'admin')
  await userEvent.type(screen.getByLabelText('Password'),PASSWORD)
  await userEvent.type(screen.getByLabelText('Administrator setup token'),'setup-token-for-test-is-long-enough')
  await userEvent.click(screen.getByRole('button',{name:'Create administrator'}))
  await screen.findByRole('button',{name:'Access and security'})
  const call=fetchMock.mock.calls.find(([url])=>url==='/api/auth/setup')
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({username:'admin',password:PASSWORD,setup_token:'setup-token-for-test-is-long-enough'})
  expect(JSON.stringify(localStorage)).not.toContain(PASSWORD)
})

it('self-onboarding chooses exactly one domain and does not expose administrator controls',async()=>{
  const fetchMock=vi.fn(async(url:string,_options?:RequestInit)=>({ok:url!=='/api/auth/me',status:url==='/api/auth/me'?401:200,json:async()=>url==='/api/auth/status'?{setup_required:false}:url==='/api/auth/me'?{detail:'Sign in'}:url.startsWith('/api/auth/onboarding-options')?{sources:[{id:'sample',name:'Retail sample'}],domains:[{id:'sales',name:'Sales',description:'Orders'},{id:'inventory',name:'Inventory',description:'Stock'}]}:url==='/api/auth/register'?{user:MEMBER,csrf_token:'csrf',access_stamp:'1:1'}:config}))
  vi.stubGlobal('fetch',fetchMock)
  render(<AuthenticatedApp/> )
  await userEvent.click(await screen.findByRole('button',{name:'New here? Create an account'}))
  await userEvent.type(screen.getByLabelText('Account name'),'analyst')
  await userEvent.type(screen.getByLabelText('Password'),PASSWORD)
  await screen.findByRole('option',{name:'Sales'})
  await userEvent.selectOptions(screen.getByLabelText('Starting business domain'),'sales')
  await userEvent.click(screen.getByRole('button',{name:'Create restricted account'}))
  await screen.findByRole('button',{name:'Sign out'})
  expect(screen.queryByRole('button',{name:'Access and security'})).toBeNull()
  expect(screen.queryByRole('button',{name:'Business domains'})).toBeNull()
  const call=fetchMock.mock.calls.find(([url])=>url==='/api/auth/register')
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({username:'analyst',password:PASSWORD,source:'sample',domain:'sales'})
})

it('administrator access editor submits member permissions and revision',async()=>{
  const directory={users:[ADMIN,MEMBER],groups:[{id:'business_users',name:'Business Users',pii:'inherit',grants:[],revision:1}],pii_encryption_enabled:false,row_policy_support:true,row_policy_enabled:false}
  const fetchMock=vi.fn(async(url:string,_options?:RequestInit)=>({ok:true,json:async()=>url==='/api/admin/access'?directory:url==='/api/sources'?{sources:config.sources}:url.endsWith('/domains')?{domains:[{id:'sales',name:'Sales'},{id:'payments',name:'Payments'}]}:{saved:true}}))
  vi.stubGlobal('fetch',fetchMock)
  render(<AccessAdmin/> )
  await userEvent.click(await screen.findByRole('button',{name:'Manage analyst'}))
  await userEvent.click(screen.getByRole('checkbox',{name:/Direct permission to view decrypted PII/}))
  await userEvent.click(await screen.findByRole('checkbox',{name:'Retail sample · Payments'}))
  await userEvent.click(screen.getByRole('button',{name:'Save member access'}))
  await screen.findByText(/Member access saved/)
  const call=fetchMock.mock.calls.find(([url])=>url.includes('/users/'))
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({pii:true,revision:1,grants:[{source:'sample',domain:'sales'},{source:'sample',domain:'payments'}]})
})

it('group policies expose allow/deny/inherit without pretending row restrictions are active',async()=>{
  const fetchMock=vi.fn(async(url:string,_options?:RequestInit)=>({ok:true,json:async()=>url==='/api/admin/access'?{users:[],groups:[{id:'pii_restricted',name:'PII Restricted',pii:'deny',grants:[],revision:1}],pii_encryption_enabled:true}:url==='/api/sources'?{sources:[]}:{saved:true}}))
  vi.stubGlobal('fetch',fetchMock)
  render(<AccessAdmin/> )
  await userEvent.click(await screen.findByRole('button',{name:'Edit PII Restricted'}))
  expect((screen.getByLabelText('PII policy') as HTMLSelectElement).value).toBe('deny')
  expect(screen.getByText(/Row-level access: disabled/)).toBeTruthy()
  expect(screen.getByText(/maintained in the server policy file/)).toBeTruthy()
  await userEvent.click(screen.getByRole('button',{name:'Save group policy'}))
  await waitFor(()=>expect(fetchMock.mock.calls.some(([url])=>url==='/api/admin/groups')).toBe(true))
})
