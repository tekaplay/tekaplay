'use client';

import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useState } from 'react';
import { Button, Card, Eyebrow, Input, Spinner } from '@/components/ui';
import { api, ApiError, post } from '@/lib/api';
import { useAuthHydrated, useAuthStore } from '@/lib/auth-store';
import type { InvitationPreviewOut } from '@/lib/types';

export default function InviteAcceptPage() {
  const params = useParams<{ token: string }>();
  const token = decodeURIComponent(params.token);
  const router = useRouter();
  const hydrated = useAuthHydrated();
  const { accessToken, register } = useAuthStore();

  const preview = useQuery({
    queryKey: ['invitation-preview', token],
    queryFn: () => api<InvitationPreviewOut>(`/invitations/${token}/preview`),
  });

  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function accept() {
    setBusy(true);
    setError('');
    try {
      await post('/invitations/accept', { token });
      router.replace('/organizations');
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not accept the invitation.');
    } finally {
      setBusy(false);
    }
  }

  async function registerAndJoin(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await register(email, password, displayName, token);
      router.replace('/organizations');
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not create the account.');
    } finally {
      setBusy(false);
    }
  }

  if (!hydrated || preview.isPending) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <Spinner label="Loading invitation" />
      </main>
    );
  }

  if (preview.isError || !preview.data?.valid) {
    return (
      <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-4 px-6">
        <Eyebrow tone="danger">Invitation</Eyebrow>
        <h1 className="font-display text-2xl font-semibold">Link no longer valid</h1>
        <p className="text-sm text-ink-muted">
          This invitation has expired, been revoked, or already been used.
        </p>
        <Link href="/login" className="text-accent underline-offset-2 hover:underline">
          Go to login
        </Link>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-4 px-6">
      <Eyebrow>Invitation</Eyebrow>
      <h1 className="font-display text-2xl font-semibold">
        Join {preview.data.organization_name}
      </h1>
      <p className="text-sm text-ink-muted">
        You&apos;ve been invited as {preview.data.role}.
      </p>

      {accessToken ? (
        <Card>
          {error && <p className="mb-3 text-sm text-danger">{error}</p>}
          <Button disabled={busy} onClick={accept}>
            {busy ? 'Joining…' : 'Accept invitation'}
          </Button>
        </Card>
      ) : (
        <Card>
          <p className="mb-3 text-sm text-ink-muted">
            Create an account to accept — it&apos;ll be joined to this organization
            automatically.
          </p>
          <form onSubmit={registerAndJoin} className="flex flex-col gap-4">
            <Input label="Display name" value={displayName} required maxLength={120}
                   onChange={(e) => setDisplayName(e.target.value)} />
            <Input label="Email" type="email" autoComplete="email" value={email} required
                   onChange={(e) => setEmail(e.target.value)} />
            <Input label="Password" type="password" autoComplete="new-password"
                   minLength={10} value={password} required
                   onChange={(e) => setPassword(e.target.value)} />
            {error && <p className="text-sm text-danger">{error}</p>}
            <Button type="submit" disabled={busy}>
              {busy ? 'Creating…' : 'Create account & join'}
            </Button>
          </form>
          <p className="mt-3 text-sm text-ink-muted">
            Already have an account?{' '}
            <Link href="/login" className="text-accent underline-offset-2 hover:underline">
              Log in
            </Link>{' '}
            first, then reopen this link.
          </p>
        </Card>
      )}
    </main>
  );
}
