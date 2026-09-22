'use client';

/**
 * HabitContextSheet — uncontrolled shadcn Sheet with a trigger slot.
 *
 * Actions navigate to server-side POST routes (Edit, Duplicate,
 * Reorder, Archive). No client-side fetch — the bearer stays in
 * the httpOnly cookie and reaches the backend via the standard
 * form POST.
 *
 * The trigger is rendered as the Sheet's trigger via asChild so the
 * caller's button is the actual click target. Callers pass the
 * trigger button via children.
 *
 * "Add note for today" form: posts to /habits/{id}/complete with
 * date=today, done=true, text=<textarea contents>. The Astro route
 * forwards to the backend's POST /api/v1/habits/{id}/completions
 * with the text included. The textarea auto-focuses when the sheet
 * opens (mounted to /habits today, not future or past).
 */

import {
  Sheet,
  SheetTrigger,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { Edit, Copy, ArrowUpDown, Archive, NotebookPen } from 'lucide-react';

interface HabitContextSheetProps {
  habitId: string;
  habitName: string;
  children?: React.ReactNode;
}

function todayDDMMYYYY(): string {
  const d = new Date();
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const yyyy = d.getFullYear();
  return `${dd}-${mm}-${yyyy}`;
}

export function HabitContextSheet({ habitId, habitName, children }: HabitContextSheetProps) {
  const today = todayDDMMYYYY();
  return (
    <Sheet>
      <SheetTrigger asChild>
        {children ?? (
          <Button variant="ghost" size="icon" aria-label={`Actions for ${habitName}`}>
            <span aria-hidden="true">⋮</span>
          </Button>
        )}
      </SheetTrigger>
      <SheetContent side="bottom" className="w-full max-w-md sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{habitName}</SheetTitle>
          <SheetDescription>Choose an action</SheetDescription>
        </SheetHeader>
        <div className="space-y-2 mt-4">
          <Button
            variant="outline"
            className="w-full justify-start gap-2"
            asChild
          >
            <a href={`/habits/${habitId}/edit`}>
              <Edit className="h-4 w-4" aria-hidden="true" />
              Edit
            </a>
          </Button>
          <Separator />
          <form method="post" action={`/habits/${habitId}/complete`} className="contents">
            <input type="hidden" name="date" value={today} />
            <input type="hidden" name="done" value="true" />
            <label className="block w-full">
              <span className="text-xs font-medium text-muted-foreground px-1">
                Add note for today
              </span>
              <textarea
                name="text"
                rows={3}
                maxLength={4000}
                placeholder="What happened today? (optional)"
                className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring resize-none"
                onClick={(e) => e.stopPropagation()}
              />
            </label>
            <Button
              type="submit"
              variant="outline"
              className="w-full justify-start gap-2"
            >
              <NotebookPen className="h-4 w-4" aria-hidden="true" />
              Save note
            </Button>
          </form>
          <Separator />
          <form method="post" action={`/habits/${habitId}/duplicate`} className="contents">
            <Button
              type="submit"
              variant="outline"
              className="w-full justify-start gap-2"
            >
              <Copy className="h-4 w-4" aria-hidden="true" />
              Duplicate
            </Button>
          </form>
          <Separator />
          <Button
            variant="outline"
            className="w-full justify-start gap-2"
            asChild
          >
            <a href="/habits/order">
              <ArrowUpDown className="h-4 w-4" aria-hidden="true" />
              Reorder
            </a>
          </Button>
          <Separator />
          <form method="post" action={`/habits/${habitId}/archive`} className="contents">
            <Button
              type="submit"
              variant="destructive"
              className="w-full justify-start gap-2"
            >
              <Archive className="h-4 w-4" aria-hidden="true" />
              Archive
            </Button>
          </form>
        </div>
      </SheetContent>
    </Sheet>
  );
}
