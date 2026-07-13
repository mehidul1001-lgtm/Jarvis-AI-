import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError } from '../api/client';
import { memoryApi } from '../api/endpoints';
import { Badge, Button, Card, EmptyState, ErrorBanner, Field, Spinner } from '../components/ui';
import type { MemoryEntry, MemoryKind } from '../types';

const KINDS: MemoryKind[] = [
  'preference', 'product', 'supplier', 'customer', 'project', 'code_note',
  'business_rule', 'task_note', 'goal', 'decision', 'learned', 'document',
  'conversation_summary',
];

export function MemoryPage() {
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [kind, setKind] = useState<MemoryKind | ''>('');
  const [query, setQuery] = useState('');
  const [scores, setScores] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<MemoryEntry | null>(null);
  const [creating, setCreating] = useState(false);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const pageSize = 20;

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      if (query.trim()) {
        const hits = await memoryApi.search(query.trim(), kind || undefined);
        setEntries(hits.map((hit) => hit.entry));
        setScores(Object.fromEntries(hits.map((hit) => [hit.entry.id, hit.score])));
        setTotal(hits.length);
      } else {
        const result = await memoryApi.list(kind || undefined, page, pageSize);
        setEntries(result.items);
        setScores({});
        setTotal(result.total);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load memories');
    } finally {
      setLoading(false);
    }
  }, [query, kind, page]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), query ? 300 : 0);
    return () => window.clearTimeout(timer);
  }, [load, query]);

  const remove = async (id: string) => {
    await memoryApi.remove(id);
    await load();
  };

  const exportAll = async () => {
    const data = await memoryApi.exportAll();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `jarvis-memory-${new Date().toISOString().slice(0, 10)}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const importFile = async (file: File) => {
    setError('');
    try {
      const parsed = JSON.parse(await file.text()) as { items?: unknown[] };
      const items = Array.isArray(parsed) ? parsed : parsed.items;
      if (!Array.isArray(items)) throw new Error('File must contain an items array');
      await memoryApi.importItems(items);
      await load();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : `Import failed: ${(err as Error).message}`,
      );
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Memory Explorer</h1>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={exportAll}>
            Export
          </Button>
          <Button variant="secondary" onClick={() => fileInput.current?.click()}>
            Import
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept="application/json"
            className="hidden"
            aria-label="Import memory JSON file"
            onChange={(changeEvent) => {
              const file = changeEvent.target.files?.[0];
              if (file) void importFile(file);
              changeEvent.target.value = '';
            }}
          />
          <Button onClick={() => setCreating(true)}>+ Add memory</Button>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <input
          className="input !w-72"
          placeholder="Semantic search…"
          value={query}
          onChange={(changeEvent) => {
            setPage(1);
            setQuery(changeEvent.target.value);
          }}
          aria-label="Search memories"
        />
        <select
          className="input !w-52"
          value={kind}
          onChange={(changeEvent) => {
            setPage(1);
            setKind(changeEvent.target.value as MemoryKind | '');
          }}
          aria-label="Filter by kind"
        >
          <option value="">All kinds</option>
          {KINDS.map((option) => (
            <option key={option} value={option}>
              {option.replace(/_/g, ' ')}
            </option>
          ))}
        </select>
      </div>

      {error && <ErrorBanner message={error} />}

      {loading ? (
        <div className="flex justify-center py-12">
          <Spinner />
        </div>
      ) : entries.length === 0 ? (
        <Card>
          <EmptyState
            title="No memories"
            subtitle="JARVIS stores what it learns here; you can also add entries manually."
          />
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {entries.map((entry) => (
            <Card key={entry.id} className="!p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge color="cyan">{entry.kind.replace(/_/g, ' ')}</Badge>
                    {scores[entry.id] !== undefined && (
                      <Badge color="slate">score {scores[entry.id].toFixed(2)}</Badge>
                    )}
                    <span
                      className="text-xs text-slate-400"
                      title="Importance drives retrieval ranking"
                    >
                      ★ {entry.importance.toFixed(2)}
                    </span>
                  </div>
                  <p className="mt-1 truncate font-semibold">{entry.title}</p>
                </div>
                <div className="flex shrink-0 gap-1">
                  <button
                    type="button"
                    className="rounded p-1 text-slate-400 hover:text-brand-500"
                    onClick={() => setEditing(entry)}
                    aria-label={`Edit ${entry.title}`}
                  >
                    ✏️
                  </button>
                  <button
                    type="button"
                    className="rounded p-1 text-slate-400 hover:text-red-500"
                    onClick={() => void remove(entry.id)}
                    aria-label={`Delete ${entry.title}`}
                  >
                    🗑️
                  </button>
                </div>
              </div>
              <p className="mt-2 line-clamp-4 whitespace-pre-wrap text-sm text-slate-500 dark:text-slate-400">
                {entry.content}
              </p>
              <p className="mt-2 text-xs text-slate-400">
                {entry.source && <>from {entry.source} · </>}
                accessed {entry.access_count}× · updated{' '}
                {new Date(entry.updated_at).toLocaleDateString()}
              </p>
            </Card>
          ))}
        </div>
      )}

      {!query && totalPages > 1 && (
        <div className="flex items-center justify-between text-sm">
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

      {(creating || editing) && (
        <MemoryEditor
          entry={editing}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          onSaved={async () => {
            setCreating(false);
            setEditing(null);
            await load();
          }}
        />
      )}
    </div>
  );
}

function MemoryEditor({
  entry,
  onClose,
  onSaved,
}: {
  entry: MemoryEntry | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [kind, setKind] = useState<MemoryKind>(entry?.kind ?? 'learned');
  const [title, setTitle] = useState(entry?.title ?? '');
  const [content, setContent] = useState(entry?.content ?? '');
  const [importance, setImportance] = useState(entry?.importance ?? 0.5);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const submit = async (formEvent: FormEvent) => {
    formEvent.preventDefault();
    setSaving(true);
    setError('');
    try {
      if (entry) {
        await memoryApi.update(entry.id, { kind, title, content, importance });
      } else {
        await memoryApi.create({ kind, title, content, importance });
      }
      await onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="card w-full max-w-lg p-6"
        onClick={(clickEvent) => clickEvent.stopPropagation()}
      >
        <h2 className="mb-4 text-lg font-bold">{entry ? 'Edit memory' : 'New memory'}</h2>
        <form onSubmit={submit} className="space-y-3">
          {error && <ErrorBanner message={error} />}
          <select
            className="input"
            value={kind}
            onChange={(changeEvent) => setKind(changeEvent.target.value as MemoryKind)}
            aria-label="Memory kind"
          >
            {KINDS.map((option) => (
              <option key={option} value={option}>
                {option.replace(/_/g, ' ')}
              </option>
            ))}
          </select>
          <Field
            id="memory-title"
            label="Title"
            required
            value={title}
            onChange={(changeEvent) => setTitle(changeEvent.target.value)}
          />
          <div className="space-y-1">
            <label
              htmlFor="memory-content"
              className="block text-sm font-medium text-slate-700 dark:text-slate-300"
            >
              Content
            </label>
            <textarea
              id="memory-content"
              className="input min-h-32"
              required
              value={content}
              onChange={(changeEvent) => setContent(changeEvent.target.value)}
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="memory-importance" className="block text-sm font-medium">
              Importance: {importance.toFixed(2)}
            </label>
            <input
              id="memory-importance"
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={importance}
              onChange={(changeEvent) => setImportance(Number(changeEvent.target.value))}
              className="w-full"
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" type="button" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={saving}>
              Save
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
