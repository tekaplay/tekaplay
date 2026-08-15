'use client';

/** Question drafting via the AI service (question_generation feature).
 * Output is a draft for the author to review and paste — never published
 * directly, matching the backend prompt's own contract. */
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { Button, Card, Eyebrow, Input } from '@/components/ui';
import { ApiError, api, post } from '@/lib/api';
import { useToast } from '@/lib/toast';
import type { AIRequestOut } from '@/lib/types';

export function AiDrafter() {
  const toast = useToast();
  const [topic, setTopic] = useState('');
  const [difficulty, setDifficulty] = useState<'easy' | 'medium' | 'hard'>('medium');
  const [count, setCount] = useState(3);
  const [result, setResult] = useState<AIRequestOut | null>(null);

  const generate = useMutation({
    mutationFn: () =>
      post<AIRequestOut>('/ai/requests', {
        feature: 'question_generation',
        input: { topic, difficulty, count },
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
      <Eyebrow tone="muted">AI question drafts</Eyebrow>
      <Input label="Topic" name="ai-topic" value={topic}
             onChange={(e) => setTopic(e.target.value)}
             placeholder="e.g. derivatives and rates of change" />
      <div className="flex gap-2">
        <div className="flex flex-1 flex-col gap-1.5">
          <label htmlFor="ai-difficulty" className="text-sm font-medium">Difficulty</label>
          <select
            id="ai-difficulty"
            value={difficulty}
            onChange={(e) => setDifficulty(e.target.value as typeof difficulty)}
            className="rounded border border-line bg-surface px-3 py-2 text-sm"
          >
            <option value="easy">Easy</option>
            <option value="medium">Medium</option>
            <option value="hard">Hard</option>
          </select>
        </div>
        <div className="w-24">
          <Input label="Count" name="ai-count" type="number" min={1} max={10}
                 value={count}
                 onChange={(e) => setCount(Number(e.target.value) || 1)} />
        </div>
      </div>
      <Button size="sm" disabled={topic.trim().length === 0 || generate.isPending}
              onClick={() => generate.mutate()}>
        {generate.isPending ? 'Drafting…' : 'Draft questions'}
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
            aria-label="Generated question drafts"
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
            Copy drafts
          </Button>
          <p className="font-mono text-[11px] text-ink-muted">
            Drafts for review — edit into your challenge configs before submitting.
          </p>
        </div>
      )}
    </Card>
  );
}
