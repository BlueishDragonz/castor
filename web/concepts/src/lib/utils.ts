/**
 * shadcn/ui utility helper — `cn(...)` is the canonical class-name
 * combinator across the shadcn ecosystem. Required by every primitive
 * that gets installed via `pnpm dlx shadcn@latest add ...`.
 */
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
