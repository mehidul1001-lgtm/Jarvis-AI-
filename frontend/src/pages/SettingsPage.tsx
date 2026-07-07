import { useEffect, useState, type FormEvent } from 'react';
import { ApiError } from '../api/client';
import { authApi } from '../api/endpoints';
import { Button, Card, ErrorBanner, Field, Spinner } from '../components/ui';
import { useAuth } from '../context/AuthContext';
import type { SessionInfo } from '../types';

export function SettingsPage() {
  const { user, refreshUser } = useAuth();

  const [fullName, setFullName] = useState(user?.full_name ?? '');
  const [profileMessage, setProfileMessage] = useState('');
  const [profileError, setProfileError] = useState('');
  const [savingProfile, setSavingProfile] = useState(false);

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [passwordMessage, setPasswordMessage] = useState('');
  const [passwordError, setPasswordError] = useState('');
  const [savingPassword, setSavingPassword] = useState(false);

  const [sessions, setSessions] = useState<SessionInfo[] | null>(null);

  useEffect(() => {
    authApi
      .sessions()
      .then(setSessions)
      .catch(() => setSessions([]));
  }, []);

  const saveProfile = async (event: FormEvent) => {
    event.preventDefault();
    setProfileMessage('');
    setProfileError('');
    setSavingProfile(true);
    try {
      await authApi.updateMe(fullName);
      await refreshUser();
      setProfileMessage('Profile updated');
    } catch (err) {
      setProfileError(err instanceof ApiError ? err.message : 'Failed to update profile');
    } finally {
      setSavingProfile(false);
    }
  };

  const savePassword = async (event: FormEvent) => {
    event.preventDefault();
    setPasswordMessage('');
    setPasswordError('');
    setSavingPassword(true);
    try {
      const result = await authApi.changePassword(currentPassword, newPassword);
      setPasswordMessage(result.message);
      setCurrentPassword('');
      setNewPassword('');
    } catch (err) {
      setPasswordError(err instanceof ApiError ? err.message : 'Failed to change password');
    } finally {
      setSavingPassword(false);
    }
  };

  const revoke = async (sessionId: string) => {
    await authApi.revokeSession(sessionId);
    setSessions(await authApi.sessions());
  };

  return (
    <div className="max-w-3xl space-y-6">
      <h1 className="text-2xl font-bold">Settings</h1>

      <Card title="Profile">
        <form onSubmit={saveProfile} className="space-y-4">
          {profileError && <ErrorBanner message={profileError} />}
          {profileMessage && (
            <p className="text-sm text-emerald-600 dark:text-emerald-400">{profileMessage}</p>
          )}
          <Field
            id="settings-name"
            label="Full name"
            value={fullName}
            required
            onChange={(e) => setFullName(e.target.value)}
          />
          <div className="text-sm text-slate-400">
            Email: <span className="font-medium text-slate-600 dark:text-slate-300">{user?.email}</span>{' '}
            · Role: <span className="font-medium capitalize">{user?.role}</span>
          </div>
          <Button type="submit" loading={savingProfile}>
            Save profile
          </Button>
        </form>
      </Card>

      <Card title="Change password">
        <form onSubmit={savePassword} className="space-y-4">
          {passwordError && <ErrorBanner message={passwordError} />}
          {passwordMessage && (
            <p className="text-sm text-emerald-600 dark:text-emerald-400">{passwordMessage}</p>
          )}
          <Field
            id="current-password"
            label="Current password"
            type="password"
            autoComplete="current-password"
            required
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
          />
          <Field
            id="new-password"
            label="New password"
            type="password"
            autoComplete="new-password"
            required
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
          />
          <p className="text-xs text-slate-400">
            Changing your password signs out every session, including this one.
          </p>
          <Button type="submit" loading={savingPassword}>
            Update password
          </Button>
        </form>
      </Card>

      <Card title="Active sessions">
        {sessions === null ? (
          <div className="flex justify-center py-6">
            <Spinner />
          </div>
        ) : sessions.length === 0 ? (
          <p className="text-sm text-slate-400">No active sessions.</p>
        ) : (
          <ul className="space-y-3">
            {sessions.map((session) => (
              <li
                key={session.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 p-3 dark:border-slate-800"
              >
                <div>
                  <p className="text-sm font-medium">
                    {session.ip_address ?? 'Unknown IP'} ·{' '}
                    {new Date(session.created_at).toLocaleString()}
                  </p>
                  <p className="max-w-md truncate text-xs text-slate-400">
                    {session.user_agent ?? 'Unknown device'}
                  </p>
                </div>
                <Button variant="secondary" onClick={() => revoke(session.id)}>
                  Revoke
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
