import { cn } from '@/lib/utils';

/**
 * shadcn/ui Skeleton — animated placeholder for loading states.
 *
 * Habit list uses this while the bearer-authed /api/v1/habits fetch is
 * in flight, so the page never shows a flash of empty state.
 */
function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('animate-pulse rounded-md bg-muted', className)} {...props} />;
}

export { Skeleton };
