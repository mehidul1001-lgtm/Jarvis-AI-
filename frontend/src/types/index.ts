export type UserRole = 'admin' | 'manager' | 'user';

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface SessionInfo {
  id: string;
  user_agent: string | null;
  ip_address: string | null;
  created_at: string;
  expires_at: string;
}

export interface AuditLogEntry {
  id: string;
  user_id: string | null;
  action: string;
  resource: string | null;
  detail: Record<string, unknown> | null;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
}

export interface HealthStatus {
  status: string;
  version: string;
  environment: string;
  database: string;
}

export interface ApiErrorBody {
  error?: { code: string; message: string };
  detail?: unknown;
}

// --- Phase 2: AI brain -------------------------------------------------

export interface Conversation {
  id: string;
  title: string;
  summary: string | null;
  total_input_tokens: number;
  total_output_tokens: number;
  created_at: string;
  updated_at: string;
}

export interface ChatMessageMeta {
  confidence: number | null;
  latency_ms: number | null;
  tool_calls: string[];
  input_tokens: number | null;
  output_tokens: number | null;
  revised: boolean;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  created_at: string | null;
  meta: ChatMessageMeta;
}

export type MemoryKind =
  | 'conversation_summary'
  | 'preference'
  | 'product'
  | 'supplier'
  | 'customer'
  | 'project'
  | 'code_note'
  | 'business_rule'
  | 'task_note'
  | 'goal'
  | 'decision'
  | 'learned'
  | 'document';

export interface MemoryEntry {
  id: string;
  kind: MemoryKind;
  title: string;
  content: string;
  importance: number;
  source: string | null;
  detail: Record<string, unknown> | null;
  access_count: number;
  created_at: string;
  updated_at: string;
}

export interface MemorySearchHit {
  entry: MemoryEntry;
  score: number;
}

export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface WorkflowTask {
  id: string;
  title: string;
  description: string | null;
  handler: string;
  status: TaskStatus;
  priority: number;
  progress: number;
  progress_note: string | null;
  attempts: number;
  error: string | null;
  result: Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface AgentStatus {
  name: string;
  display_name: string;
  description: string;
  healthy: boolean;
  available: boolean;
  last_heartbeat: string | null;
  runs_completed: number;
  runs_failed: number;
  tools: string[];
}

/** Events pushed over the realtime WebSocket. */
export interface RealtimeEvent {
  type: string;
  [key: string]: unknown;
}
