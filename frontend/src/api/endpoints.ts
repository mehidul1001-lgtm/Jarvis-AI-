import type {
  AgentStatus,
  AuditLogEntry,
  ChatMessage,
  Conversation,
  HealthStatus,
  MemoryEntry,
  MemoryKind,
  MemorySearchHit,
  Page,
  SessionInfo,
  TaskStatus,
  TokenPair,
  User,
  WorkflowTask,
} from '../types';
import { api, tokenStore } from './client';

export const authApi = {
  register(email: string, fullName: string, password: string): Promise<User> {
    return api<User>('/auth/register', {
      method: 'POST',
      auth: false,
      body: { email, full_name: fullName, password },
    });
  },
  async login(email: string, password: string): Promise<TokenPair> {
    const pair = await api<TokenPair>('/auth/login', {
      method: 'POST',
      auth: false,
      body: { email, password },
    });
    tokenStore.set(pair);
    return pair;
  },
  async logout(): Promise<void> {
    const refreshToken = tokenStore.getRefresh();
    if (refreshToken) {
      try {
        await api('/auth/logout', {
          method: 'POST',
          body: { refresh_token: refreshToken },
        });
      } catch {
        // Best effort: clear local state even if the server call fails.
      }
    }
    tokenStore.clear();
  },
  me(): Promise<User> {
    return api<User>('/auth/me');
  },
  updateMe(fullName: string): Promise<User> {
    return api<User>('/auth/me', { method: 'PATCH', body: { full_name: fullName } });
  },
  changePassword(currentPassword: string, newPassword: string): Promise<{ message: string }> {
    return api('/auth/me/password', {
      method: 'POST',
      body: { current_password: currentPassword, new_password: newPassword },
    });
  },
  sessions(): Promise<SessionInfo[]> {
    return api<SessionInfo[]>('/auth/me/sessions');
  },
  revokeSession(sessionId: string): Promise<{ message: string }> {
    return api(`/auth/me/sessions/${sessionId}`, { method: 'DELETE' });
  },
};

export const systemApi = {
  health(): Promise<HealthStatus> {
    return api<HealthStatus>('/health', { auth: false });
  },
};

export const usersApi = {
  list(page = 1, pageSize = 20): Promise<Page<User>> {
    return api<Page<User>>(`/users?page=${page}&page_size=${pageSize}`);
  },
  update(
    userId: string,
    payload: Partial<Pick<User, 'full_name' | 'role' | 'is_active'>>,
  ): Promise<User> {
    return api<User>(`/users/${userId}`, { method: 'PATCH', body: payload });
  },
};

export const auditApi = {
  list(page = 1, pageSize = 50, action?: string): Promise<Page<AuditLogEntry>> {
    const filter = action ? `&action=${encodeURIComponent(action)}` : '';
    return api<Page<AuditLogEntry>>(`/audit?page=${page}&page_size=${pageSize}${filter}`);
  },
};

export const chatApi = {
  createConversation(title?: string): Promise<Conversation> {
    return api<Conversation>('/chat/conversations', {
      method: 'POST',
      body: title ? { title } : {},
    });
  },
  listConversations(page = 1, pageSize = 50): Promise<Page<Conversation>> {
    return api<Page<Conversation>>(`/chat/conversations?page=${page}&page_size=${pageSize}`);
  },
  renameConversation(id: string, title: string): Promise<Conversation> {
    return api<Conversation>(`/chat/conversations/${id}`, { method: 'PATCH', body: { title } });
  },
  deleteConversation(id: string): Promise<{ message: string }> {
    return api(`/chat/conversations/${id}`, { method: 'DELETE' });
  },
  messages(id: string): Promise<ChatMessage[]> {
    return api<ChatMessage[]>(`/chat/conversations/${id}/messages`);
  },
  send(id: string, text: string): Promise<ChatMessage> {
    return api<ChatMessage>(`/chat/conversations/${id}/messages`, {
      method: 'POST',
      body: { text },
    });
  },
};

export const memoryApi = {
  list(kind?: MemoryKind, page = 1, pageSize = 20): Promise<Page<MemoryEntry>> {
    const filter = kind ? `&kind=${kind}` : '';
    return api<Page<MemoryEntry>>(`/memory?page=${page}&page_size=${pageSize}${filter}`);
  },
  search(q: string, kind?: MemoryKind): Promise<MemorySearchHit[]> {
    const filter = kind ? `&kind=${kind}` : '';
    return api<MemorySearchHit[]>(`/memory/search?q=${encodeURIComponent(q)}${filter}`);
  },
  create(payload: {
    kind: MemoryKind;
    title: string;
    content: string;
    importance: number;
  }): Promise<MemoryEntry> {
    return api<MemoryEntry>('/memory', { method: 'POST', body: payload });
  },
  update(
    id: string,
    payload: Partial<Pick<MemoryEntry, 'kind' | 'title' | 'content' | 'importance'>>,
  ): Promise<MemoryEntry> {
    return api<MemoryEntry>(`/memory/${id}`, { method: 'PATCH', body: payload });
  },
  remove(id: string): Promise<{ message: string }> {
    return api(`/memory/${id}`, { method: 'DELETE' });
  },
  exportAll(): Promise<{ version: number; count: number; items: unknown[] }> {
    return api('/memory/export');
  },
  importItems(items: unknown[]): Promise<{ message: string }> {
    return api('/memory/import', { method: 'POST', body: { items } });
  },
};

export const tasksApi = {
  list(status?: TaskStatus, page = 1, pageSize = 20): Promise<Page<WorkflowTask>> {
    const filter = status ? `&status=${status}` : '';
    return api<Page<WorkflowTask>>(`/tasks?page=${page}&page_size=${pageSize}${filter}`);
  },
  create(payload: {
    title: string;
    agent: string;
    objective: string;
    priority?: number;
  }): Promise<WorkflowTask> {
    return api<WorkflowTask>('/tasks', { method: 'POST', body: payload });
  },
  cancel(id: string): Promise<WorkflowTask> {
    return api<WorkflowTask>(`/tasks/${id}/cancel`, { method: 'POST' });
  },
  summary(): Promise<Record<TaskStatus, number>> {
    return api('/tasks/summary');
  },
};

export const agentsApi = {
  list(): Promise<AgentStatus[]> {
    return api<AgentStatus[]>('/agents');
  },
};
