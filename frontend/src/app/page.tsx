'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { Button, Eyebrow } from '@/components/ui';
import { useAuthHydrated, useAuthStore } from '@/lib/auth-store';

export default function LandingPage() {
  const router = useRouter();
  const { accessToken } = useAuthStore();
  const hydrated = useAuthHydrated();

  useEffect(() => {
    if (hydrated && accessToken) router.replace('/dashboard');
  }, [hydrated, accessToken, router]);

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center gap-6 px-6">
      <Eyebrow>Tekaplay // incoming transmission</Eyebrow>
      <h1 className="font-display text-5xl font-semibold leading-[1.05]">
        Your next class is
        <br />a mission. Take it.
      </h1>
      <p className="max-w-lg text-ink-muted">
        Story-driven missions that teach high school subjects — Calculus,
        Chemistry, Biology, Physics, and more. Answer under pressure, earn
        your rank, keep the streak alive.
      </p>
      <div className="flex gap-3">
        <Button onClick={() => router.push('/register')}>Start your first mission</Button>
        <Link href="/login">
          <Button variant="ghost">Log in</Button>
        </Link>
      </div>
    </main>
  );
}
