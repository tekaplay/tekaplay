'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import {
  Badge, Button, Card, EmptyState, ErrorState, Eyebrow, Input, Skeleton,
} from '@/components/ui';
import { ApiError, api, post } from '@/lib/api';
import { usePermissions } from '@/lib/permissions';
import { useToast } from '@/lib/toast';
import type { ProjectOut } from '@/lib/types';

export default function StudioPage() {
  const router = useRouter();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { loading, isAuthor } = usePermissions();
  const [slug, setSlug] = useState('');
  const [title, setTitle] = useState('');
  const [certification, setCertification] = useState('');

  const projects = useQuery({
    queryKey: ['studio-projects'],
    queryFn: () => api<ProjectOut[]>('/content/projects'),
    enabled: isAuthor,
  });

  const create = useMutation({
    mutationFn: () =>
      post<ProjectOut>('/content/projects', { slug, title, certification }),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ['studio-projects'] });
      toast('Project created', 'success');
      router.push(`/studio/projects/${project.id}`);
    },
    onError: (error) =>
      toast(error instanceof ApiError ? error.message : 'Could not create the project.', 'danger'),
  });

  if (loading) return <Skeleton className="h-40" />;
  if (!isAuthor) {
    return (
      <EmptyState
        title="Creator Studio is for authors"
        hint="Your account doesn't have the content.author permission. Ask an admin to add you to the creator role."
      />
    );
  }

  const slugValid = /^[a-z0-9]+(-[a-z0-9]+)*$/.test(slug);

  return (
    <div className="flex flex-col gap-8">
      <div>
        <Eyebrow>Studio // projects</Eyebrow>
        <h1 className="mt-1 font-display text-3xl font-semibold">Creator Studio</h1>
      </div>

      <Card className="max-w-lg">
        <Eyebrow tone="muted">New project</Eyebrow>
        <form
          className="mt-3 flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <Input label="Title" name="title" value={title}
                 onChange={(e) => setTitle(e.target.value)} required maxLength={300} />
          <Input label="Slug" name="slug" value={slug}
                 onChange={(e) => setSlug(e.target.value.toLowerCase())}
                 placeholder="calculus-mission-2" required
                 error={slug && !slugValid ? 'Lowercase letters, digits, and hyphens' : undefined} />
          <Input label="Certification (label)" name="certification" value={certification}
                 onChange={(e) => setCertification(e.target.value)}
                 placeholder="high-school-calculus" />
          <Button type="submit" disabled={!slugValid || !title || create.isPending}>
            {create.isPending ? 'Creating…' : 'Create project'}
          </Button>
        </form>
      </Card>

      <section className="flex flex-col gap-3" aria-label="Projects">
        <h2 className="font-display text-xl font-semibold">All projects</h2>
        {projects.isPending ? (
          <Skeleton className="h-32" />
        ) : projects.isError ? (
          <ErrorState message="Projects did not load." onRetry={() => projects.refetch()} />
        ) : (projects.data ?? []).length === 0 ? (
          <EmptyState title="No projects yet"
                      hint="Create your first project above — every mission starts as a draft." />
        ) : (
          <div className="flex flex-col gap-2">
            {(projects.data ?? []).map((project) => (
              <Link key={project.id} href={`/studio/projects/${project.id}`}>
                <Card className="flex items-center gap-4 transition-colors hover:border-accent/60">
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium">{project.title}</p>
                    <p className="font-mono text-xs text-ink-muted">
                      {project.slug}
                      {project.certification && <> // {project.certification}</>}
                    </p>
                  </div>
                  {project.live_version_id ? (
                    <Badge tone="success">Live</Badge>
                  ) : (
                    <Badge>Unpublished</Badge>
                  )}
                </Card>
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
