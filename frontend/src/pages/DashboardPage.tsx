import { useEffect, useState } from 'react';
import { systemApi } from '../api/endpoints';
import { Badge, Card } from '../components/ui';
import { useAuth } from '../context/AuthContext';
import type { HealthStatus } from '../types';

interface AgentSummary {
  name: string;
  description: string;
  phase: string;
}

const PLANNED_AGENTS: AgentSummary[] = [
  { name: 'Amazon Agent', description: 'Inventory, PPC, listings, profitability', phase: 'Phase 3' },
  { name: 'Finance Agent', description: 'Expenses, income, P&L, cash flow', phase: 'Phase 3' },
  { name: 'Developer Agent', description: 'Repository inspection, bug fixes, refactoring', phase: 'Phase 3' },
  { name: 'Operations Agent', description: 'Daily reports, reminders, workflow automation', phase: 'Phase 3' },
  { name: 'Product Research Agent', description: 'Suppliers, competition, IP risk, sourcing', phase: 'Phase 3' },
  { name: 'Marketing Agent', description: 'SEO, A+ content, advertising recommendations', phase: 'Phase 3' },
];

export function DashboardPage() {
  const { user } = useAuth();
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [healthError, setHealthError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const status = await systemApi.health();
        if (!cancelled) {
          setHealth(status);
          setHealthError(false);
        }
      } catch {
        if (!cancelled) setHealthError(true);
      }
    };
    load();
    const interval = window.setInterval(load, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">
          Welcome, {user?.full_name?.split(' ')[0] ?? 'there'} 👋
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          Your business operating system. AI chat, agents and analytics come online in the next
          phases.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Card title="System health">
          {healthError ? (
            <Badge color="red">API unreachable</Badge>
          ) : health ? (
            <div className="space-y-2">
              <Badge color={health.status === 'ok' ? 'green' : 'amber'}>
                {health.status === 'ok' ? 'All systems operational' : 'Degraded'}
              </Badge>
              <p className="text-xs text-slate-400">
                v{health.version} · {health.environment} · database {health.database}
              </p>
            </div>
          ) : (
            <p className="text-sm text-slate-400">Checking…</p>
          )}
        </Card>
        <Card title="AI tasks">
          <p className="text-3xl font-bold">—</p>
          <p className="mt-1 text-xs text-slate-400">Task queue arrives with Phase 2</p>
        </Card>
        <Card title="Sales (30d)">
          <p className="text-3xl font-bold">—</p>
          <p className="mt-1 text-xs text-slate-400">Connects to Amazon Agent in Phase 3</p>
        </Card>
        <Card title="Net profit (30d)">
          <p className="text-3xl font-bold">—</p>
          <p className="mt-1 text-xs text-slate-400">Connects to Finance Agent in Phase 3</p>
        </Card>
      </div>

      <Card title="Agent roster">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {PLANNED_AGENTS.map((agent) => (
            <div
              key={agent.name}
              className="rounded-lg border border-slate-200 p-4 dark:border-slate-800"
            >
              <div className="flex items-center justify-between">
                <p className="font-semibold">{agent.name}</p>
                <Badge color="slate">{agent.phase}</Badge>
              </div>
              <p className="mt-1 text-sm text-slate-400">{agent.description}</p>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
