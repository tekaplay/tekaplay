/** Mirrors of the backend DTOs. Additive-only, like the API itself. */

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface UserOut {
  id: string;
  email: string;
  display_name: string;
  avatar_url: string | null;
  locale: string;
  timezone: string;
  theme: string;
  email_verified_at: string | null;
  created_at: string;
}

export interface DefinitionOut {
  id: string;
  slug: string;
  title: string;
  certification: string;
  schema_version: number;
  created_at: string;
}

export interface HUD {
  variables: Record<string, unknown>;
  flags: string[];
  inventory: Record<string, number>;
  xp_earned: number;
  achievements: string[];
}

export interface SessionView {
  session_id: string;
  definition_id: string;
  status: string;
  scene_id: string | null;
  scene_title: string | null;
  passives: PassiveElement[];
  interactive: InteractiveElement | null;
  can_advance: boolean;
  ending: { id: string; title?: string; description?: string } | null;
  hud: HUD;
}

export type PassiveElement =
  | { type: 'dialogue'; npc: { id: string; name: string; role: string } | null; text: string }
  | { type: 'media'; kind: string; asset_id: string; caption: string };

export interface ChoiceOptionView { id: string; text: string }

export type InteractiveElement =
  | { type: 'choice'; id: string; prompt: string; options: ChoiceOptionView[] }
  | {
      type: 'challenge';
      id: string;
      challenge_type: string;
      config: Record<string, unknown>;
      attempts_remaining: number;
    };

export interface AnswerOut {
  correct: boolean;
  score: number;
  feedback: string;
  view: SessionView;
}

export interface MissionNode {
  id: string; slug: string; title: string; sort_order: number;
  project_id: string | null; definition_id: string | null;
}
export interface CourseNode {
  id: string; slug: string; title: string; sort_order: number; missions: MissionNode[];
}
export interface CampaignNode {
  id: string; slug: string; title: string; sort_order: number;
  description: string; courses: CourseNode[];
}
export interface CertificationNode {
  id: string; slug: string; title: string; sort_order: number;
  description: string; category: string; campaigns: CampaignNode[];
}

export interface ProgressOut {
  id: string;
  slug: string;
  status: string;
  completions: number;
  best_ending: string | null;
  questions_answered: number;
  questions_correct: number;
  last_played_at: string | null;
}

export interface StreakOut {
  current_streak: number;
  longest_streak: number;
  last_activity_date: string | null;
}

export interface XpSummary {
  total_xp: number;
  level: number;
  level_floor: number;
  next_level_at: number;
}

export interface LeaderboardEntry {
  rank: number;
  user_id: string;
  display_name: string;
  total_xp: number;
  level: number;
}

export interface UnlockedAchievement {
  id: string; code: string; title: string; description: string;
  icon: string; xp_reward: number; hidden: boolean; unlocked_at: string;
}

export interface InventoryItemOut {
  id: string; source_slug: string; item_key: string; qty: number;
}

// ── Creator Studio ─────────────────────────────────────────────
export interface ProjectOut {
  id: string;
  slug: string;
  title: string;
  certification: string;
  owner_id: string | null;
  live_version_id: string | null;
  created_at: string;
}

export type VersionStatus =
  | 'draft' | 'in_review' | 'approved' | 'rejected' | 'published' | 'superseded';

export interface VersionOut {
  id: string;
  project_id: string;
  version_number: number;
  status: VersionStatus;
  notes: string;
  review_note: string;
  created_by: string | null;
  created_at: string;
  submitted_at: string | null;
  reviewed_at: string | null;
  published_at: string | null;
}

export interface VersionDetail extends VersionOut {
  definition: Record<string, unknown>;
}

export interface ValidateOut {
  valid: boolean;
  errors: Array<Record<string, unknown>>;
}

export interface AIResponseOut {
  content: string;
  provider: string;
  model: string;
  cached: boolean;
  tokens_input: number;
  tokens_output: number;
  latency_ms: number;
}

export interface AIRequestOut {
  id: string;
  feature: string;
  status: string;
  personalized: boolean;
  error: string;
  created_at: string;
  completed_at: string | null;
  response: AIResponseOut | null;
}

// ── Organizations, licensing, trials ────────────────────────────
export type OrgRole = 'owner' | 'admin' | 'member';

export interface OrganizationOut {
  id: string;
  name: string;
  slug: string;
  org_type: string | null;
  status: string;
  created_at: string;
}

export interface OrganizationMemberOut {
  id: string;
  user_id: string;
  display_name: string;
  email: string;
  role: OrgRole;
  has_license: boolean;
  joined_at: string;
}

export interface InvitationOut {
  id: string;
  organization_id: string;
  email: string;
  role: string;
  status: string;
  expires_at: string;
  created_at: string;
}

export interface InvitationPreviewOut {
  organization_name: string;
  role: string;
  expires_at: string;
  valid: boolean;
}

export interface LicenseAssignmentOut {
  id: string;
  license_id: string;
  user_id: string;
  status: string;
  assigned_at: string;
  revoked_at: string | null;
}

export interface ActiveLicenseOut {
  id: string;
  seats: number;
  status: string;
  expires_at: string | null;
}

export interface OrgLicenseSummaryOut {
  organization_id: string;
  seats_purchased: number;
  seats_assigned: number;
  seats_available: number;
  licenses: ActiveLicenseOut[];
  assignments: LicenseAssignmentOut[];
}

export interface TrialOut {
  started_at: string;
  expires_at: string;
  status: string;
}

export interface CommerceSubscriptionOut {
  id: string;
  status: string;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  trial_end: string | null;
  plan_id: string | null;
}

export interface EntitlementOut {
  premium: boolean;
  source: 'subscription' | 'license' | 'trial' | 'none';
  subscription: CommerceSubscriptionOut | null;
  trial: TrialOut | null;
}

export interface PlanOut {
  id: string;
  code: string;
  name: string;
  description: string;
  price_cents: number;
  currency: string;
  interval: string;
  trial_days: number;
}
