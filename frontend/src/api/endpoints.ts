import type {
  AuditLogEntry,
  HealthStatus,
  Page,
  SessionInfo,
  TokenPair,
  User,
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
