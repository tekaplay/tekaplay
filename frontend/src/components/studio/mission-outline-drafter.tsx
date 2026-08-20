'use client';

/** Full mission drafting via the AI service (mission_outline feature).
 * Output is a first-draft game definition for the author to review, edit,
 * and validate — never published directly, matching the backend prompt's
 * own contract. */
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { Button, Card, Eyebrow, Input } from '@/components/ui';
import { ApiError, api, post } from '@/lib/api';
import { useToast } from '@/lib/toast';
import type { AIRequestOut } from '@/lib/types';

export function MissionOutlineDrafter() {
  const toast = useToast();
  const [topic, setTopic] = useState('');
  const [nelsonReference, setNelsonReference] = useState('');
  const [sceneCount, setSceneCount] = useState(4);
  const [challengeCount, setChallengeCount] = useState(3);
  const [result, setResult] = useState<AIRequestOut | null>(null);

  const generate = useMutation({
    mutationFn: () =>
      post<AIRequestOut>('/ai/requests', {
        feature: 'mission_outline',
        input: {
          topic,
          nelson_reference: nelsonReference,
          scene_count: sceneCount,
          challenge_count: challengeCount,
        },
      }),
    onSuccess: (request) => {
      setResult(request);
      if (request.status === 'failed') {
        toast('Generation failed — try again.', 'danger');
      } else if (request.status === 'queued') {
        toast('Drafting queued — use "Check result" in a moment.');
      }
    },
    onError: (error) =>
      toast(error instanceof ApiError ? error.message : 'Generation failed.', 'danger'),
  });

  const check = useMutation({
    mutationFn: (requestId: string) => api<AIRequestOut>(`/ai/requests/${requestId}`),
    onSuccess: (request) => {
      setResult(request);
      if (request.status === 'queued') toast('Still drafting — try again shortly.');
      if (request.status === 'failed') toast('Generation failed — try again.', 'danger');
    },
    onError: (error) =>
      toast(error instanceof ApiError ? error.message : 'Could not check the draft.', 'danger'),
  });

  return (
    <Card className="flex flex-col gap-3">
      <Eyebrow tone="muted">AI mission draft</Eyebrow>
      <Input label="Topic" name="mission-topic" value={topic}
             onChange={(e) => setTopic(e.target.value)}
             placeholder="e.g. solutions and solubility" />
      <Input label="Textbook reference (optional)" name="mission-reference"
             value={nelsonReference}
             onChange={(e) => setNelsonReference(e.target.value)}
             placeholder="e.g. Nelson Chemistry 11, Ch. 8" />
      <div className="flex gap-2">
        <div className="w-24">
          <Input label="Scenes" name="mission-scenes" type="number" min={2} max={8}
                 value={sceneCount}
                 onChange={(e) => setSceneCount(Number(e.target.value) || 2)} />
        </div>
        <div className="w-24">
          <Input label="Challenges" name="mission-challenges" type="number" min={1} max={8}
                 value={challengeCount}
                 onChange={(e) => setChallengeCount(Number(e.target.value) || 1)} />
        </div>
      </div>
      <Button size="sm" disabled={topic.trim().length === 0 || generate.isPending}
              onClick={() => generate.mutate()}>
        {generate.isPending ? 'Drafting…' : 'Draft mission'}
      </Button>
      {result && !result.response && result.status !== 'failed' && (
        <Button size="sm" variant="ghost" disabled={check.isPending}
                onClick={() => check.mutate(result.id)}>
          {check.isPending ? 'Checking…' : 'Check result'}
        </Button>
      )}
      {result?.response && (
        <div className="flex flex-col gap-2">
          <textarea
            readOnly
            value={result.response.content}
            aria-label="Generated mission draft"
            className="h-40 rounded border border-line bg-surface p-2 font-mono text-xs"
          />
          <Button
            size="sm"
            variant="ghost"
            onClick={async () => {
              await navigator.clipboard.writeText(result.response!.content);
              toast('Copied to clipboard', 'success');
            }}
          >
            Copy draft
          </Button>
          <p className="font-mono text-[11px] text-ink-muted">
            Draft for review — paste into the definition JSON and edit before submitting.
          </p>
        </div>
      )}
    </Card>
  );
}
