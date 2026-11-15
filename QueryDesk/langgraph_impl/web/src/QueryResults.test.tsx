import { cleanup,render,screen,within } from '@testing-library/react'
import { afterEach,describe,expect,it } from 'vitest'
import QueryResults from './QueryResults'

afterEach(cleanup)

describe('Query result tables',()=>{
  it('shows database headers and exact values as text without interpreting cell HTML',()=>{
    render(<QueryResults result={{columns:['Segment','Amount','Customer ID','Note'],rows:[['VIP',{$qd_type:'decimal',value:'12345678901234567890.00100'},{$qd_type:'integer',value:'9007199254740993'},'<img src=x onerror=alert(1)>']],returned_rows:1,truncated:false}}/>)
    const table=screen.getByRole('table',{name:'Query results'})
    expect(within(table).getByRole('columnheader',{name:'Amount'})).toBeTruthy()
    expect(within(table).getByRole('cell',{name:'12345678901234567890.00100'})).toBeTruthy()
    expect(within(table).getByRole('cell',{name:'9007199254740993'})).toBeTruthy()
    expect(within(table).getByText('<img src=x onerror=alert(1)>')).toBeTruthy()
    expect(table.querySelector('img')).toBeNull()
  })

  it('distinguishes an empty result from missing saved rows',()=>{
    render(<QueryResults result={{columns:['region'],rows:[],returned_rows:0,truncated:false}}/>)
    expect(screen.getByText('No matching rows.')).toBeTruthy()
    expect(screen.getByRole('columnheader',{name:'region'})).toBeTruthy()
    expect(screen.queryByText(/not retained/)).toBeNull()
  })

  it('does not display cells when durable history omitted raw rows',()=>{
    render(<QueryResults result={{columns:['email'],rows:[['should-not-be-shown']],returned_rows:1,truncated:false,rows_retained:false}}/>)
    expect(screen.getByText(/Result rows were not retained in saved history/)).toBeTruthy()
    expect(screen.queryByText('should-not-be-shown')).toBeNull()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('discloses limited results and displays NULL without treating it as an empty string',()=>{
    render(<QueryResults result={{columns:['region'],rows:[[null]],returned_rows:1,truncated:true}}/>)
    expect(screen.getByRole('cell',{name:'NULL'})).toBeTruthy()
    expect(screen.getByText(/Results were limited or clipped/)).toBeTruthy()
  })
})
