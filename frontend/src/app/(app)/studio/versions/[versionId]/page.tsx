'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import { AiDrafter } from '@/components/studio/ai-drafter';
import { MissionOutlineDrafter } from '@/components/studio/mission-outline-drafter';
import { MissionOutline } from '@/components/studio/outline';
import { StatusBadge } from '@/components/studio/status-badge';
import { Button, Card, ErrorState, Eyebrow, Input, Skeleton } from '@/components/ui';
import { ApiError, api, post } from '@/lib/api';
import { usePermissions } from '@/lib/permissions';
import { useToast } from '@/lib/toast';
import type { ValidateOut, VersionDetail, VersionOut } from '@/lib/types';

function formatValidationError(error: Record<string, unknown>): string {
  const loc = Array.isArray(error.loc) ? error.loc.join(' → ') : '';
  const msg = typeof error.msg === 'string' ? error.msg : JSON.stringify(error);
  return loc ? `${loc}: ${msg}` : msg;
}

export default function VersionEditorPage() {
  const { versionId } = useParams<{ versionId: string }>();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { isPublisher } = usePermissions();

  const version = useQuery({
    queryKey: ['studio-version', versionId],
    queryFn: () => api<VersionDetail>(`/content/versions/${versionId}`),
  });

  const [raw, setRaw] = useState('');
  const [notes, setNotes] = useState('');
  const [reviewNote, setReviewNote] = useState('');
  const [validation, setValidation] = useState<ValidateOut | null>(null);
  const [loadedId, setLoadedId] = useState<string | null>(null);

  // Seed local editor state exactly once per loaded version.
  useEffect(() => {
    if (version.data && loadedId !== version.data.id) {
      setRaw(JSON.stringify(version.data.definition, null, 2));
      setNotes(version.data.notes);
      setLoadedId(version.data.id);
    }
  }, [version.data, loadedId]);

  function refresh() {
    queryClient.invalidateQueries({ queryKey: ['studio-version', versionId] });
    if (version.data) {
      queryClient.invalidateQueries({
        queryKey: ['studio-versions', version.data.project_id],
      });
    }
  }

  function actionError(error: unknown) {
    toast(error instanceof ApiError ? error.message : 'The action did not go through.', 'danger');
  }

  function parsedDefinition(): Record<string, unknown> | null {
    try {
      return JSON.parse(raw) as Record<string, unknown>;
    } catch {
      toast('Fix the JSON first — it does not parse.', 'danger');
      return null;
    }
  }

  const validate = useMutation({
    mutationFn: (definition: Record<string, unknown>) =>
      post<ValidateOut>('/content/validate', { definition }),
    onSuccess: (result) => {
      setValidation(result);
      toast(result.valid ? 'Definition is valid' : 'Validation found problems',
            result.valid ? 'success' : 'danger');
    },
    onError: actionError,
  });

  const save = useMutation({
    mutationFn: (definition: Record<string, unknown>) =>
      api<VersionOut>(`/content/versions/${versionId}`, {
        method: 'PUT',
        body: JSON.stringify({ definition, notes }),
      }),
    onSuccess: () => {
      refresh();
      toast('Draft saved', 'success');
    },
    onError: actionError,
  });

  const submit = useMutation({
    mutationFn: () => post<VersionOut>(`/content/versions/${versionId}/submit`),
    onSuccess: () => {
      refresh();
      toast('Submitted for review', 'success');
    },
    onError: actionError,
  });

  const review = useMutation({
    mutationFn: (decision: 'approve' | 'reject') =>
      post<VersionOut>(`/content/versions/${versionId}/${decision}`, {
        note: reviewNote,
      }),
    onSuccess: (result) => {
      refresh();
      toast(result.status === 'approved' ? 'Approved' : 'Rejected', 'success');
    },
    onError: actionError,
  });

  const publish = useMutation({
    mutationFn: () => post<VersionOut>(`/content/versions/${versionId}/publish`),
    onSuccess: () => {
      refresh();
      toast('Published — the mission is live', 'success');
    },
    onError: actionError,
  });

  if (version.isPending) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-96" />
      </div>
    );
  }
  if (version.isError) {
    return <ErrorState message="This version did not load." onRetry={() => version.refetch()} />;
  }

  const data = version.data;
  const editable = data.status === 'draft';
  const busy = save.isPending || submit.isPending || validate.isPending ||
    review.isPending || publish.isPending;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Eyebrow>
            <Link href={`/studio/projects/${data.project_id}`}
                  className="underline-offset-2 hover:underline">
              Studio // project
            </Link>{' '}
            // v{data.version_number}
          </Eyebrow>
          <h1 className="mt-1 flex items-center gap-3 font-display text-3xl font-semibold">
            Version {data.version_number}
            <StatusBadge status={data.status} />
          </h1>
        </div>
        <div className="flex flex-wrap gap-2">
          {editable && (
            <>
              <Button variant="ghost" disabled={busy}
                      onClick={() => {
                        const definition = parsedDefinition();
                        if (definition) validate.mutate(definition);
                      }}>
                {validate.isPending ? 'Validating…' : 'Validate'}
              </Button>
              <Button disabled={busy}
                      onClick={() => {
                        const definition = parsedDefinition();
                        if (definition) save.mutate(definition);
                      }}>
                {save.isPending ? 'Saving…' : 'Save draft'}
              </Button>
              <Button variant="ghost" disabled={busy} onClick={() => submit.mutate()}>
                {submit.isPending ? 'Submitting…' : 'Submit for review'}
              </Button>
            </>
          )}
          {isPublisher && data.status === 'approved' && (
            <Button disabled={busy} onClick={() => publish.mutate()}>
              {publish.isPending ? 'Publishing…' : 'Publish'}
            </Button>
          )}
        </div>
      </div>

      {!editable && data.status !== 'in_review' && (
        <p className="rounded border border-line bg-surface-raised px-4 py-2 text-sm text-ink-muted">
          This version is {data.status.replace('_', ' ')} and read-only. Create a
          new draft from the project page to make changes.
        </p>
      )}

      {isPublisher && data.status === 'in_review' && (
        <Card className="flex flex-col gap-3">
          <Eyebrow tone="muted">Review decision</Eyebrow>
          <Input label="Review note" name="review-note" value={reviewNote}
                 onChange={(e) => setReviewNote(e.target.value)}
                 placeholder="What should the author know?" />
          <div className="flex gap-2">
            <Button disabled={busy} onClick={() => review.mutate('approve')}>
              Approve
            </Button>
            <Button variant="danger" disabled={busy}
                    onClick={() => review.mutate('reject')}>
              Reject
            </Button>
          </div>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="flex flex-col gap-3">
          {editable && (
            <Input label="Draft notes" name="notes" value={notes}
                   onChange={(e) => setNotes(e.target.value)}
                   placeholder="What changed in this draft?" />
          )}
          <label htmlFor="definition-json" className="text-sm font-medium">
            Game definition (JSON)
          </label>
          <textarea
            id="definition-json"
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            readOnly={!editable}
            spellCheck={false}
            className="min-h-[520px] rounded border border-line bg-surface p-3 font-mono text-xs leading-relaxed"
          />
          {validation && !validation.valid && (
            <Card className="border-danger/50">
              <Eyebrow tone="danger">Validation problems</Eyebrow>
              <ul className="mt-2 flex flex-col gap-1">
                {validation.errors.map((error, index) => (
                  <li key={index} className="font-mono text-xs text-danger">
                    {formatValidationError(error)}
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>

        <aside className="flex flex-col gap-4">
          <MissionOutline raw={raw} />
          {editable && <MissionOutlineDrafter />}
          {editable && <AiDrafter />}
        </aside>
      </div>
    </div>
  );
}
