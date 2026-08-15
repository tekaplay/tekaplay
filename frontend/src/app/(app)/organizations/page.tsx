'use client';

import { useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useState } from 'react';
import { Badge, Button, Card, EmptyState, Eyebrow, Input, Skeleton } from '@/components/ui';
import { ApiError, post } from '@/lib/api';
import { useMyOrganizations } from '@/lib/organizations';
import { useToast } from '@/lib/toast';
import type { OrganizationOut } from '@/lib/types';

export default function OrganizationsPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const organizations = useMyOrganizations();
  const [name, setName] = useState('');
  const [orgType, setOrgType] = useState('');
  const [busy, setBusy] = useState(false);

  async function createOrganization(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await post<OrganizationOut>('/organizations', {
        name,
        org_type: orgType || undefined,
      });
      setName('');
      setOrgType('');
      await queryClient.invalidateQueries({ queryKey: ['organizations', 'me'] });
      toast('Organization created', 'success');
    } catch (e) {
      toast(e instanceof ApiError ? e.message : 'Could not create the organization.',
        'danger');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-8">
      <div>
        <Eyebrow>Organizations</Eyebrow>
        <h1 className="mt-1 font-display text-3xl font-semibold">Your organizations</h1>
      </div>

      {organizations.isPending ? (
        <Skeleton className="h-24" />
      ) : (organizations.data ?? []).length === 0 ? (
        <EmptyState
          title="No organizations yet"
          hint="Create one to provision licenses for employees, students, or a class."
        />
      ) : (
        <div className="flex flex-col gap-2">
          {(organizations.data ?? []).map((org) => (
            <Link key={org.id} href={`/organizations/${org.id}`}>
              <Card className="flex items-center justify-between gap-4 transition-colors hover:border-accent/60">
                <div>
                  <p className="font-medium">{org.name}</p>
                  <p className="font-mono text-xs text-ink-muted">
                    {org.org_type ?? 'organization'} // {org.slug}
                  </p>
                </div>
                <Badge tone={org.status === 'active' ? 'success' : 'default'}>
                  {org.status}
                </Badge>
              </Card>
            </Link>
          ))}
        </div>
      )}

      <Card>
        <Eyebrow tone="muted">New organization</Eyebrow>
        <form onSubmit={createOrganization} className="mt-3 flex flex-col gap-4">
          <Input label="Name" name="name" value={name} required maxLength={200}
                 onChange={(e) => setName(e.target.value)}
                 placeholder="e.g. Acme School" />
          <Input label="Type (optional)" name="org_type" value={orgType} maxLength={40}
                 onChange={(e) => setOrgType(e.target.value)}
                 placeholder="e.g. school, employer" />
          <Button type="submit" disabled={busy || !name.trim()}>
            {busy ? 'Creating…' : 'Create organization'}
          </Button>
        </form>
      </Card>
    </div>
  );
}
