import { useEffect,useRef } from 'react'
import type { TableOption } from './api'

function GroupCheckbox({ids,selected,onChange,label}:{ids:string[];selected:string[];onChange:(ids:string[])=>void;label:string}){
  const input=useRef<HTMLInputElement>(null)
  const count=ids.filter(id=>selected.includes(id)).length
  const mixed=count>0&&count<ids.length
  useEffect(()=>{if(input.current)input.current.indeterminate=mixed},[mixed])
  return <input ref={input} type="checkbox" aria-label={label} aria-checked={mixed?'mixed':count===ids.length} checked={count===ids.length} onChange={event=>{
    const next=new Set(selected)
    ids.forEach(id=>event.target.checked?next.add(id):next.delete(id))
    onChange([...next])
  }}/>
}

export default function TablePicker({available,options,selected,onChange}:{available:string[];options?:TableOption[];selected:string[];onChange:(ids:string[])=>void}){
  const permitted=new Set(available)
  const byId=new Map((options||[]).filter(option=>permitted.has(option.id)).map(option=>[option.id,option]))
  const choose=(id:string,checked:boolean)=>onChange(checked?[...new Set([...selected,id])]:selected.filter(value=>value!==id))
  const grouped=available.every(id=>byId.get(id)?.database&&byId.get(id)?.schema)
  if(!grouped)return <div className="confirmation-options">{available.map(id=><label key={id}><input type="checkbox" checked={selected.includes(id)} onChange={event=>choose(id,event.target.checked)}/>{id}</label>)}</div>
  const databases=new Map<string,Map<string,TableOption[]>>()
  available.forEach(id=>{
    const option=byId.get(id)!
    if(!databases.has(option.database!))databases.set(option.database!,new Map())
    const schemas=databases.get(option.database!)!
    if(!schemas.has(option.schema!))schemas.set(option.schema!,[])
    schemas.get(option.schema!)!.push(option)
  })
  return <div className="table-picker">
    <p aria-live="polite">{selected.filter(id=>permitted.has(id)).length} of {available.length} permitted tables selected. Database and schema checkboxes select only the tables shown below.</p>
    <div className="table-tree">{[...databases].sort(([a],[b])=>a.localeCompare(b)).map(([database,schemas])=>{
      const ids=[...schemas.values()].flat().map(option=>option.id)
      return <div className="table-tree-database" role="group" aria-label={`Database ${database}`} key={database}>
        <div className="table-tree-heading"><GroupCheckbox ids={ids} selected={selected} onChange={onChange} label={`Select permitted tables in database ${database}`}/><strong>{database}</strong><span>{ids.length} {ids.length===1?'table':'tables'}</span></div>
        {[...schemas].sort(([a],[b])=>a.localeCompare(b)).map(([schema,tables])=><div className="table-tree-schema" role="group" aria-label={`Schema ${database}.${schema}`} key={schema}>
          <div className="table-tree-heading"><GroupCheckbox ids={tables.map(table=>table.id)} selected={selected} onChange={onChange} label={`Select permitted tables in schema ${database}.${schema}`}/><strong>{schema}</strong></div>
          <div className="table-tree-leaves">{tables.sort((a,b)=>a.table.localeCompare(b.table)).map(table=><label key={table.id}>
            <input type="checkbox" aria-label={`${database}.${schema}.${table.table} (${table.id})`} checked={selected.includes(table.id)} onChange={event=>choose(table.id,event.target.checked)}/>
            <span>{table.table}<small>{table.id}</small></span>
          </label>)}</div>
        </div>)}
      </div>
    })}</div>
  </div>
}
