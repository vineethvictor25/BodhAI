import {cleanup,render,screen,waitFor} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {afterEach,expect,it,vi} from 'vitest'
import KnowledgeWorkspace from './KnowledgeWorkspace'

afterEach(()=>{cleanup();vi.unstubAllGlobals()})
const definition={kind:'concept',title:'Order lifecycle',description:'Accepted order lifecycle terms.',aliases:[],columns:{},unit:'',grain:'',date_basis:'',filters:[],relationship:null,sql:'',parameters:[],questions:['Which order statuses are eligible?']}
function directory(role='owner'){
  return {domains:[{id:'sales',name:'Sales',role,tables:['orders']}],entries:[],assignments:[],users:[],can_assign:role==='admin',schema:{tables:[{table:'orders',columns:[{name:'order_id',type:'INTEGER',nullable:false,primary_key:true}],foreign_keys:[]}]}}
}
function mockApi(data:object,handler?:(url:string,body:unknown)=>object){
  const fetch=vi.fn(async(url:string,init?:RequestInit)=>({ok:true,json:async()=>init?.method==='POST'?(handler?.(url,JSON.parse(String(init.body)))||{}):data}))
  vi.stubGlobal('fetch',fetch);return fetch
}

it('readers see published knowledge without authoring or assignment controls',async()=>{
  mockApi(directory('reader'));render(<KnowledgeWorkspace source="sample" onPublished={()=>{}}/>)
  await screen.findByText(/Your knowledge role/)
  expect(screen.queryByRole('button',{name:'Create definition'})).toBeNull()
  expect(screen.queryByText('Assign knowledge owners and reviewers')).toBeNull()
  expect(screen.getByText(/No visible definitions/)).toBeTruthy()
})

it('owners create drafts rather than publishing through the save action',async()=>{
  const fetch=mockApi(directory());render(<KnowledgeWorkspace source="sample" onPublished={()=>{}}/>)
  await userEvent.click(await screen.findByRole('button',{name:'Create definition'}))
  await userEvent.type(screen.getByLabelText('Name'),'Order lifecycle')
  await userEvent.type(screen.getByLabelText('Business definition'),'Accepted order states.')
  await userEvent.click(screen.getByRole('button',{name:'Save draft'}))
  await screen.findByRole('status')
  const calls=fetch.mock.calls.filter(([,init])=>init?.method==='POST')
  expect(calls).toHaveLength(1);expect(calls[0][0]).toContain('/knowledge/entries')
  expect(JSON.parse(String(calls[0][1]?.body))).toMatchObject({domain:'sales',expected_revision:0,content:{kind:'concept',title:'Order lifecycle'}})
})

it('publication requires explicit review evidence and confirmation',async()=>{
  const entry={domain:'sales',id:'order_lifecycle',revision:1,head_revision:1,published_revision:null,author:'other',state:'in_review',content:definition}
  const data={...directory('reviewer'),entries:[entry]};const fetch=mockApi(data);const updated=vi.fn()
  render(<KnowledgeWorkspace source="sample" onPublished={updated}/>)
  await userEvent.click(await screen.findByText('Version history and review actions'))
  const button=screen.getByRole('button',{name:'Publish revision'}) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  await userEvent.type(screen.getByLabelText('Review evidence, independent checks and limitations'),'Compared with business requirements.')
  await userEvent.click(screen.getByRole('checkbox',{name:/I reviewed the business meaning/}))
  expect(button.disabled).toBe(false);await userEvent.click(button)
  await waitFor(()=>expect(updated).toHaveBeenCalledOnce())
  const call=fetch.mock.calls.find(([,init])=>init?.method==='POST')!
  expect(JSON.parse(String(call[1]?.body))).toMatchObject({revision:1,action:'publish',confirm_business_review:true,review_note:'Compared with business requirements.'})
})

it('AI suggestions are drafts and reviewing one does not save it',async()=>{
  const fetch=mockApi(directory(),()=>({entries:[definition],draft:true,saved:false}))
  render(<KnowledgeWorkspace source="sample" onPublished={()=>{}}/>)
  await userEvent.click(await screen.findByText('Draft suggestions with AI'))
  await userEvent.type(screen.getByLabelText('What should this domain explain?'),'Explain order status')
  await userEvent.click(screen.getByRole('button',{name:'Suggest draft knowledge'}))
  await userEvent.click(await screen.findByRole('button',{name:'Review suggestion'}))
  expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Order lifecycle')
  expect(fetch.mock.calls.filter(([,init])=>init?.method==='POST')).toHaveLength(1)
})

it('JSON imports are server-validated previews and cannot import approval state',async()=>{
  const fetch=mockApi(directory(),()=>({content:definition,saved:false}))
  render(<KnowledgeWorkspace source="sample" onPublished={()=>{}}/>)
  await userEvent.click(await screen.findByText('Import a definition from JSON'))
  await userEvent.paste('')
  const box=screen.getByLabelText('Knowledge JSON')
  await userEvent.click(box);await userEvent.paste(JSON.stringify({content:definition,published:true}))
  await userEvent.click(screen.getByRole('button',{name:'Review imported definition'}))
  await screen.findByRole('form',{name:'Knowledge definition'})
  const call=fetch.mock.calls.find(([,init])=>init?.method==='POST')!
  expect(call[0]).toContain('/knowledge/preview')
  expect(JSON.parse(String(call[1]?.body))).not.toHaveProperty('published')
  expect(fetch.mock.calls.filter(([,init])=>init?.method==='POST')).toHaveLength(1)
})
