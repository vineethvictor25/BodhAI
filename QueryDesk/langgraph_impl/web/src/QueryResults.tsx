import { displayValue,type QueryResult } from './api'

type Rows=QueryResult['queries'][number]['result']

export default function QueryResults({result}:{result:Rows}){
  return <section className="query-results" aria-label="Query results">
    <div className="query-results-heading"><h3>Query results</h3>{result.rows_retained!==false&&<span>{result.rows.length} {result.rows.length===1?'row':'rows'} shown</span>}</div>
    {result.rows_retained===false?<p>Result rows were not retained in saved history. Run the question again and approve the SQL to view fresh results.</p>:<>
      {result.columns.length>0&&<div className="query-results-scroll" role="region" aria-label="Scrollable query results" tabIndex={0}>
        <table><caption>Query results</caption><thead><tr>{result.columns.map((column,index)=><th scope="col" key={index}>{column}</th>)}</tr></thead>
          <tbody>{result.rows.map((row,index)=><tr key={index}>{result.columns.map((_,column)=><td key={column}>{displayValue(row[column])}</td>)}</tr>)}</tbody>
        </table>
      </div>}
      {result.rows.length===0&&<p>No matching rows.</p>}
      {result.truncated&&<p className="query-results-notice">Results were limited or clipped. Narrow the question to see a more complete result.</p>}
    </>}
  </section>
}
