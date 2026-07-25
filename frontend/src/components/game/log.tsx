'use client';

/**
 * The signature element: scene content rendered as a comm log —
 * transmissions with NPC nameplates against a left signal rule.
 */

import { motion, useReducedMotion } from 'framer-motion';
import { Badge, Card, Eyebrow } from '@/components/ui';
import type { HUD, PassiveElement } from '@/lib/types';

export function CommLog({ entries }: { entries: PassiveElement[] }) {
  const reduceMotion = useReducedMotion();

  return (
    <div className="flex flex-col gap-3" aria-label="Scene transmissions">
      {entries.map((entry, index) => (
        <motion.div
          key={index}
          initial={reduceMotion ? false : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{
            delay: reduceMotion ? 0 : index * 0.08,
            duration: 0.25,
          }}
        >
          {entry.type === 'dialogue' ? (
            <div className="border-l-2 border-accent/70 pl-4">
              <p className="font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted">
                {entry.npc ? (
                  <>
                    <span className="text-accent">{entry.npc.name}</span>
                    {entry.npc.role && <> — {entry.npc.role}</>}
                  </>
                ) : (
                  'narrator'
                )}
              </p>

              <p className="mt-1 leading-relaxed">{entry.text}</p>
            </div>
          ) : (
            <div className="rounded border border-dashed border-line px-4 py-6 text-center">
              <p className="font-mono text-xs uppercase tracking-wider text-ink-muted">
                {entry.kind} attachment
              </p>

              {entry.caption && (
                <p className="mt-1 text-sm text-ink-muted">
                  {entry.caption}
                </p>
              )}
            </div>
          )}
        </motion.div>
      ))}
    </div>
  );
}

export function HudStrip({ hud }: { hud: HUD }) {
  const inventory = Object.entries(hud.inventory);

  return (
    <Card aria-label="Mission status" className="flex flex-col gap-3">
      <Eyebrow tone="muted">Mission telemetry</Eyebrow>

      <p className="font-display text-2xl font-semibold">
        {hud.xp_earned}{' '}
        <span className="text-sm text-ink-muted">XP this run</span>
      </p>

      {hud.achievements.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {hud.achievements.map((code) => (
            <Badge key={code} tone="accent">
              {code}
            </Badge>
          ))}
        </div>
      )}

      <div>
        <p className="mb-1 font-mono text-[11px] uppercase tracking-wider text-ink-muted">
          Inventory
        </p>

        {inventory.length === 0 ? (
          <p className="text-sm text-ink-muted">Empty — for now.</p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {inventory.map(([item, qty]) => (
              <li key={item}>
                <Badge>
                  {item}
                  {qty > 1 ? ` ×${qty}` : ''}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}