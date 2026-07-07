import { useCallback, useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import { auditApi } from '../api/endpoints';
import { Badge, Button, Card, EmptyState, ErrorBanner, Spinner } from '../components/ui';
import type { AuditLogEntry } from '../types';

function actionColor(action: string): 'green' | 'red' | 'amber' | 'slate' {
  if (action.includes('failed') || action.includes('reuse') || action.includes('blocked')) {
    return 'red';
  }
  if (action.includes('login') || action.includes('register')) return 'green';
  if (action.includes('revoked') || action.includes('password')) return 'amber';
  return 'slate';
}

export function LogsPage() {
  const [entries, setEntries] = useState<AuditLogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [actionFilter, setActionFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const pageSize = 50;

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = await auditApi.list(page, pageSize, actionFilter || undefined);
      setEntries(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load audit logs');
    } finally {
      setLoading(false);
    }
  }, [page, actionFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Audit Logs</h1>
        <input
          className="input !w-64"
          placeholder="Filter by action (e.g. auth.login)"
          value={actionFilter}
          onChange={(e) => {
            setPage(1);
            setActionFilter(e.target.value);
          }}
          aria-label="Filter audit logs by action"
        />
      </div>
      {error && <ErrorBanner message={error} />}
      <Card>
        {loading ? (
          <div className="flex justify-center py-12">
            <Spinner />
          </div>
        ) : entries.length === 0 ? (
          <EmptyState title="No audit entries" subtitle="Matching events will appear here." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                  <th className="px-3 py-2">Time</th>
                  <th className="px-3 py-2">Action</th>
                  <th className="px-3 py-2">Resource</th>
                  <th className="px-3 py-2">IP</th>
                  <th className="px-3 py-2">Detail</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => (
                  <tr
                    key={entry.id}
                    className="border-b border-slate-100 last:border-0 dark:border-slate-800/60"
                  >
                    <td className="whitespace-nowrap px-3 py-3 text-slate-500 dark:text-slate-400">
                      {new Date(entry.created_at).toLocaleString()}
                    </td>
                    <td className="px-3 py-3">
                      <Badge color={actionColor(entry.action)}>{entry.action}</Badge>
                    </td>
                    <td className="px-3 py-3 text-slate-500 dark:text-slate-400">
                      {entry.resource ?? '—'}
                    </td>
                    <td className="px-3 py-3 text-slate-500 dark:text-slate-400">
                      {entry.ip_address ?? '—'}
                    </td>
                    <td className="max-w-xs truncate px-3 py-3 text-xs text-slate-400">
                      {entry.detail ? JSON.stringify(entry.detail) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {totalPages > 1 && (
          <div className="mt-4 flex items-center justify-between text-sm">
            <Button variant="secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
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
