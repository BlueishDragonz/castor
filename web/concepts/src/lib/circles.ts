/**
 * Private Circle — domain layer.
 *
 * Pure types and visibility logic for the Private Circle feature.
 * No network calls; this module is the source of truth for what
 * each visibility tier means on the client side. The server side
 * (beaverhabits/app/circles.py + circle_routes.py) is the security
 * gate; the rules here mirror it for display + gating client UI.
 */

export type CircleVisibility =
  | 'ticks'
  | 'ticks+streak'
  | 'ticks+streak+notes';

export const VISIBILITY_LEVELS: CircleVisibility[] = [
  'ticks',
  'ticks+streak',
  'ticks+streak+notes',
];

export const VISIBILITY_LABEL: Record<CircleVisibility, string> = {
  'ticks': 'Ticks only',
  'ticks+streak': 'Ticks + streak',
  'ticks+streak+notes': 'Ticks + streak + notes',
};

export const VISIBILITY_DESCRIPTION: Record<CircleVisibility, string> = {
  'ticks': 'Members see which days you ticked. Nothing else.',
  'ticks+streak': 'Members see ticks and your current streak count.',
  'ticks+streak+notes': 'Members see ticks, streak, and any notes you wrote on those days.',
};

/** Returns true if `visibility` includes the data described by `want`. */
export function visibilityAllows(
  visibility: CircleVisibility,
  want: 'ticks' | 'streak' | 'notes',
): boolean {
  switch (want) {
    case 'ticks':
      return true; // every tier includes ticks
    case 'streak':
      return visibility === 'ticks+streak' || visibility === 'ticks+streak+notes';
    case 'notes':
      return visibility === 'ticks+streak+notes';
  }
}

export interface CircleSummary {
  id: number;
  name: string;
  owner_id: string;
  is_owner: boolean;
  member_count: number;
  shared_habit_count: number;
  created_at: string; // ISO
}

export interface CircleMember {
  user_id: string;
  email: string;
  is_owner: boolean;
  joined_at: string; // ISO
}

export interface CircleSharedHabit {
  habit_id: string;
  visibility: CircleVisibility;
  share_notes: boolean;
  // Slice 24d: backend now includes this on GET /circles/{id} so the
  // owner-facing detail page can tell which shares belong to the
  // owner (manageable) vs to a member (read-only with attribution).
  owner_user_id?: string;
}

export interface CircleDetail {
  id: number;
  name: string;
  owner_id: string;
  owner_email: string;
  is_owner: boolean;
  members: CircleMember[];
  shared_habits: CircleSharedHabit[];
  created_at: string;
}

export interface CircleInvite {
  id: number;
  delivery: 'link' | 'email';
  invited_email: string | null;
  expires_at: string; // ISO
  used_at: string | null;
  raw_token: string | null; // only set on create
}

export interface CircleFeedHabit {
  habit_id: string;
  owner_email: string;
  name: string;
  visibility: CircleVisibility;
  share_notes: boolean;
  streak: number;
  records: Array<{ day: string; done: boolean; text?: string }>;
}
