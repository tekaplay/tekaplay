'use client';

/** Challenge renderer registry — the frontend mirror of the backend's
 * challenge-type registry. Adding a game type = one component + one entry;
 * unknown types render a graceful fallback instead of breaking the mission. */
import { useState } from 'react';
import { Button, Card, cn } from '@/components/ui';

export interface ChallengeProps {
  config: Record<string, unknown>;
  attemptsRemaining: number;
  submitting: boolean;
  onSubmit(response: Record<string, unknown>): void;
}

// ── quiz ───────────────────────────────────────────────────────
interface QuizOption { id: string; text: string }

function QuizChallenge({ config, attemptsRemaining, submitting, onSubmit }: ChallengeProps) {
  const question = String(config.question ?? '');
  const options = (config.options as QuizOption[] | undefined) ?? [];
  const multi = Boolean(config.multi_select);
  const [selected, setSelected] = useState<string[]>([]);

  function toggle(id: string) {
    setSelected((current) =>
      multi
        ? current.includes(id)
          ? current.filter((x) => x !== id)
          : [...current, id]
        : [id],
    );
  }

  return (
    <fieldset className="flex flex-col gap-3">
      <legend className="font-medium leading-relaxed">{question}</legend>
      {multi && (
        <p className="font-mono text-xs text-ink-muted">select all that apply</p>
      )}
      <div className="flex flex-col gap-2" role={multi ? 'group' : 'radiogroup'}>
        {options.map((option) => {
          const active = selected.includes(option.id);
          return (
            <button
              key={option.id}
              type="button"
              role={multi ? 'checkbox' : 'radio'}
              aria-checked={active}
              onClick={() => toggle(option.id)}
              className={cn(
                'rounded border px-4 py-3 text-left text-sm transition-colors',
                active
                  ? 'border-accent bg-accent-soft text-ink'
                  : 'border-line hover:border-accent/50',
              )}
            >
              <span className="mr-3 font-mono text-xs text-ink-muted">
                {option.id.toUpperCase()}
              </span>
              {option.text}
            </button>
          );
        })}
      </div>
      <Button
        disabled={selected.length === 0 || submitting}
        onClick={() => onSubmit({ selected })}
      >
        {submitting ? 'Transmitting…' : `Submit answer (${attemptsRemaining} left)`}
      </Button>
    </fieldset>
  );
}

// ── ordering ───────────────────────────────────────────────────
interface OrderingItem { id: string; text: string }

function OrderingChallenge({ config, attemptsRemaining, submitting, onSubmit }: ChallengeProps) {
  const prompt = String(config.prompt ?? '');
  const initial = (config.items as OrderingItem[] | undefined) ?? [];
  const [items, setItems] = useState(initial);

  function move(index: number, direction: -1 | 1) {
    setItems((current) => {
      const next = [...current];
      const target = index + direction;
      if (target < 0 || target >= next.length) return current;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="font-medium leading-relaxed">{prompt}</p>
      <ol className="flex flex-col gap-2">
        {items.map((item, index) => (
          <li
            key={item.id}
            className="flex items-center gap-3 rounded border border-line px-4 py-2.5 text-sm"
          >
            <span className="font-mono text-xs text-accent">
              {String(index + 1).padStart(2, '0')}
            </span>
            <span className="flex-1">{item.text}</span>
            <button
              type="button"
              aria-label={`Move ${item.text} up`}
              disabled={index === 0}
              onClick={() => move(index, -1)}
              className="rounded px-2 py-1 font-mono text-xs text-ink-muted hover:text-accent disabled:opacity-30"
            >
              ▲
            </button>
            <button
              type="button"
              aria-label={`Move ${item.text} down`}
              disabled={index === items.length - 1}
              onClick={() => move(index, 1)}
              className="rounded px-2 py-1 font-mono text-xs text-ink-muted hover:text-accent disabled:opacity-30"
            >
              ▼
            </button>
          </li>
        ))}
      </ol>
      <Button
        disabled={submitting}
        onClick={() => onSubmit({ order: items.map((i) => i.id) })}
      >
        {submitting ? 'Transmitting…' : `Submit order (${attemptsRemaining} left)`}
      </Button>
    </div>
  );
}

// ── text input ─────────────────────────────────────────────────
function TextInputChallenge({ config, attemptsRemaining, submitting, onSubmit }: ChallengeProps) {
  const prompt = String(config.prompt ?? '');
  const [text, setText] = useState('');
  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({ text });
      }}
    >
      <label htmlFor="challenge-text" className="font-medium leading-relaxed">
        {prompt}
      </label>
      <input
        id="challenge-text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        autoComplete="off"
        className="rounded border border-line bg-surface px-3 py-2 font-mono text-sm"
      />
      <Button type="submit" disabled={text.trim().length === 0 || submitting}>
        {submitting ? 'Transmitting…' : `Submit (${attemptsRemaining} left)`}
      </Button>
    </form>
  );
}

// ── matching ───────────────────────────────────────────────────
interface MatchingCategory { id: string; label: string }
interface MatchingItem { id: string; text: string }

function MatchingChallenge({ config, attemptsRemaining, submitting, onSubmit }: ChallengeProps) {
  const prompt = String(config.prompt ?? '');
  const categories = (config.categories as MatchingCategory[] | undefined) ?? [];
  const items = (config.items as MatchingItem[] | undefined) ?? [];
  const [placements, setPlacements] = useState<Record<string, string>>({});

  const allPlaced = items.length > 0 && items.every((item) => placements[item.id]);

  return (
    <div className="flex flex-col gap-3">
      <p className="font-medium leading-relaxed">{prompt}</p>
      <div className="flex flex-col gap-2">
        {items.map((item) => (
          <div
            key={item.id}
            className="flex items-center gap-3 rounded border border-line px-4 py-2.5 text-sm"
          >
            <span className="flex-1">{item.text}</span>
            <select
              aria-label={`Category for ${item.text}`}
              value={placements[item.id] ?? ''}
              onChange={(e) =>
                setPlacements((current) => ({ ...current, [item.id]: e.target.value }))
              }
              className="rounded border border-line bg-surface px-2 py-1.5 font-mono text-xs"
            >
              <option value="" disabled>
                Choose…
              </option>
              {categories.map((category) => (
                <option key={category.id} value={category.id}>
                  {category.label}
                </option>
              ))}
            </select>
          </div>
        ))}
      </div>
      <Button
        disabled={!allPlaced || submitting}
        onClick={() => onSubmit({ placements })}
      >
        {submitting ? 'Transmitting…' : `Submit answer (${attemptsRemaining} left)`}
      </Button>
    </div>
  );
}

const RENDERERS: Record<string, React.ComponentType<ChallengeProps>> = {
  quiz: QuizChallenge,
  ordering: OrderingChallenge,
  text_input: TextInputChallenge,
  matching: MatchingChallenge,
};

export function ChallengeRenderer({
  type,
  ...props
}: ChallengeProps & { type: string }) {
  const Renderer = RENDERERS[type];
  if (!Renderer) {
    return (
      <Card className="border-dashed text-center text-sm text-ink-muted">
        This challenge type isn&apos;t supported by your client yet. Update the
        app to continue this mission.
        <p className="mt-1 font-mono text-xs">{type}</p>
      </Card>
    );
  }
  return <Renderer {...props} />;
}
