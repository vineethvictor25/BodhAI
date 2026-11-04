/* File ETL, lineage and layer inspection. All data is escaped before rendering. */
const medNav=document.createElement('button');medNav.dataset.page='medallion';medNav.innerHTML='◫ <span>Medallion ETL</span>';
document.querySelector('nav').append(medNav);medNav.onclick=()=>{page('medallion');loadMedallion().catch(e=>notify(e.message,true));};
document.querySelector('main').insertAdjacentHTML('beforeend',`<section id="medallion" class="page"><div class="eyebrow">BRONZE → SILVER → GOLD</div><h1>File ETL & business-ready data</h1><p>Stage multiple files, reconcile their schemas, preview transformations, then publish through quality gates.</p>
<div class="button-row"><button id="stageMedallionSample" class="primary">Stage four-format order example</button><button id="reloadMedallion">Refresh sources & runs</button><button id="medallionStudio">Open Pipeline studio</button></div>
<div class="grid two"><article class="panel"><h2>1. Bronze · inspect or merge sources</h2><p>Original bytes remain unchanged. Statistics describe the staged extract. Select two or more files to append, or exactly two for a keyed join.</p><div id="mergeSelection" class="selection"></div><label>Merge behavior<select id="mergeMode"><option value="append">Append matching schemas</option><option value="left_join">Left join · retain unmatched left records</option><option value="inner_join">Inner join · matched records only</option></select></label><div class="form-row"><label>Left key<input id="mergeLeft" placeholder="customer_id"></label><label>Right key<input id="mergeRight" placeholder="customer_id"></label></div><label>Merged dataset name<input id="mergeName" value="merged_source"></label><button id="mergeFiles">Create merged source</button><p class="hint">The first selected source is the left side. Right keys must be unique. Join output uses left.* and right.* paths. To append different schemas, select the original sources in Pipeline studio and review each mapping.</p><div id="mergeStatus"></div></article>
<article class="panel"><h2>Source profiles & contracts</h2><label>Source<select id="profileDataset"></select></label><button id="profileDatasetButton">Profile & inspect Bronze</button><div id="sourceProfile"></div><details><summary>Source contract · schema drift and control totals</summary><p>Match source_name to the uploaded filename. expected_schema uses flattened paths and allowed observed types. Fail rejects drift; alert records drift; evolve accepts the observed extract without weakening the target contract.</p><textarea id="sourceContract" rows="12">{"source_name":"my_orders.csv","owner":"Data engineering","update_pattern":"mutable","ingestion_pattern":"full","expected_schema":{},"drift_policy":"fail","classification":"internal"}</textarea><button id="saveSourceContract">Save versioned source contract</button></details></article></div>
<article class="panel"><h2>2. Silver & 3. Gold · inspect a run</h2><p>Silver retains validated candidates and row provenance. Failed gates block published detail and Gold. Gold aggregates the full current detail target; a preview shows only the selected batch.</p><label>Pipeline run<select id="layerRun"></select></label><div class="button-row"><button id="inspectLayers">Inspect all layers & STTM</button><button id="exportSilverJson">Download Silver JSON</button><button id="exportSilverParquet">Download Silver Parquet</button><button id="exportGoldCsv">Download Gold CSV</button><button id="exportGoldParquet">Download Gold Parquet</button></div><div id="layerResults"></div></article>
<article class="panel"><h2>Extraction control & audit</h2><div id="extractionAudit"></div></article></section>`);
$('execute').insertAdjacentHTML('beforebegin','<button id="previewMedallion">Preview Silver & Gold</button>');
async function loadMedallion(){
 const [sources,runs,audits]=await Promise.all([api('datasets'),api('runs'),api('extractions')]);
 $('mergeSelection').innerHTML=sources.map(d=>`<label><input type="checkbox" value="${esc(d.id)}">${esc(d.name)} · ${esc(d.row_count)} rows</label>`).join('');
 $('profileDataset').innerHTML=sources.map(d=>`<option value="${esc(d.id)}">${esc(d.name)}</option>`).join('');
 $('layerRun').innerHTML=runs.map(r=>`<option value="${esc(r.id)}">${esc(r.id.slice(0,10))} · ${esc(r.status)}</option>`).join('');
 $('extractionAudit').innerHTML=table(['File / source','Status','Rows','Duration (s)','Batch'],audits.map(r=>[esc(r.source_name),badge(r.status),esc(r.details.rows_extracted??'—'),esc(r.details.duration_seconds?.toFixed(3)??'—'),esc(r.id.slice(0,10))]));
}
bind('reloadMedallion',loadMedallion);bind('medallionStudio',async()=>page('designer'));
bind('stageMedallionSample',async()=>{
 const result=await api('medallion/sample',{});setSpec(result.spec,true);if(typeof syncDestination==='function')syncDestination();clearSemanticMapping();await refresh();
 const ids=result.datasets.map(d=>d.id);document.querySelectorAll('#sourceSelection input').forEach(el=>el.checked=ids.includes(el.value));
 await loadMedallion();page('designer');notify('CSV, nested JSON, Parquet and pipe-delimited TXT staged. Review the order-line contract, preview, then run.');
});
bind('mergeFiles',async()=>{
 const ids=[...document.querySelectorAll('#mergeSelection input:checked')].map(el=>el.value);
 const result=await api('datasets/merge',{name:$('mergeName').value,dataset_ids:ids,mode:$('mergeMode').value,left_key:$('mergeLeft').value,right_key:$('mergeRight').value});
 await refresh();await loadMedallion();$('mergeStatus').textContent=`Created ${result.name}: ${result.row_count} records. Select this dataset in Pipeline studio; do not also select its parents.`;
});
bind('profileDatasetButton',async()=>{
 const id=$('profileDataset').value;if(!id)throw Error('Stage a source first.');
 const [profile,bronze]=await Promise.all([api(`datasets/${id}/profile`),api(`datasets/${id}/bronze`)]);
 $('sourceProfile').innerHTML=`<details open><summary>Source statistics</summary>${jsonView(profile)}</details><details><summary>Bronze metadata & preview</summary>${jsonView(bronze)}</details>`;
});
bind('saveSourceContract',async()=>{const r=await api('sources/contracts',JSON.parse($('sourceContract').value));notify('Source contract revision '+r.revision+' saved. It applies to subsequent ingestions.');});
bind('previewMedallion',async()=>{
 const ids=selected();if(!ids.length)throw Error('Select staged sources first.');
 if(semanticProposal&&(!approvedMappingId||mappingContext!==currentMappingContext()))throw Error('Approve the current mapping or clear it first.');
 const result=await api('medallion/preview',{spec:spec(),dataset_ids:ids,idempotency_key:'preview',approved_mapping_id:approvedMappingId});
 $('mappingResult').innerHTML=jsonView(result);notify('Preview complete. No pipeline output was written. Review batch checks and contract errors.');
});
bind('inspectLayers',async()=>{
 const id=$('layerRun').value;if(!id)throw Error('Run a pipeline first.');
 const paths=[`runs/${id}`,`runs/${id}/layers/silver`,`runs/${id}/layers/gold`,`runs/${id}/sttm`,`runs/${id}/filtered`];
 const results=await Promise.all(paths.map(p=>api(p)));
 $('layerResults').innerHTML=['Run & quality gates','Silver · first 100 rows','Gold · first 100 groups','Source-to-target contract & lineage','Filtered / superseded rows'].map((label,i)=>`<details ${i===2?'open':''}><summary>${esc(label)}</summary>${jsonView(results[i])}</details>`).join('');
});
async function downloadLayer(layer,format){
 const id=$('layerRun').value;if(!id)throw Error('Select a successful run first.');
 const response=await fetch(`/api/runs/${encodeURIComponent(id)}/export?layer=${layer}&format=${format}`,{headers:token?{Authorization:'Bearer '+token}:{}});
 if(!response.ok){const error=await response.json();throw Error(error.detail||'Export failed');}
 const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=`${layer}-${id}.${format}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
bind('exportSilverJson',()=>downloadLayer('silver','json'));bind('exportSilverParquet',()=>downloadLayer('silver','parquet'));bind('exportGoldCsv',()=>downloadLayer('gold','csv'));bind('exportGoldParquet',()=>downloadLayer('gold','parquet'));
