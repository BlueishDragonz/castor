/**
 * Avatar — initials-based avatar. No image uploads, no external service.
 *
 * First letter is the first alphanumeric of the email's local part
 * (so `jane.doe@example.com` → "J", `+44...@x.com` → "4"). If the
 * email is missing or has no alphanumeric chars, falls back to "?" so
 * the layout doesn't collapse.
 *
 * Deterministic background colour from the email: hash the local part
 * to one of 6 castor-teal-ish hues so two friends with the same
 * initial don't look identical but the castor palette stays cohesive.
 */
import * as React from 'react';
import { cn } from '@/lib/utils';

const PALETTE = [
  'bg-primary text-primary-foreground',                          // castor-teal
  'bg-emerald-600 text-white',
  'bg-sky-600 text-white',
  'bg-violet-600 text-white',
  'bg-amber-600 text-white',
  'bg-rose-600 text-white',
];

function firstInitial(email: string | null | undefined): string {
  if (!email) return '?';
  const local = email.split('@')[0] ?? '';
  const match = local.match(/[A-Za-z0-9]/);
  return match ? match[0].toUpperCase() : '?';
}

function paletteIndex(email: string | null | undefined): number {
  if (!email) return 0;
  let h = 0;
  for (let i = 0; i < email.length; i++) {
    h = (h * 31 + email.charCodeAt(i)) >>> 0;
  }
  return h % PALETTE.length;
}

export interface AvatarProps {
  email?: string | null;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
  /** aria-label override; defaults to "Account for {email}". */
  label?: string;
}

const SIZE: Record<NonNullable<AvatarProps['size']>, string> = {
  sm: 'h-7 w-7 text-xs',
  md: 'h-9 w-9 text-sm',
  lg: 'h-12 w-12 text-base',
};

export function Avatar({ email, size = 'md', className, label }: AvatarProps) {
  const initial = firstInitial(email);
  const colour = PALETTE[paletteIndex(email)];
  const aria = label ?? (email ? `Account for ${email}` : 'Account');
  return (
    <span
      role="img"
      aria-label={aria}
      className={cn(
        'inline-flex items-center justify-center rounded-full font-semibold select-none shrink-0',
        SIZE[size],
        colour,
        className,
      )}
    >
      {initial}
    </span>
  );
}
