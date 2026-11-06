"""Validate model-proposed source relationships without granting execution authority."""


def validate_plan(plan,request,profiles,threshold=None):
    result=plan.model_dump();lookup={d['id']:{c['path'] for c in columns} for d,columns in profiles}
    if request.combination_mode=='append' and plan.strategy!='append':raise ValueError('AI contradicted the explicit append choice')
    if request.combination_mode=='join' and plan.strategy=='append':raise ValueError('AI contradicted the explicit join choice')
    if plan.strategy in ('append','none'):
        if any((plan.left_dataset_id,plan.right_dataset_id,plan.left_key,plan.right_key)):raise ValueError('Append/none recommendations cannot include join bindings')
        result.update({'eligible':plan.strategy=='append','preview':{'mode':'map_each_source_then_append','input_rows':sum(d['row_count'] for d,_ in profiles),'note':'Execution appends normalized rows after mapping and DQ; it does not require identical raw headers or create a merged raw file.'} if plan.strategy=='append' else None})
        return result
    if len(profiles)!=2:raise ValueError('A suggested join must account for exactly two selected datasets; multi-step joins need separate reviewed stages')
    if plan.left_dataset_id==plan.right_dataset_id or {plan.left_dataset_id,plan.right_dataset_id}!=set(lookup):raise ValueError('AI invented, reused or omitted a source in the join recommendation')
    if plan.left_key not in lookup[plan.left_dataset_id] or plan.right_key not in lookup[plan.right_dataset_id]:raise ValueError('AI proposed a join key that does not exist in its source')
    result['eligible']=False
    minimum = request.spec.mapping_threshold if threshold is None else threshold
    if plan.confidence < minimum:
        result['blocked_reason']='Join confidence is below the review threshold; inspect the keys and use the manual merge workflow if appropriate'
        return result
    from .medallion_api import MergeRequest,preview_merge
    try:
        preview=preview_merge(MergeRequest(dataset_ids=[plan.left_dataset_id,plan.right_dataset_id],mode=plan.strategy,left_key=plan.left_key,right_key=plan.right_key))
        result.update({'eligible':True,'preview':preview})
    except ValueError as error:result['blocked_reason']=str(error)
    return result
