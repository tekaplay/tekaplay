import { describe, expect, it, vi } from 'vitest';
import { daysRemaining, pickLicenseWithFreeSeat } from '@/lib/organizations';
import type { ActiveLicenseOut, LicenseAssignmentOut } from '@/lib/types';

function license(id: string, seats: number): ActiveLicenseOut {
  return { id, seats, status: 'active', expires_at: null };
}

function assignment(licenseId: string, userId: string): LicenseAssignmentOut {
  return {
    id: `a-${licenseId}-${userId}`,
    license_id: licenseId,
    user_id: userId,
    status: 'active',
    assigned_at: '2026-01-01T00:00:00Z',
    revoked_at: null,
  };
}

describe('pickLicenseWithFreeSeat', () => {
  it('returns null when there are no licenses', () => {
    expect(pickLicenseWithFreeSeat([], [])).toBeNull();
  });

  it('picks a license that still has room', () => {
    const picked = pickLicenseWithFreeSeat([license('L1', 2)], [assignment('L1', 'u1')]);
    expect(picked?.id).toBe('L1');
  });

  it('returns null when every seat is taken', () => {
    const picked = pickLicenseWithFreeSeat(
      [license('L1', 1)],
      [assignment('L1', 'u1')],
    );
    expect(picked).toBeNull();
  });

  it('skips a full license in favour of one with capacity', () => {
    const picked = pickLicenseWithFreeSeat(
      [license('full', 1), license('roomy', 5)],
      [assignment('full', 'u1')],
    );
    expect(picked?.id).toBe('roomy');
  });

  it('ignores assignments belonging to other licenses when counting', () => {
    const picked = pickLicenseWithFreeSeat(
      [license('L1', 1)],
      [assignment('L2', 'u1'), assignment('L2', 'u2')],
    );
    expect(picked?.id).toBe('L1');
  });
});

describe('daysRemaining', () => {
  it('floors at zero for past dates', () => {
    expect(daysRemaining('2000-01-01T00:00:00Z')).toBe(0);
  });

  it('counts whole days ahead', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    expect(daysRemaining('2026-01-15T00:00:00Z')).toBe(14);
    vi.useRealTimers();
  });
});
