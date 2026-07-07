import { useCallback, useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import { usersApi } from '../api/endpoints';
import { Badge, Button, Card, EmptyState, ErrorBanner, Spinner } from '../components/ui';
import { useAuth } from '../context/AuthContext';
import type { User, UserRole } from '../types';

const roleBadge: Record<UserRole, 'cyan' | 'amber' | 'slate'> = {
  admin: 'cyan',
  manager: 'amber',
  user: 'slate',
};

export function UsersPage() {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const pageSize = 20;

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = await usersApi.list(page, pageSize);
      setUsers(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load users');
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    void load();
  }, [load]);

  const changeRole = async (target: User, role: UserRole) => {
    try {
      await usersApi.update(target.id, { role });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update role');
    }
  };

  const toggleActive = async (target: User) => {
    try {
      await usersApi.update(target.id, { is_active: !target.is_active });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update user');
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Users</h1>
      {error && <ErrorBanner message={error} />}
      <Card>
        {loading ? (
          <div className="flex justify-center py-12">
            <Spinner />
          </div>
        ) : users.length === 0 ? (
          <EmptyState title="No users found" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                  <th className="px-3 py-2">Name</th>
                  <th className="px-3 py-2">Email</th>
                  <th className="px-3 py-2">Role</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Last login</th>
                  <th className="px-3 py-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => {
                  const isSelf = u.id === currentUser?.id;
                  return (
                    <tr
                      key={u.id}
                      className="border-b border-slate-100 last:border-0 dark:border-slate-800/60"
                    >
                      <td className="px-3 py-3 font-medium">
                        {u.full_name}
                        {isSelf && <span className="ml-2 text-xs text-slate-400">(you)</span>}
                      </td>
                      <td className="px-3 py-3 text-slate-500 dark:text-slate-400">{u.email}</td>
                      <td className="px-3 py-3">
                        <Badge color={roleBadge[u.role]}>{u.role}</Badge>
                      </td>
                      <td className="px-3 py-3">
                        <Badge color={u.is_active ? 'green' : 'red'}>
                          {u.is_active ? 'Active' : 'Deactivated'}
                        </Badge>
                      </td>
                      <td className="px-3 py-3 text-slate-500 dark:text-slate-400">
                        {u.last_login_at ? new Date(u.last_login_at).toLocaleString() : 'Never'}
                      </td>
                      <td className="px-3 py-3">
                        {!isSelf && (
                          <div className="flex flex-wrap items-center gap-2">
                            <select
                              className="input !w-auto !py-1 text-xs"
                              value={u.role}
                              onChange={(e) => changeRole(u, e.target.value as UserRole)}
                              aria-label={`Change role for ${u.email}`}
                            >
                              <option value="user">user</option>
                              <option value="manager">manager</option>
                              <option value="admin">admin</option>
                            </select>
                            <Button variant="secondary" onClick={() => toggleActive(u)}>
                              {u.is_active ? 'Deactivate' : 'Activate'}
                            </Button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {totalPages > 1 && (
          <div className="mt-4 flex items-center justify-between text-sm">
            <Button
              variant="secondary"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              Previous
            </Button>
            <span className="text-slate-400">
              Page {page} of {totalPages}
            </span>
            <Button
              variant="secondary"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}
