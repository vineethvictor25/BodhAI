export type TableOption={id:string;table:string;database?:string;schema?:string}
export type QueryResult = {
  status: 'answered' | 'clarification' | 'refused' | 'failed'; answer: string; reason: string; tables: string[];
  queries: { sql: string; parameters: Record<string, unknown>; result: { columns: string[]; rows: unknown[][]; returned_rows: number; truncated: boolean;rows_retained?:boolean } }[];
  assumptions: string[]; workspaces: string[]; attempts: number; model_calls: number;
  example_ids: string[]; pruning: { original_columns?: number; prompt_columns?: number; kept_columns?: number };
}
export type QueryJob = {
  submission_replayed?:boolean;
  cancellation_requested?:boolean;
  id: string; question: string; workspace: string; model: string | null; source?: string;
  status: 'queued' | 'running' | 'awaiting_confirmation' | 'awaiting_sql_approval' | 'recoverable' | 'completed' | 'failed'; created_at: string; elapsed_seconds: number; expires_at?:string; recovery_available?:boolean;
  sql_approval?:{version:string;fingerprint:string;sql:string;parameters:Record<string,unknown>;tables:string[];row_limit:number;execution_timeout_seconds:number;timeout_seconds:number;read_scope:string;checks:string;deadline:number;execution_mode?:'direct'|'materialized'|'operator';adapter_capabilities?:{tls?:{encryption_required:boolean;tls12_enforced:boolean;certificate_verification_when_encrypted:boolean;minimum_version:string}};resource_limits?:{maximum_bytes_billed:number;location:string};pii_limits?:{max_rows:number;max_copy_bytes:number;max_input_bytes:number;max_envelope_bytes:number;max_cell_bytes:number;max_decrypt_cells:number;timeout_seconds:number};copy_scope?:{table:string;columns:string[];decrypt_columns:string[];row_marker_only:boolean;source_sql:string;parameters:Record<string,unknown>}[];row_security?:{enabled:boolean;admin_bypass:boolean;summary:string;filters:{table:string;mode:string;sql:string;parameters:Record<string,unknown>}[]}}|null;
  confirmation?: { version: string; tables: string[]; available_tables: string[];table_options?:TableOption[]; workspaces: string[]; reason: string; timeout_seconds: number } | null;
  events: { stage: string; status: string; label: string; at: string }[]; result: QueryResult | null; error: string | null;
  planned_tables: string[]; planned_sql: string | null; planned_parameters: Record<string, unknown>;
}
export type AppConfig = {
  app_name: string; dataset: string; table_count: number;
  job_cache_ttl_seconds?:number;history_retention_seconds?:number;history_stores_result_rows?:boolean;
  source?: string; sources?: { id: string; name: string; kind: string; configured: boolean }[];
  model: { configured: boolean; default: string | null; models: string[]; policy?:{allowed:boolean;message:string} };
  workspaces: { id: string; name?: string; description: string }[];
  suggestions: { title: string; question: string; workspace: string; icon: string }[];
}
export type Table = { table: string; columns: { name: string; type: string; primary_key: boolean; nullable: boolean }[];
  foreign_keys: { column: string; references_table: string; references_column: string }[] }
export type Metric = { id: string; name: string; definition: string; unit: string; date: string; grain: string; filters: string[]; workspaces: string[] }

export type AccessGrant = {source:string;domain:string}
export type Account = {id:string;username:string;role:'admin'|'member';pii:number;enabled:number;revision:number;can_view_pii:boolean;groups:string[];grants:AccessGrant[];direct_grants:AccessGrant[]}
let csrfToken=''
export const setCsrfToken=(token:string)=>{csrfToken=token}
export function displayValue(value:unknown):string{
  if(value===null)return 'NULL'
  if(typeof value==='object'&&value){
    const scalar=value as Record<string,unknown>
    if((['decimal','integer'].includes(String(scalar.$qd_type))||scalar.type==='decimal')&&typeof scalar.value==='string')return scalar.value
  }
  return String(value)
}
export class RequestError extends Error {constructor(message:string,public status:number){super(message)}}

export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { credentials:'same-origin', ...init, headers: { 'Content-Type': 'application/json', ...(csrfToken?{'X-CSRF-Token':csrfToken}:{}), ...init?.headers } })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = typeof data?.detail === 'string' ? data.detail : response.status === 422 ? 'Please check your question and selected options.' : 'Could not reach the data service. Please try again.'
    if(response.status===401 && !['/api/auth/login','/api/auth/me'].includes(url))window.dispatchEvent(new Event('querydesk-session-expired'))
    throw new RequestError(detail,response.status)
  }
  return data as T
}

