/**
 * HabitNoteRow — wires one habit row's ticks to a single notes dialog.
 *
 * Architecture:
 *   - HabitGrid renders one <HabitNoteRow> per habit.
 *   - HabitCheckBox buttons emit a `castor:tick-longpress` CustomEvent
 *     (fired by the script at the bottom of HabitGrid.astro when the
 *     user holds the tick for 200ms or right-clicks).
 *   - HabitNoteRow listens for events on its own root, filters by
 *     `data-habit-id`, and opens the dialog with that date's text.
 *
 * Why one dialog per row (not per tick): the dialog is expensive to
 * mount (Radix portal + React state). One per row covers all 7 days
 * without exploding the DOM or the React tree.
 */
import { useEffect, useState } from 'react';
import { HabitNoteDialog } from './HabitNoteDialog';

interface HabitNoteRowProps {
  habitId: string;
  habitName: string;
  initialNotesByDate: Record<string, string>;
  initialTickedDays: string[];
}

interface DialogState {
  open: boolean;
  /** ISO YYYY-MM-DD */
  date: string;
  done: boolean;
  text: string;
  /** "Today" / "Yesterday" / "N days ago" */
  label: string;
}

function dateLabel(iso: string): string {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const [y, m, d] = iso.split('-').map(Number);
  const target = new Date(y, m - 1, d);
  const days = Math.round((today.getTime() - target.getTime()) / (1000 * 60 * 60 * 24));
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  return `${days} days ago`;
}

export function HabitNoteRow({ habitId, habitName, initialNotesByDate, initialTickedDays }: HabitNoteRowProps) {
  const [notes, setNotes] = useState(initialNotesByDate);
  const [tickedDays, setTickedDays] = useState<Set<string>>(new Set(initialTickedDays));
  const [dialog, setDialog] = useState<DialogState>({ open: false, date: '', done: false, text: '', label: '' });

  useEffect(() => {
    const handler = (e: Event) => {
      const ev = e as CustomEvent<{ habitId: string; date: string; done: boolean }>;
      if (!ev.detail || ev.detail.habitId !== habitId) return;
      const date = ev.detail.date;
      setDialog({
        open: true,
        date,
        done: ev.detail.done,
        text: notes[date] || '',
        label: dateLabel(date),
      });
      // Reflect immediate toggle on the local set so the row's tick
      // visually flips on close without a refetch.
      setTickedDays((prev) => {
        const next = new Set(prev);
        if (ev.detail.done) next.add(date); else next.delete(date);
        return next;
      });
    };
    document.addEventListener('castor:tick-longpress', handler as EventListener);
    return () => document.removeEventListener('castor:tick-longpress', handler as EventListener);
  }, [habitId, notes]);

  // When the dialog saves a new note, refresh local state so the next
  // open on the same date sees the latest text (without a page reload).
  const handleOpenChange = (open: boolean) => {
    setDialog((d) => ({ ...d, open }));
    if (!open) {
      // Pull the latest text back into the row state.
      setNotes((prev) => {
        const next = { ...prev };
        if (dialog.text.trim()) next[dialog.date] = dialog.text;
        else delete next[dialog.date];
        return next;
      });
    }
  };

  return (
    <HabitNoteDialog
      habitId={habitId}
      habitName={habitName}
      date={dialog.date}
      done={dialog.done}
      initialText={dialog.text}
      dateLabel={dialog.label}
      open={dialog.open}
      onOpenChange={handleOpenChange}
    />
  );
}
