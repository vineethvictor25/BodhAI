/* No dependencies or third-party requests. All untrusted values render as text. */
let token = '', datasets = [], activeRun = null, proposal = null;
let semanticProposal = null, approvedMappingId = null, mappingContext = null;
let specChosenByUser = false;
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
function notify(message, error = false) { $('notice').textContent = message; $('notice').className = error ? 'error' : ''; $('notice').hidden = false; }
function page(id) { document.querySelectorAll('.page').forEach(el => el.classList.toggle('active', el.id === id)); document.querySelectorAll('nav button').forEach(el => el.classList.toggle('active', el.dataset.page === id)); $('breadcrumb').textContent = document.querySelector(`[data-page="${id}"] span`).textContent; }
document.querySelectorAll('[data-page]').forEach(el => el.onclick = () => page(el.dataset.page));
document.querySelectorAll('[data-nav]').forEach(el => el.onclick = () => page(el.dataset.nav));
async function api(path, body, method, extraHeaders = {}) {
 const headers = {...(token ? {Authorization: 'Bearer ' + token} : {}), ...extraHeaders};
 let options = {method: method || (body ? 'POST' : 'GET'), headers};
 if (body instanceof FormData) options.body = body;
 else if (body !== undefined) { options.body = JSON.stringify(body); headers['Content-Type'] = 'application/json'; }
 const response = await fetch('/api/' + path, options);
 let result = {};
 const text = await response.text();
 if (text) {
   try { result = JSON.parse(text); } catch (error) { result = {detail: text}; }
 }
 if (!response.ok) { if (response.status === 401) $('tokenDialog').showModal(); const detail = (result && (result.detail ?? result.message)) || text || `HTTP ${response.status}`; throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail)); }
 return result;
}
function bind(id, action) { $(id).onclick = async () => { const button = $(id); button.disabled = true; try { await action(); } catch (error) { notify(error.message, true); } finally { button.disabled = false; } }; }
function table(headers, rows) { return rows.length ? `<table><thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table>` : '<div class="empty">No records yet. Run the sample pipeline or stage your own source.</div>'; }
function badge(status) { return `<span class="badge ${esc(status)}">${esc(status.replaceAll('_', ' '))}</span>`; }
function jsonView(value) { return `<pre>${esc(JSON.stringify(value, null, 2))}</pre>`; }
function spec() { return JSON.parse($('spec').value); }
function setSpec(value, chosenByUser = false) {
 $('spec').value = JSON.stringify(value, null, 2);
 specChosenByUser = chosenByUser;
 if (typeof syncPrivacyFields === 'function') syncPrivacyFields();
}
function selected() { return [...document.querySelectorAll('#sourceSelection input:checked')].map(el => el.value); }
function renderArtifacts(artifacts) { $('artifacts').replaceChildren(); Object.entries(artifacts).forEach(([name, content]) => { const button = document.createElement('button'); button.textContent = '↓ ' + name; button.onclick = () => { const url = URL.createObjectURL(new Blob([content], {type:'text/plain'})); const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }; $('artifacts').append(button); }); }
async function refreshWarehouseRows() {
 const target = $('warehouseTarget').value;
 $('warehouseValidation').replaceChildren();
 if (!target) {
  $('warehouseRows').textContent = 'No published warehouse targets yet.';
  return;
 }
 const warehouse = await api('warehouse?target=' + encodeURIComponent(target));
 const records = warehouse.rows.map(row => row.record);
 const keys = Object.keys(records[0] || {});
 $('warehouseRows').innerHTML = table(keys, records.map(row => keys.map(key => esc(row[key]))));
}
async function refresh() {
 const [sources, runs, targets] = await Promise.all([api('datasets'), api('runs'), api('warehouse/targets')]); datasets = sources;
 const chosen = selected();
 $('sourceCount').textContent = sources.length; $('runCount').textContent = runs.length; $('qualityScore').textContent = runs[0]?.report.quality_score != null ? runs[0].report.quality_score + '%' : '—'; $('warehouseCount').textContent = targets.reduce((total, item) => total + item.total, 0);
 $('datasetTable').innerHTML = table(['Source','Records','Type','Fingerprint','Created'], sources.map(d => [esc(d.name), esc(d.row_count), esc(d.metadata.kind), `<code>${esc(d.sha256.slice(0,16))}…</code>`, esc(new Date(d.created_at).toLocaleString())]));
 $('sourceSelection').innerHTML = sources.map(d => `<label><input type="checkbox" value="${esc(d.id)}" ${chosen.includes(d.id) ? 'checked' : ''}>${esc(d.name)} <span class="muted">${esc(d.row_count)} rows</span></label>`).join('') || '<p class="hint">Stage a source in Data sources first.</p>';
 const rows = runs.map(r => [`<code>${esc(r.id.slice(0,10))}</code>`,badge(r.status),esc(r.report.accepted ?? '—'),esc(r.report.rejected ?? '—'),esc(r.report.published ?? 0),esc(new Date(r.created_at).toLocaleString()),`<button data-run="${esc(r.id)}">Inspect →</button>`]);
 $('runTable').innerHTML = table(['Run','Status','Valid','Rejected','Published','Created',''],rows);
 $('recentRuns').innerHTML = table(['Run','Status','Valid','Rejected','Published','Created',''],rows.slice(0,5));
 document.querySelectorAll('[data-run]').forEach(el => el.onclick = async () => { try { await inspect(await api('runs/' + el.dataset.run)); } catch(e) { notify(e.message,true); } });
 const targetSelect = $('warehouseTarget'), previousTarget = targetSelect.value;
 targetSelect.innerHTML = targets.map(item => `<option value="${esc(item.target)}">${esc(item.target)} (${esc(item.total)} rows)</option>`).join('');
 if (targets.some(item => item.target === previousTarget)) targetSelect.value = previousTarget;
 await refreshWarehouseRows();
}
async function inspect(run) {
 activeRun = run; const r = run.report; page('quality');
 $('propose').disabled = !r.rejected || !['quality_failed','succeeded'].includes(run.status);
 $('runDetail').className = '';
 const encryptedColumns = run.request.spec.columns.filter(column => column.protection === 'encrypt');
 const decryptionPanel = encryptedColumns.length ? `<section class="pii-decrypt panel" aria-labelledby="decryptHeading"><h2 id="decryptHeading">View encrypted values</h2><p class="hint">Decrypt only the Silver rows you need. This action is audited. Enter the separate step-up token; it is used for this request only and is not saved. Use run row numbers in input order; rejected or filtered rows are not available. Maximum 20 rows per request.</p><label for="decryptColumn">Encrypted column</label><select id="decryptColumn">${encryptedColumns.map(column => `<option value="${esc(column.name)}">${esc(column.name)}</option>`).join('')}</select><label for="decryptRows">Run row numbers (comma-separated)</label><input id="decryptRows" type="text" inputmode="numeric" autocomplete="off" placeholder="1, 2, 3"><label for="decryptToken">Step-up decryption token</label><input id="decryptToken" type="password" autocomplete="off"><div class="button-row"><button id="decryptSelected" class="primary">Decrypt selected rows</button><button id="clearDecrypted">Clear displayed values</button></div><div id="decryptedValues" aria-live="polite"></div></section>` : '';
 $('runDetail').innerHTML = `<div class="panel-head"><code>${esc(run.id)}</code>${badge(run.status)}</div><div class="detail-summary"><span>${esc(r.total ?? 0)} input</span><span>${esc(r.accepted ?? 0)} valid</span><span>${esc(r.rejected ?? 0)} quarantined</span><span>${esc(r.published ?? 0)} published</span><span>${esc(r.quality_score ?? '—')}% quality</span></div><p class="hint">Reject limit: ${esc((run.request.spec.max_reject_ratio * 100).toFixed(1))}%. ${r.gate?.passed ? 'Quality gate passed.' : 'Publication blocked.'} Source snapshot and specification are retained.</p>${decryptionPanel}${['mappings','issues','changes','preview'].map(key => `<details ${key === 'issues' ? 'open' : ''}><summary>${esc(key[0].toUpperCase()+key.slice(1))}</summary>${jsonView(r[key] || [])}</details>`).join('')}<details><summary>Audit trail</summary>${jsonView(run.events)}</details><details><summary>Full quarantine (first 1,000 records)</summary><div id="fullQuarantine">Loading…</div></details>`;
 if (encryptedColumns.length) {
  $('decryptSelected').onclick = async () => {
   const button = $('decryptSelected');
   const tokenInput = $('decryptToken');
   button.disabled = true;
   try {
    const rowNumbers = $('decryptRows').value.split(',').map(value => value.trim());
    if (!tokenInput.value) throw Error('Enter the step-up decryption token.');
    if (!rowNumbers.length || rowNumbers.length > 20 || rowNumbers.some(value => !/^[1-9]\d*$/.test(value))) throw Error('Enter 1–20 positive run row numbers, separated by commas.');
    const numbers = rowNumbers.map(Number);
    if (new Set(numbers).size !== numbers.length || numbers.some(value => !Number.isSafeInteger(value))) throw Error('Enter distinct, valid row numbers.');
    const result = await api('privacy/decrypt', {
     run_id: run.id,
     column: $('decryptColumn').value,
     row_numbers: numbers
    }, 'POST', {'X-PII-Access-Token': tokenInput.value});
    $('decryptedValues').innerHTML = table(['Run row','Decrypted value'], result.rows.map(row => [esc(row.row_number), esc(typeof row.value === 'string' ? row.value : JSON.stringify(row.value))]));
    notify(`Decrypted ${result.rows.length} selected value(s). Access was audited.`);
   } catch (error) {
    notify(error.message, true);
   } finally {
    tokenInput.value = '';
    button.disabled = false;
   }
  };
  $('clearDecrypted').onclick = () => { $('decryptedValues').replaceChildren(); $('decryptToken').value = ''; };
 }
 const rejected = await api('runs/' + run.id + '/quarantine?limit=1000'); $('fullQuarantine').innerHTML = jsonView(rejected);
}
bind('demo', async () => { const result = await api('demo', {}); setSpec(result.run.request.spec, true); await refresh(); document.querySelector(`#sourceSelection input[value="${result.dataset.id}"]`).checked = true; await inspect(result.run); notify('Sample run completed. The strict numeric parser blocked publication. Choose “Propose repair” to review recovery.'); });
bind('generate', async () => { const result = await api('generate',{prompt:$('prompt').value}); setSpec(result.spec, true); $('generationMeta').textContent = 'Generated with ' + result.metadata.provider + (result.metadata.model ? ' / '+result.metadata.model : '') + (result.metadata.fallback_used ? ' (fallback route)' : '') + '. ' + (result.metadata.notes || []).join(' '); renderArtifacts(result.artifacts); notify('Plan generated. Review its contract before running.'); });
bind('export', async () => { renderArtifacts(await api('artifacts',spec())); notify('Validated artifacts are ready to download.'); });
bind('saveSpec', async () => { renderArtifacts(await api('artifacts',spec())); specChosenByUser = true; notify('Specification is valid. Changes are kept in the editor; each run persists its own copy.'); });
bind('generateSourceSpec', async () => {
 const ids=selected();if(ids.length!==1)throw Error('Select exactly one source to build a draft specification.');
 const draft=await api(`datasets/${ids[0]}/suggested-spec`);
 setSpec(draft.spec,true);clearSemanticMapping();
 notify(`Draft specification built from ${draft.source_name}. Review the primary key and generated field types. ${draft.warnings.join(' ')}`);
});
bind('profile', async () => { const ids = selected(); if (!ids.length) throw Error('Select at least one source.'); const results = []; for (const id of ids) results.push(await api('mapping',{dataset_id:id,spec:spec()})); $('mappingResult').innerHTML = jsonView(results); });
bind('execute', async () => { const ids = selected(); if (!ids.length) throw Error('Select at least one source.'); if (semanticProposal && (!approvedMappingId || mappingContext !== currentMappingContext())) throw Error('Approve the current AI mapping first, or clear it to use name-based matching.'); const result = await api('runs',{spec:spec(),dataset_ids:ids,idempotency_key:crypto.randomUUID(),approved_mapping_id:approvedMappingId}); await refresh(); await inspect(result); notify('Run completed: ' + result.status.replaceAll('_',' ')); });
bind('upload', async () => {
 const files=[...$('sourceFile').files];if(!files.length)throw Error('Choose one or more files first.');
 if(files.length>20)throw Error('Upload at most 20 files at once.');
 const successful=[],errors=[];
 for(const file of files){
  const form=new FormData();form.append('file',file);let delimiter=$('textDelimiter').value;
  if(delimiter)form.append('delimiter',delimiter==='tab'?'\t':delimiter);
  try{successful.push(await api('datasets/upload',form));}catch(e){errors.push(file.name+': '+e.message);}
 }
 await refresh();document.querySelectorAll('#sourceSelection input').forEach(el=>{if(successful.some(d=>d.id===el.value))el.checked=true;});
 let draftNotice='';
 if(successful.length&&!specChosenByUser){
  try{
   const draft=await api(`datasets/${successful[0].id}/suggested-spec`);
   setSpec(draft.spec);
   clearSemanticMapping();
   draftNotice=` Pipeline specification populated from ${draft.source_name}; review the selected primary key and generated field types. ${draft.warnings.join(' ')}`;
  }catch(error){draftNotice=` Source was staged, but automatic specification generation failed: ${error.message}`;}
 }else if(successful.length){
  draftNotice=' Existing pipeline specification was preserved. Select exactly one source and choose “Build draft from selected source” to create a draft for this file.';
 }
 notify(`Staged ${successful.length} of ${files.length} files. `+(errors.length?errors.join(' | '):'')+draftNotice+(draftNotice?'':'Select them in Pipeline studio, or merge them in Medallion ETL.'),errors.length>0||draftNotice.includes('failed'));
});
bind('fetchApi', async () => { const result = await api('datasets/api',{url:$('apiUrl').value}); await refresh(); notify('API snapshot staged: ' + result.row_count + ' records.'); });
bind('translate', async () => { const result = await api('translate',{sql:$('sqlInput').value,source:$('sourceDialect').value,target:$('targetDialect').value}); $('sqlOutput').textContent = result.sql || 'Manual review required'; $('sqlWarnings').textContent = result.warnings.join(' '); });
bind('diagnose', async () => { $('diagnosis').innerHTML = jsonView(await api('diagnose',{log:$('errorLog').value})); });
bind('propose', async () => { proposal = await api('runs/' + activeRun.id + '/remediation',{}); $('proposal').className=''; $('proposal').innerHTML = `<p>${esc(proposal.root_cause)}</p>${jsonView(proposal.patch)}<p class="hint">${esc(proposal.risk)}</p><details><summary>Revised recipe</summary>${jsonView(proposal.spec)}</details>`; $('applyRepair').hidden=false; page('heal'); });
bind('applyRepair', async () => { if (!proposal) return; const result = await api('remediations/' + proposal.id + '/apply',{approved:true}); $('applyRepair').hidden=true; await refresh(); await inspect(result); notify('Reviewed patch applied in a new run. Published ' + result.report.published + ' valid records; original rules are still enforced.'); });
bind('refreshRuns',refresh);
bind('refreshWarehouse',refresh);
bind('validateWarehouse',async()=>{
 const target=$('warehouseTarget').value;
 if(!target)throw Error('There is no published warehouse target to validate.');
 const result=await api('warehouse/validate?target='+encodeURIComponent(target));
 $('warehouseValidation').innerHTML=`<div class="callout">${result.passed?'Validation passed':'Validation found issues'} · ${esc(result.rows_checked)} rows checked · ${esc(result.invalid_records)} invalid records</div>${table(['Check','Column','Invalid records','Status'],result.checks.map(check=>[esc(check.check),esc(check.column??'—'),esc(check.invalid_records),check.invalid_records?'Failed':'Passed']))}`;
 notify(result.passed?`Warehouse validation passed for ${target}.`:`Warehouse validation found ${result.invalid_records} invalid record(s) in ${target}.`,!result.passed);
});
$('warehouseTarget').onchange=()=>refreshWarehouseRows().catch(error=>notify(error.message,true));
$('tokenButton').onclick = () => $('tokenDialog').showModal();
$('tokenDialog').addEventListener('close', async () => { if ($('tokenDialog').returnValue === 'save') { token=$('tokenInput').value; $('tokenInput').value=''; try { await initialize(); } catch(e) { notify(e.message,true); } } });
document.addEventListener('DOMContentLoaded',()=>{$('spec').addEventListener('input',()=>{specChosenByUser=true;});});
async function initialize() { const conf=await api('config'); $('provider').textContent=conf.provider==='local' ? 'Offline recipe mode' : 'Live AI · '+(conf.live_ai_configured?'configured':'configuration needed'); if (!$('spec').value) setSpec(conf.default_spec); await refresh(); }
// AI mapping sits beside source selection, independently of target-schema generation.
$('mappingResult').insertAdjacentHTML('beforebegin', `<div class="semantic-panel"><h3>Auto-generate schema mappings with natural language</h3><label for="mappingInstructions">Describe what the source fields mean and how they relate to your target</label><textarea id="mappingInstructions" rows="3">Match the supplier's merchandise identifier to product_id, its customer-facing caption to product_name, package span to length_m, selling value to price, settlement currency to currency, and department to category. Use sample values to determine meaning. Leave fields unmatched when the meaning is uncertain.</textarea><p class="hint">Live AI receives column names, observed types, and up to 3 sample values per field (80 characters each). Select up to 5 sources. Review confidence and units before approval; existing cleaning rules still apply.</p><div class="button-row"><button id="generateMappings" class="primary">✧ Generate AI mappings</button><button id="clearMappings">Clear AI mapping</button></div><div id="semanticResult"></div><button id="approveMappings" class="primary" hidden>Approve reviewed mappings</button></div>`);
function currentMappingContext() { return JSON.stringify({spec:spec(), ids:selected().sort(), instructions:$('mappingInstructions').value, use_knowledge:$('useKnowledge')?.checked||false}); }
function clearSemanticMapping() { semanticProposal=null; approvedMappingId=null; mappingContext=null; $('semanticResult').replaceChildren(); $('approveMappings').hidden=true; }
bind('clearMappings', async () => { clearSemanticMapping(); notify('AI mapping cleared. Runs will use the existing name-based mapper.'); });
function renderSemanticMapping(result) {
 $('semanticResult').innerHTML = `<p class="hint">${esc(result.notice)}</p>` + result.datasets.map((dataset, datasetIndex) => `<h3>${esc(dataset.name)}</h3><div class="table-wrap">${table(['Target','Reviewed source path','Model confidence','Explanation & evidence'], dataset.mappings.map((match, matchIndex) => [esc(match.target), `<select data-mapping-dataset="${datasetIndex}" data-mapping-entry="${matchIndex}"><option value="">Unmapped / review required</option>${dataset.source_columns.map(c => `<option value="${esc(c.path)}" ${c.path===match.source?'selected':''}>${esc(c.path)}</option>`).join('')}</select>`, `${esc(Math.round(match.confidence*100))}%${match.review_required?'<br><span class="badge quality_failed">Review required</span>':''}`, `${esc(match.reason)}<div class="hint">${esc(match.evidence.join(' · '))}${match.suggested_source && !match.source?'<br>Suggestion: '+esc(match.suggested_source):''}</div>`]))}</div><p class="hint">${esc(dataset.warnings.join(' '))}</p>`).join('');
 document.querySelectorAll('[data-mapping-dataset]').forEach(el => el.onchange=()=>{ approvedMappingId=null; $('approveMappings').hidden=false; });
 $('approveMappings').hidden=false;
}
bind('generateMappings', async () => {
 const ids=selected(); if (!ids.length) throw Error('Select source datasets first.');
 const body={instructions:$('mappingInstructions').value, spec:spec(), dataset_ids:ids, use_knowledge:$('useKnowledge')?.checked||false};
 const context=currentMappingContext();
 const result=await api('mappings/generate',body);
 semanticProposal=result; approvedMappingId=null; mappingContext=context; renderSemanticMapping(result);
 notify('AI mapping proposal generated. Review the selected source path for every target, then approve.');
});
bind('approveMappings', async () => {
 if (!semanticProposal || mappingContext!==currentMappingContext()) throw Error('Sources or specification changed. Generate a fresh mapping proposal.');
 const decisions={}; semanticProposal.datasets.forEach((dataset,di)=>{ decisions[dataset.dataset_id]={}; dataset.mappings.forEach((match,mi)=>{const el=document.querySelector(`[data-mapping-dataset="${di}"][data-mapping-entry="${mi}"]`); decisions[dataset.dataset_id][match.target]=el.value || null; }); });
 const result=await api('mappings/'+semanticProposal.id+'/approve',{approved:true,decisions});
 approvedMappingId=result.id; $('approveMappings').hidden=true;
 document.querySelectorAll('[data-mapping-dataset]').forEach(el=>el.disabled=true);
 notify('Mapping approved and saved. “Run reviewed pipeline” will use these exact per-source decisions. Generate a new proposal to revise them.');
});
initialize().catch(error => notify(error.message,true));
