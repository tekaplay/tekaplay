'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useParams } from 'next/navigation';
import { useState } from 'react';
import { Badge, Button, Card, EmptyState, Eyebrow, Input, ProgressBar, Skeleton } from
  '@/components/ui';
import { ApiError, del, post } from '@/lib/api';
import { useAuthStore } from '@/lib/auth-store';
import {
  pickLicenseWithFreeSeat,
  useOrganization, useOrgInvitations, useOrgLicenseSummary, useOrgMembers,
} from '@/lib/organizations';
import { useToast } from '@/lib/toast';
import type { InvitationOut, LicenseAssignmentOut } from '@/lib/types';

function roleBadgeTone(role: string): 'default' | 'success' | 'accent' {
  if (role === 'owner') return 'accent';
  if (role === 'admin') return 'success';
  return 'default';
}

export default function OrganizationDetailPage() {
  const params = useParams<{ orgId: string }>();
  const orgId = params.orgId;
  const toast = useToast();
  const queryClient = useQueryClient();
  const currentUser = useAuthStore((s) => s.user);

  const org = useOrganization(orgId);
  const members = useOrgMembers(orgId);
  const invitations = useOrgInvitations(orgId);
  const licenses = useOrgLicenseSummary(orgId);

  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<'admin' | 'member'>('member');
  const [busy, setBusy] = useState(false);

  const me = (members.data ?? []).find((m) => m.user_id === currentUser?.id);
  const isAdmin = me?.role === 'owner' || me?.role === 'admin';

  function refresh() {
    queryClient.invalidateQueries({ queryKey: ['organizations', orgId] });
    queryClient.invalidateQueries({ queryKey: ['commerce', 'organizations', orgId] });
  }

  async function sendInvite(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await post<InvitationOut>(`/organizations/${orgId}/invitations`, {
        email: inviteEmail, role: inviteRole,
      });
      setInviteEmail('');
      refresh();
      toast('Invitation sent', 'success');
    } catch (e) {
      toast(e instanceof ApiError ? e.message : 'Could not send the invitation.', 'danger');
    } finally {
      setBusy(false);
    }
  }

  async function revokeInvitation(invitationId: string) {
    try {
      await del(`/organizations/${orgId}/invitations/${invitationId}`);
      refresh();
    } catch (e) {
      toast(e instanceof ApiError ? e.message : 'Could not revoke the invitation.', 'danger');
    }
  }

  async function removeMember(userId: string) {
    if (!window.confirm('Remove this member from the organization?')) return;
    try {
      await post(`/organizations/${orgId}/members/${userId}/remove`);
      refresh();
    } catch (e) {
      toast(e instanceof ApiError ? e.message : 'Could not remove the member.', 'danger');
    }
  }

  async function assignLicense(userId: string) {
    // Picks the first active license with a free seat. Orgs running
    // multiple concurrent licenses (e.g. an old comped one plus a new
    // Stripe seat subscription) would need an explicit picker; the common
    // case is a single active license per organization.
    const target = pickLicenseWithFreeSeat(
      licenses.data?.licenses ?? [], licenses.data?.assignments ?? [],
    );
    if (!target) {
      toast('No active license with a free seat.', 'danger');
      return;
    }
    try {
      await post<LicenseAssignmentOut>(
        `/commerce/organizations/${orgId}/licenses/${target.id}/assign`,
        { user_id: userId },
      );
      refresh();
      toast('License assigned', 'success');
    } catch (e) {
      toast(e instanceof ApiError ? e.message : 'Could not assign a license.', 'danger');
    }
  }

  if (org.isPending || members.isPending) {
    return <Skeleton className="h-64" />;
  }
  if (org.isError || !org.data) {
    return <EmptyState title="Organization not found" />;
  }

  const seatsPurchased = licenses.data?.seats_purchased ?? 0;
  const seatsAssigned = licenses.data?.seats_assigned ?? 0;
  const seatsAvailable = licenses.data?.seats_available ?? 0;
  const assignedUserIds = new Set(
    (licenses.data?.assignments ?? []).map((a) => a.user_id),
  );

  return (
    <div className="flex flex-col gap-8">
      <div>
        <Eyebrow>Organization // {org.data.org_type ?? 'general'}</Eyebrow>
        <h1 className="mt-1 font-display text-3xl font-semibold">{org.data.name}</h1>
      </div>

      <Card>
        <Eyebrow tone="muted">Licenses</Eyebrow>
        <div className="mt-2">
          <ProgressBar value={seatsAssigned} max={Math.max(seatsPurchased, 1)}
                       label="Seats used" />
        </div>
        <div className="mt-3 flex gap-6 font-mono text-xs text-ink-muted">
          <span>{seatsPurchased} purchased</span>
          <span>{seatsAssigned} assigned</span>
          <span>{seatsAvailable} available</span>
        </div>
      </Card>

      <section className="flex flex-col gap-3">
        <h2 className="font-display text-xl font-semibold">Members</h2>
        <div className="flex flex-col gap-2">
          {(members.data ?? []).map((member) => (
            <Card key={member.id} className="flex flex-wrap items-center gap-3">
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium">{member.display_name}</p>
                <p className="truncate font-mono text-xs text-ink-muted">{member.email}</p>
              </div>
              <Badge tone={roleBadgeTone(member.role)}>{member.role}</Badge>
              <Badge tone={member.has_license ? 'success' : 'default'}>
                {member.has_license ? 'Licensed' : 'Unlicensed'}
              </Badge>
              {isAdmin && member.role !== 'owner' && (
                <div className="flex gap-2">
                  {!member.has_license && seatsAvailable > 0 && (
                    <Button size="sm" variant="ghost"
                            onClick={() => assignLicense(member.user_id)}>
                      Assign license
                    </Button>
                  )}
                  <Button size="sm" variant="danger"
                          onClick={() => removeMember(member.user_id)}>
                    Remove
                  </Button>
                </div>
              )}
            </Card>
          ))}
        </div>
      </section>

      {isAdmin && (
        <section className="flex flex-col gap-3">
          <h2 className="font-display text-xl font-semibold">Invitations</h2>
          <Card>
            <form onSubmit={sendInvite} className="flex flex-wrap items-end gap-3">
              <div className="min-w-[200px] flex-1">
                <Input label="Email" type="email" value={inviteEmail} required
                       onChange={(e) => setInviteEmail(e.target.value)} />
              </div>
              <div className="flex flex-col gap-1.5">
                <label htmlFor="invite-role" className="text-sm font-medium">Role</label>
                <select
                  id="invite-role"
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value as 'admin' | 'member')}
                  className="rounded border border-line bg-surface px-3 py-2 text-sm"
                >
                  <option value="member">Member</option>
                  <option value="admin">Admin</option>
                </select>
              </div>
              <Button type="submit" disabled={busy || !inviteEmail.trim()}>
                {busy ? 'Sending…' : 'Send invite'}
              </Button>
            </form>
          </Card>
          {(invitations.data ?? []).filter((i) => i.status === 'pending').length > 0 && (
            <div className="flex flex-col gap-2">
              {(invitations.data ?? [])
                .filter((i) => i.status === 'pending')
                .map((invitation) => (
                  <Card key={invitation.id}
                        className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="font-medium">{invitation.email}</p>
                      <p className="font-mono text-xs text-ink-muted">
                        {invitation.role} // expires{' '}
                        {new Date(invitation.expires_at).toLocaleDateString()}
                      </p>
                    </div>
                    <Button size="sm" variant="ghost"
                            onClick={() => revokeInvitation(invitation.id)}>
                      Revoke
                    </Button>
                  </Card>
                ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
