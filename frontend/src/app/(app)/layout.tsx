'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { cn, Spinner } from '@/components/ui';
import { useAuthHydrated, useAuthStore } from '@/lib/auth-store';
import { useMyOrganizations } from '@/lib/organizations';
import { usePermissions } from '@/lib/permissions';
import { useTheme } from '@/lib/theme';

const BASE_NAV = [
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/library', label: 'Library' },
  { href: '/leaderboard', label: 'Leaderboard' },
  { href: '/profile', label: 'Profile' },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { accessToken, user, loadMe, logout } = useAuthStore();
  const hydrated = useAuthHydrated();
  const { theme, setTheme } = useTheme();
  const { isAuthor } = usePermissions();
  const organizations = useMyOrganizations();
  let nav = isAuthor
    ? [...BASE_NAV.slice(0, 3), { href: '/studio', label: 'Studio' }, BASE_NAV[3]]
    : BASE_NAV;
  if ((organizations.data ?? []).length > 0) {
    nav = [...nav.slice(0, -1), { href: '/organizations', label: 'Organization' }, nav.at(-1)!];
  }

  useEffect(() => {
    if (hydrated && !accessToken) router.replace('/login');
  }, [hydrated, accessToken, router]);

  useEffect(() => {
    if (hydrated && accessToken && !user) loadMe().catch(() => undefined);
  }, [hydrated, accessToken, user, loadMe]);

  if (!hydrated || !accessToken) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <Spinner label="Loading Tekaplay" />
      </main>
    );
  }

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 border-b border-line bg-surface/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-5xl items-center gap-6 px-4">
          <Link href="/dashboard" className="font-display text-lg font-semibold tracking-wide">
            Teka<span className="text-accent">play</span>
          </Link>
          <nav aria-label="Primary" className="flex gap-1">
            {nav.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                aria-current={pathname.startsWith(item.href) ? 'page' : undefined}
                className={cn(
                  'rounded px-3 py-1.5 text-sm transition-colors',
                  pathname.startsWith(item.href)
                    ? 'bg-accent-soft text-accent'
                    : 'text-ink-muted hover:text-ink',
                )}
              >
                {item.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <button
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
              className="rounded px-2 py-1 font-mono text-xs text-ink-muted hover:text-ink"
            >
              {theme === 'dark' ? 'LIGHT' : 'DARK'}
            </button>
            <span className="hidden text-sm text-ink-muted sm:inline">
              {user?.display_name}
            </span>
            <button
              onClick={async () => {
                await logout();
                router.replace('/login');
              }}
              className="rounded px-2 py-1 text-sm text-ink-muted hover:text-danger"
            >
              Log out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-8">{children}</main>
    </div>
  );
}
