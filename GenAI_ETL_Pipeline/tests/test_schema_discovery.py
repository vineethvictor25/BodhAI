import json

from fastapi.testclient import TestClient
import pytest

from app import schema_discovery as discovery, semantic_mapping, store
from app.ingest import save_rows
from app.main import app
from app.medallion_api import MergeRequest, merge, preview_merge
from app.merge_engine import save_generated_rows


def test_large_merges_persist_every_row():
    count = 25_007
    base = save_generated_rows(({'line_id': i, 'ref': 'matched' if i % 2 else 'missing'}
                                for i in range(count)), 'large-base.csv', {})
    lookup = save_rows([{'lookup_ref': 'matched', 'category': 'home'}], 'lookup.json')
    request = MergeRequest(dataset_ids=[base['id'], lookup['id']], mode='left_join',
                           left_key='ref', right_key='lookup_ref')
    report = preview_merge(request)
    assert report['output_rows'] == count
    assert report['matched_left_rows'] == count // 2
    assert report['relationship_cardinality'] == 'many_to_one'
    with store.db() as conn:
        assert conn.execute('SELECT count(*) FROM datasets').fetchone()[0] == 2
    joined = merge(request)
    assert joined['row_count'] == count and joined['metadata']['chunked']
    assert [p['dataset_id'] for p in joined['metadata']['parents']] == [base['id'], lookup['id']]
    for start, rows in store.iter_dataset_rows(joined['id'], batch_size=700):
        assert len(rows) <= 700
    assert start + len(rows) - 1 == count
    assert rows[-1] == {'left': {'line_id': count - 1, 'ref': 'missing'}, 'right': {}}

    extra = save_rows([{'line_id': count, 'ref': 'matched'}], 'more.csv')
    appended = merge(MergeRequest(dataset_ids=[base['id'], extra['id']], mode='append'))
    assert appended['row_count'] == count + 1


def fixture_response(left_id, right_id):
    return discovery.DiscoveryResponse.model_validate({
        'target': 'catalog',
        'grain': 'One row per item',
        'field_equivalences': [],
        'primary_key': 'item_id',
        'columns': [
            {'name': 'item_id', 'dtype': 'integer', 'meaning': 'Stable merchandise identifier',
             'bindings': [{'dataset_id': left_id, 'path': 'sku', 'meaning': 'Stock keeping unit', 'confidence': .99},
                          {'dataset_id': right_id, 'path': 'item_code', 'meaning': 'Merchandise code', 'confidence': .99}]},
            {'name': 'category', 'dtype': 'string', 'meaning': 'Merchandise grouping',
             'bindings': [{'dataset_id': left_id, 'path': 'department', 'meaning': 'Sales department', 'confidence': .96},
                          {'dataset_id': right_id, 'path': 'class', 'meaning': 'Merchandise class', 'confidence': .96}]},
        ],
        'source_keys': [{'dataset_id': left_id, 'path': 'sku', 'role': 'primary_candidate',
                         'reason': 'Identifies one merchandise record'}],
        'combination': {'strategy': 'append', 'confidence': .97, 'reason': 'Both sources are item catalogs'},
    })


@pytest.mark.parametrize('duplicate', [False, True])
def test_discovery_validates_unsampled_cross_source_key_and_requires_review(monkeypatch, duplicate):
    left = save_generated_rows(({'sku': f'A{i}', 'department': 'home'} for i in range(1001)),
                               'left.csv', {'schema': {'sku': ['string'], 'department': ['string']}})
    right = save_rows([{'item_code': ' A1000 ' if duplicate else 'B1', 'class': 'outdoors'}], 'right.csv')
    response = fixture_response(left['id'], right['id'])
    monkeypatch.setattr(discovery, 'provider', lambda: 'groq')

    def model(task, payload, schema, **kwargs):
        assert schema is discovery.DiscoveryResponse
        profiles = json.loads(payload)['sources']
        assert [p['row_count'] for p in profiles] == [1001, 1]
        assert 'A1000' not in profiles[0]['columns'][1]['samples']
        return response, {'provider': 'test'}

    monkeypatch.setattr(discovery, 'structured', model)
    client = TestClient(app)
    result = client.post('/api/mappings/discover', json={
        'dataset_ids': [left['id'], right['id']], 'instructions': 'Infer the item catalog schema and keys',
    })
    assert result.status_code == 200, result.text
    proposal = result.json()
    evidence = proposal['discovery']['target_key_check']
    assert evidence['rows'] == 1002 and evidence['duplicate_rows'] == int(duplicate)
    assert proposal['discovery']['source_keys'][0]['check']['unique_and_nonblank']
    if duplicate:
        assert proposal['id'] is None and proposal['spec'] is None
        return
    assert proposal['spec']['columns'][0]['dtype'] == 'string'
    assert proposal['datasets'][1]['mappings'][1]['evidence'] == ['Merchandise grouping']
    decisions = {left['id']: {'item_id': 'department', 'category': 'sku'},
                 right['id']: {'item_id': 'item_code', 'category': 'class'}}
    with pytest.raises(ValueError, match='Changing an inferred primary-key'):
        semantic_mapping.approve_mapping(proposal['id'], semantic_mapping.MappingApproval(approved=True, decisions=decisions))
    decisions[left['id']] = {'item_id': 'sku', 'category': 'department'}
    approval = semantic_mapping.approve_mapping(proposal['id'], semantic_mapping.MappingApproval(approved=True, decisions=decisions))
    assert approval['status'] == 'approved'


def test_missing_append_source_is_named_and_blocks_approval(monkeypatch):
    left = save_rows([{'sku': 'A', 'department': 'Home'}], 'items.csv')
    right = save_rows([{'item_code': 'B', 'class': 'Outdoor'}], 'extra_items.csv')
    response = fixture_response(left['id'], right['id'])
    for column in response.columns:
        column.bindings = [binding for binding in column.bindings if binding.dataset_id == left['id']]

    def model(task, payload, schema, *, result_correction):
        assert right['id'] in result_correction(response)
        return response, {}

    monkeypatch.setattr(discovery, 'provider', lambda: 'groq')
    monkeypatch.setattr(discovery, 'structured', model)
    proposal = discovery.discover(discovery.DiscoveryRequest(
        dataset_ids=[left['id'], right['id']], instructions='Append both product catalogs',
    ))
    assert proposal['id'] is None and proposal['spec'] is None
    assert not proposal['combination']['eligible']
    assert 'extra_items.csv' in proposal['discovery']['blocked_reason']
    assert proposal['datasets'][1]['warnings']
    with store.db() as conn:
        assert conn.execute('SELECT count(*) FROM mapping_proposals').fetchone()[0] == 0


def test_join_source_can_participate_through_its_key_alone(monkeypatch):
    left = save_rows([{'sku': 'A', 'department': 'Home'}, {'sku': 'B', 'department': 'Outdoor'}], 'items.csv')
    right = save_rows([{'item_code': 'A', 'class': 'Home'}], 'permitted_items.csv')
    response = fixture_response(left['id'], right['id'])
    for column in response.columns:
        column.bindings = [binding for binding in column.bindings if binding.dataset_id == left['id']]
    response.combination = semantic_mapping.CombinationPlan(
        strategy='inner_join', left_dataset_id=left['id'], right_dataset_id=right['id'],
        left_key='sku', right_key='item_code', confidence=.99, reason='Only include permitted items',
    )

    def model(task, payload, schema, *, result_correction):
        assert result_correction(response) is None
        return response, {}

    monkeypatch.setattr(discovery, 'provider', lambda: 'groq')
    monkeypatch.setattr(discovery, 'structured', model)
    proposal = discovery.discover(discovery.DiscoveryRequest(
        dataset_ids=[left['id'], right['id']], instructions='Only include permitted items and left-side fields',
    ))
    assert proposal['spec'] and proposal['combination']['preview']['dropped_left_rows'] == 1
    assert proposal['datasets'][1]['warnings'] == [
        'This source participates through its join key. No output columns were selected from it.'
    ]


def test_ai_equivalences_are_visible_without_a_join(monkeypatch):
    left = save_rows([{'sku': 'A', 'department': 'Home', 'Design Number': 'D7'}], 'items.csv')
    right = save_rows([{'item_code': 'B', 'class': 'Outdoor', 'Style': 'D7'}], 'styles.csv')
    response = fixture_response(left['id'], right['id'])
    response.combination = semantic_mapping.CombinationPlan(
        strategy='none', confidence=.9, reason='Target grain is unclear',
    )
    match = discovery.EquivalentFields(
        left_dataset_id=left['id'], left_path='Design Number',
        right_dataset_id=right['id'], right_path='Style',
        meaning='Design reference', reason='The field names and example code patterns indicate the same design concept.',
    )
    response.field_equivalences = [match]
    monkeypatch.setattr(discovery, 'provider', lambda: 'groq')
    monkeypatch.setattr(discovery, 'structured', lambda *args, **kwargs: (response, {}))
    request = discovery.DiscoveryRequest(
        dataset_ids=[left['id'], right['id']], instructions='Compare the product catalogs and their design fields',
    )
    proposal = discovery.discover(request)
    assert proposal['id'] is None and proposal['spec'] is None
    assert proposal['discovery']['field_equivalences'] == [match.model_dump()]
    response.field_equivalences = [match.model_copy(update={'right_path': 'unavailable_field'})]
    with pytest.raises(ValueError, match='outside the selected source schemas'):
        discovery.discover(request)
