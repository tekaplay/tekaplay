'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { Badge, Button, Card, Eyebrow, Input } from '@/components/ui';
import { ApiError, del, patch, post } from '@/lib/api';
import { useAuthStore } from '@/lib/auth-store';
import { daysRemaining, useEntitlement } from '@/lib/organizations';
import { useTheme } from '@/lib/theme';
import { useToast } from '@/lib/toast';
import type { TrialOut, UserOut } from '@/lib/types';

function EntitlementCard() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const entitlement = useEntitlement();
  const [starting, setStarting] = useState(false);

  async function startTrial() {
    setStarting(true);
    try {
      await post<TrialOut>('/commerce/trial/start');
      await queryClient.invalidateQueries({ queryKey: ['commerce', 'entitlement'] });
      toast('Free trial started', 'success');
    } catch (e) {
      toast(e instanceof ApiError ? e.message : 'Could not start a trial.', 'danger');
    } finally {
      setStarting(false);
    }
  }

  if (entitlement.isPending) return null;
  const data = entitlement.data;

  return (
    <Card>
      <Eyebrow tone="muted">Access</Eyebrow>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        {data?.premium ? (
          <Badge tone="success">
            {data.source === 'subscription' && 'Premium // subscription'}
            {data.source === 'license' && 'Premium // organization license'}
            {data.source === 'trial' && 'Premium // free trial'}
          </Badge>
        ) : (
          <Badge>Free plan</Badge>
        )}
        {data?.trial && data.source === 'trial' && (
          <span className="font-mono text-xs text-ink-muted">
            {daysRemaining(data.trial.expires_at)} day
            {daysRemaining(data.trial.expires_at) === 1 ? '' : 's'} left
          </span>
        )}
      </div>
      {!data?.premium && !data?.trial && (
        <Button className="mt-4" size="sm" disabled={starting} onClick={startTrial}>
          {starting ? 'Starting…' : 'Start free trial'}
        </Button>
      )}
    </Card>
  );
}

export default function ProfilePage() {
  const router = useRouter();
  const toast = useToast();
  const { user, loadMe, clear } = useAuthStore();
  const { theme, setTheme } = useTheme();
  const [displayName, setDisplayName] = useState(user?.display_name ?? '');
  const [timezone, setTimezone] = useState(user?.timezone ?? 'UTC');
  const [busy, setBusy] = useState(false);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await patch<UserOut>('/users/me', {
        display_name: displayName,
        timezone,
        theme,
      });
      await loadMe();
      toast('Profile saved', 'success');
    } catch (e) {
      toast(e instanceof ApiError ? e.message : 'Could not save the profile.', 'danger');
    } finally {
      setBusy(false);
    }
  }

  async function deleteAccount() {
    const confirmed = window.confirm(
      'Delete your account? Progress, XP, and achievements are removed permanently.',
    );
    if (!confirmed) return;
    try {
      await del('/users/me');
      clear();
      router.replace('/');
    } catch (e) {
      toast(e instanceof ApiError ? e.message : 'Could not delete the account.', 'danger');
    }
  }

  return (
    <div className="flex max-w-lg flex-col gap-6">
      <div>
        <Eyebrow>Profile // {user?.email}</Eyebrow>
        <h1 className="mt-1 font-display text-3xl font-semibold">Your file</h1>
      </div>

      <EntitlementCard />

      <Card>
        <form onSubmit={save} className="flex flex-col gap-4">
          <Input label="Display name" name="display_name" value={displayName}
                 onChange={(e) => setDisplayName(e.target.value)} required maxLength={120} />
          <Input label="Timezone" name="timezone" value={timezone}
                 onChange={(e) => setTimezone(e.target.value)}
                 placeholder="e.g. Europe/Berlin" />
          <div className="flex flex-col gap-1.5">
            <label htmlFor="theme" className="text-sm font-medium">Theme</label>
            <select
              id="theme"
              value={theme}
              onChange={(e) => setTheme(e.target.value as 'light' | 'dark' | 'system')}
              className="rounded border border-line bg-surface px-3 py-2 text-sm"
            >
              <option value="system">Match system</option>
              <option value="dark">Dark</option>
              <option value="light">Light</option>
            </select>
          </div>
          <Button type="submit" disabled={busy}>
            {busy ? 'Saving…' : 'Save changes'}
          </Button>
        </form>
      </Card>

      <Card className="border-danger/40">
        <Eyebrow tone="danger">Danger zone</Eyebrow>
        <p className="mb-3 mt-1 text-sm text-ink-muted">
          Deleting your account removes your profile and player record. Sessions
          end immediately. This cannot be undone.
        </p>
        <Button variant="danger" onClick={deleteAccount}>Delete account</Button>
      </Card>
    </div>
  );
}
