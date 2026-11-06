/* Interactive, column-level lineage for mapping previews and proposals. */
function renderMappingModel(container, result, readBinding) {
 const target = result.spec?.target || result.discovery?.target;
 const primaryKey = result.spec?.primary_key || result.discovery?.primary_key;
 const columns = result.spec?.columns || result.discovery?.columns;
 if (!target || !columns?.length) return;
 const joinTypes = {left_join: 'Left join', inner_join: 'Inner join'};
 const proposed = result.combination;
 const source = result.datasets.length === 1 ? result.datasets[0] : null;
 const staged = source && datasets.find(dataset => dataset.id === source.dataset_id)?.metadata;
 const stagedJoin = staged?.kind === 'merge' && joinTypes[staged.mode] && staged.parents?.length === 2;
 const join = joinTypes[proposed?.strategy] ? proposed : stagedJoin ? {
  strategy: staged.mode, left_dataset_id: staged.parents[0].dataset_id, right_dataset_id: staged.parents[1].dataset_id,
  left_key: staged.left_key, right_key: staged.right_key
 } : null;
 const name = id => result.datasets.find(dataset => dataset.dataset_id === id)?.name
  || datasets.find(dataset => dataset.id === id)?.name || 'Source ' + id.slice(0, 8);
 const sourceKey = (id, path) => {
  const candidate = result.discovery?.source_keys?.find(key => key.dataset_id === id && key.path === path);
  if (candidate?.check?.unique_and_nonblank) return 'Unique in snapshot';
  if (candidate?.role === 'primary_candidate') return 'Primary key candidate';
  if (candidate?.role === 'foreign_candidate') return 'Foreign key candidate';
  return '';
 };
 const arrow = '<svg viewBox="0 0 36 20" aria-hidden="true" focusable="false"><path d="M2 10h30m-7-7 7 7-7 7"/></svg>';
 const relationship = join ? `<div class="model-join">
  <div class="model-node"><small>Left source</small><strong>${esc(name(join.left_dataset_id))}</strong><code>${esc(join.left_key)}</code></div>
  <div class="model-join-link"><strong>${esc(joinTypes[join.strategy])}</strong>${arrow}
  <small>${esc(join.preview?.relationship_cardinality?.replaceAll('_',' ') || (stagedJoin ? 'Staged' : 'Proposed'))}</small></div>
  <div class="model-node"><small>Right source</small><strong>${esc(name(join.right_dataset_id))}</strong><code>${esc(join.right_key)}</code></div>
 </div>` : '';
 const state = join && !stagedJoin ? 'Proposed relationship' : proposed?.strategy === 'append' ? 'Mapped append'
  : proposed?.strategy === 'none' ? 'Relationship unresolved' : '';
 container.innerHTML = `<figure class="mapping-model">
  <figcaption><strong>Data model</strong>${state ? '<span class="badge">' + esc(state) + '</span>' : ''}</figcaption>
  ${relationship}
  <p class="hint">Choose a target column to trace its source. The diagram follows your mapping edits.</p>
  <div class="model-lineage">
   <section class="model-target" aria-label="Target table">
    <div class="model-table-head"><small>Target table</small><strong>${esc(target)}</strong></div>
    <div class="model-fields" aria-label="Target columns">${columns.map((column, index) => `<button type="button" data-model-field="${index}" aria-pressed="false">
     <span>${esc(column.name)}</span><small>${column.name === primaryKey ? '<b title="Primary key">PK</b> ' : ''}${esc(column.dtype)}</small>
    </button>`).join('')}</div>
   </section>
   <section class="model-sources" aria-label="Column lineage"></section>
  </div>
 </figure>`;
 let active = Math.max(0, columns.findIndex(column => column.name === primaryKey));
 const buttons = [...container.querySelectorAll('[data-model-field]')];
 const paths = container.querySelector('.model-sources');
 const update = () => {
  const column = columns[active];
  buttons.forEach((button, index) => button.setAttribute('aria-pressed', String(index === active)));
  const mapped = result.datasets.map((dataset, datasetIndex) => {
   const index = dataset.mappings.findIndex(match => match.target === column.name);
   const path = index < 0 ? null : readBinding(datasetIndex, index);
   const side = stagedJoin && path ? path.startsWith('left.') ? 'left' : path.startsWith('right.') ? 'right' : null : null;
   const parentId = side ? staged.parents[side === 'left' ? 0 : 1].dataset_id : dataset.dataset_id;
   const sourcePath = side ? path.slice(side.length + 1) : path;
   return `<li class="model-route">
    <div class="model-node"><small>Source${side ? ' · ' + side + ' side' : ''}</small><strong>${esc(name(parentId))}</strong>
     ${sourcePath ? `<code>${esc(sourcePath)}</code><small>${esc(sourceKey(parentId, sourcePath))}</small>` : '<small class="model-unmapped">Unmapped</small>'}
    </div>
    ${sourcePath ? arrow : '<span class="model-no-link" aria-hidden="true">· · ·</span>'}
    <code class="model-endpoint">${esc(column.name)}</code>
   </li>`;
  });
  paths.innerHTML = `<div class="model-lineage-head"><strong>Column lineage</strong><span aria-live="polite">${esc(column.name)}</span></div>
   <ul>${mapped.join('')}</ul>`;
 };
 buttons.forEach((button, index) => button.onclick = () => { active = index; update(); });
 update();
 return update;
}
