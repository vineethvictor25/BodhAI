/* Destination-specific configuration is an explicit, versioned deployment plan. */
let destinationCatalog={};
document.querySelector('#designer .grid').insertAdjacentHTML('beforebegin',`<article class="panel"><h2>Business grain, destination & load configuration</h2><p>Choose a target, then configure its loading requirements. Local execution is available now. Cloud destinations produce reviewed deployment plans and DDL; cloud connectors are not connected.</p><div class="form-row"><label>Cloud provider<select id="destinationCloud"></select></label><label>Destination warehouse<select id="destinationWarehouse"></select></label><label>Load strategy<select id="destinationLoad"><option value="upsert">Upsert / Type 1 overwrite</option><option value="append">Append · reject existing keys</option><option value="full_refresh">Full refresh · atomic replacement</option><option value="partition_overwrite">Overwrite explicit partitions</option><option value="scd2">SCD Type 2 · version tracked attributes</option></select></label></div><div class="form-row"><label>Schema / dataset namespace<input id="destinationNamespace" value="analytics"></label><label>Catalog / project (optional)<input id="destinationCatalogName"></label></div><div id="destinationSettings" class="grid two"></div><div id="destinationModeFields"></div><details><summary>Business requirements</summary><label>What does one target row represent?<input id="businessGrain" placeholder="One order line, identified by order_line_id"></label><div class="form-row"><label>Data owner<input id="businessOwner" placeholder="Commerce data team"></label><label>Freshness requirement (minutes)<input id="businessFreshness" type="number" value="1440" min="1"></label></div><label>Consumers (comma separated)<input id="businessConsumers" placeholder="Finance BI, forecasting"></label><p class="hint">A declared SLA is metadata. Distributed scheduling and SLA alerting require the cloud orchestration integration.</p></details><div class="button-row"><button id="applyDestination">Apply configuration to specification</button><button id="reviewDestinationPlan">Review destination load plan</button></div><div id="destinationPlan"></div></article>`);
function renderDestinationWarehouses(saved){
 const entry=destinationCatalog[$('destinationCloud').value];if(!entry)return;
 $('destinationWarehouse').innerHTML=entry.warehouses.map(w=>`<option value="${esc(w.id)}">${esc(w.label)}</option>`).join('');
 if(saved&&entry.warehouses.some(w=>w.id===saved))$('destinationWarehouse').value=saved;
 renderDestinationSettings();
}
function renderDestinationSettings(settings={}){
 const selected=destinationCatalog[$('destinationCloud').value]?.warehouses.find(w=>w.id===$('destinationWarehouse').value);if(!selected)return;
 $('destinationSettings').innerHTML=selected.configuration_fields.map(f=>`<label>${esc(f.label)}${f.required?' *':''}<input data-destination-setting="${esc(f.name)}" value="${esc(settings[f.name]||'')}" autocomplete="off"></label>`).join('')||'<p class="hint">Local storage uses DATA_DIR on the server. No remote credentials are required.</p>';
 renderLoadFields();
}
function renderLoadFields(){
 const mode=$('destinationLoad').value;
 $('destinationModeFields').innerHTML=mode==='partition_overwrite'?'<div class="form-row"><label>Partition column<input id="loadPartitionColumn"></label><label>Partitions to replace (comma separated)<input id="loadPartitionValues" placeholder="2026-11-01,2026-11-02"></label></div>':mode==='scd2'?'<label>Tracked attributes (comma separated; blank tracks all)<input id="loadTrackedColumns"></label><p class="hint">Local SCD2 records load-time history. Event-time/as-of dimension enrichment and inferred members require further integration.</p>':'';
}
function syncDestination(){
 if(!Object.keys(destinationCatalog).length)return;
 const s=spec();const d=s.destination||{cloud:'local',warehouse:'sqlite',namespace:'analytics',settings:{}};
 $('destinationCloud').value=d.cloud;renderDestinationWarehouses(d.warehouse);renderDestinationSettings(d.settings||{});
 $('destinationNamespace').value=d.namespace;$('destinationCatalogName').value=d.catalog||'';$('destinationLoad').value=s.load?.mode||'upsert';renderLoadFields();
 if($('loadPartitionColumn')){$('loadPartitionColumn').value=s.load.partition_column||'';$('loadPartitionValues').value=(s.load.partition_values||[]).join(',');}
 if($('loadTrackedColumns'))$('loadTrackedColumns').value=(s.load.tracked_columns||[]).join(',');
 $('businessGrain').value=s.business?.grain||'';$('businessOwner').value=s.business?.owner||'';$('businessFreshness').value=s.business?.freshness_minutes||1440;$('businessConsumers').value=(s.business?.consumers||[]).join(',');
}
function destinationSpec(){
 const s=spec();const settings={};document.querySelectorAll('[data-destination-setting]').forEach(el=>{if(el.value.trim())settings[el.dataset.destinationSetting]=el.value.trim();});
 s.destination={cloud:$('destinationCloud').value,warehouse:$('destinationWarehouse').value,namespace:$('destinationNamespace').value,catalog:$('destinationCatalogName').value,settings};
 s.load={mode:$('destinationLoad').value,partition_column:$('loadPartitionColumn')?.value||null,partition_values:($('loadPartitionValues')?.value||'').split(',').map(s=>s.trim()).filter(Boolean),tracked_columns:($('loadTrackedColumns')?.value||'').split(',').map(s=>s.trim()).filter(Boolean)};
 if($('businessGrain').value.trim())s.business={...(s.business||{}),grain:$('businessGrain').value.trim(),owner:$('businessOwner').value.trim(),freshness_minutes:Number($('businessFreshness').value),consumers:$('businessConsumers').value.split(',').map(s=>s.trim()).filter(Boolean)};
 return s;
}
$('destinationCloud').onchange=()=>renderDestinationWarehouses();$('destinationWarehouse').onchange=()=>renderDestinationSettings();$('destinationLoad').onchange=renderLoadFields;
bind('applyDestination',async()=>{const s=destinationSpec();const result=await api('artifacts',s);setSpec(s,true);clearSemanticMapping();renderArtifacts(result);notify('Destination and load settings validated and saved in the specification. Cloud destinations support export and preview only.');});
bind('reviewDestinationPlan',async()=>{const ids=selected();const s=destinationSpec();const result=await api('artifacts',s);$('destinationPlan').innerHTML=jsonView(JSON.parse(result['destination-plan.json']));renderArtifacts(result);});
api('destinations/catalog').then(c=>{destinationCatalog=c;$('destinationCloud').innerHTML=Object.entries(c).map(([key,value])=>`<option value="${esc(key)}">${esc(value.label)}</option>`).join('');renderDestinationWarehouses();if($('spec').value)syncDestination();}).catch(e=>notify(e.message,true));
