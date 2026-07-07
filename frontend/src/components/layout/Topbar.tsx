import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { useTheme } from '../../context/ThemeContext';
import type { RealtimeStatus } from '../../hooks/useRealtime';
import { Badge } from '../ui';

const statusColor: Record<RealtimeStatus, 'green' | 'amber' | 'red'> = {
  connected: 'green',
  connecting: 'amber',
  disconnected: 'red',
};

export function Topbar({
  onMenuClick,
  realtime,
}: {
  onMenuClick: () => void;
  realtime: RealtimeStatus;
}) {
  const { user, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  return (
    <header className="flex h-16 items-center justify-between border-b border-slate-200 bg-white px-4 dark:border-slate-800 dark:bg-slate-900 lg:px-6">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onMenuClick}
          className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 lg:hidden"
          aria-label="Open navigation menu"
        >
          <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
        <Badge color={statusColor[realtime]}>
          {realtime === 'connected' ? 'Realtime: online' : `Realtime: ${realtime}`}
        </Badge>
      </div>

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={toggleTheme}
          className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
        >
          {theme === 'dark' ? '☀️' : '🌙'}
        </button>
        <div className="hidden text-right sm:block">
          <p className="text-sm font-semibold">{user?.full_name}</p>
          <p className="text-xs capitalize text-slate-400">{user?.role}</p>
        </div>
        <button type="button" onClick={handleLogout} className="btn-secondary">
          Sign out
        </button>
      </div>
    </header>
  );
}
