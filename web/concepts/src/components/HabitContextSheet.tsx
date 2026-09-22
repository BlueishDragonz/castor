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
import { Edit, Copy, ArrowUpDown, Archive } from 'lucide-react';

interface HabitContextSheetProps {
  habitId: string;
  habitName: string;
  children?: React.ReactNode;
}

export function HabitContextSheet({ habitId, habitName, children }: HabitContextSheetProps) {
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
