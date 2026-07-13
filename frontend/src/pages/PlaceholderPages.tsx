import { Card } from '../components/ui';

function ComingSoon({
  title,
  phase,
  description,
}: {
  title: string;
  phase: string;
  description: string;
}) {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">{title}</h1>
      <Card>
        <div className="flex flex-col items-center py-16 text-center">
          <span className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-600/10 text-2xl">
            🚧
          </span>
          <p className="text-lg font-semibold">Arriving in {phase}</p>
          <p className="mt-2 max-w-md text-sm text-slate-400">{description}</p>
        </div>
      </Card>
    </div>
  );
}

export function AgentsPage() {
  return (
    <ComingSoon
      title="Agents"
      phase="Phase 3"
      description="Specialized agents for Amazon operations, finance, development, product research, marketing and daily operations."
    />
  );
}

export function AnalyticsPage() {
  return (
    <ComingSoon
      title="Analytics"
      phase="Phase 5"
      description="Sales, expenses, profit, inventory and performance analytics across your whole business."
    />
  );
}
