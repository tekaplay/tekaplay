'use client';

import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useAuthStore } from '@/lib/auth-store';
import type {
  ActiveLicenseOut,
  EntitlementOut,
  InvitationOut,
  LicenseAssignmentOut,
  OrganizationMemberOut,
  OrganizationOut,
  OrgLicenseSummaryOut,
} from '@/lib/types';

export function useMyOrganizations() {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery({
    queryKey: ['organizations', 'me'],
    queryFn: () => api<OrganizationOut[]>('/organizations/me'),
    enabled: Boolean(accessToken),
    staleTime: 30_000,
  });
}

export function useOrganization(organizationId: string | undefined) {
  return useQuery({
    queryKey: ['organizations', organizationId],
    queryFn: () => api<OrganizationOut>(`/organizations/${organizationId}`),
    enabled: Boolean(organizationId),
  });
}

export function useOrgMembers(organizationId: string | undefined) {
  return useQuery({
    queryKey: ['organizations', organizationId, 'members'],
    queryFn: () => api<OrganizationMemberOut[]>(`/organizations/${organizationId}/members`),
    enabled: Boolean(organizationId),
  });
}

export function useOrgInvitations(organizationId: string | undefined) {
  return useQuery({
    queryKey: ['organizations', organizationId, 'invitations'],
    queryFn: () => api<InvitationOut[]>(`/organizations/${organizationId}/invitations`),
    enabled: Boolean(organizationId),
  });
}

export function useOrgLicenseSummary(organizationId: string | undefined) {
  return useQuery({
    queryKey: ['commerce', 'organizations', organizationId, 'licenses'],
    queryFn: () =>
      api<OrgLicenseSummaryOut>(`/commerce/organizations/${organizationId}/licenses`),
    enabled: Boolean(organizationId),
  });
}

export function useEntitlement() {
  const accessToken = useAuthStore((s) => s.accessToken);
  return useQuery({
    queryKey: ['commerce', 'entitlement'],
    queryFn: () => api<EntitlementOut>('/commerce/subscription'),
    enabled: Boolean(accessToken),
    staleTime: 30_000,
  });
}

/** Days remaining until an ISO timestamp, floored at 0 — used for trial
 * countdowns and license/subscription expiry displays. */
export function daysRemaining(isoDate: string): number {
  const ms = new Date(isoDate).getTime() - Date.now();
  return Math.max(0, Math.ceil(ms / 86_400_000));
}

/** Picks the first active license with a free seat, given the org's active
 * licenses and current active assignments. Returns null if every license is
 * full (or there are none) — the caller should surface that as "no seats
 * available" rather than guessing. */
export function pickLicenseWithFreeSeat(
  licenses: ActiveLicenseOut[],
  assignments: LicenseAssignmentOut[],
): ActiveLicenseOut | null {
  const assignedCounts = new Map<string, number>();
  for (const a of assignments) {
    assignedCounts.set(a.license_id, (assignedCounts.get(a.license_id) ?? 0) + 1);
  }
  return licenses.find((lic) => (assignedCounts.get(lic.id) ?? 0) < lic.seats) ?? null;
}
