/**
 * HabitNoteDialog — long-press notes modal for a single habit tick.
 *
 * Ported from castor-main's habit_tick_dialog (architecture/components.py:149).
 * The castor-main pattern:
 *
 *   1. Open a Textarea pre-filled with the existing record's note text.
 *   2. On every keystroke, save if the text changed by >= 24 characters
 *      since the last persisted version (debounce to avoid hammering the
 *      backend on every single key).
 *   3. Always include `done` so this endpoint is the same shape as the
 *      no-notes click-toggle — idempotent per (habit, date).
 *
 * Trigger: long-press OR right-click on a tick cell. We mount one dialog
 * per row; the trigger is a `<button>` inside the tick cell with no
 * visible chrome, hit-tested via JS on mousedown/touchstart/contextmenu.
 *
 * The form-POST path (click to toggle, no JS) still works for users
 * without JS — this dialog is the enhancement, not the primary path.
 */
import { useEffect, useRef, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';

const AUTOSAVE_CHAR_DELTA = 24;

interface HabitNoteDialogProps {
  habitId: string;
  habitName: string;
  /** ISO YYYY-MM-DD, converted to DD-MM-YYYY on the wire. */
  date: string;
  /** Whether the date is currently ticked done. */
  done: boolean;
  /** Existing note text (may be empty). */
  initialText: string;
  /** Human label for the date — e.g. "Today", "Yesterday", "3 days ago". */
  dateLabel: string;
  /** Called when the dialog opens/closes so the parent can show/hide the trigger. */
  onOpenChange?: (open: boolean) => void;
  /** Controlled state from the parent trigger. */
  open: boolean;
}

export function HabitNoteDialog(props: HabitNoteDialogProps) {
  const { habitId, habitName, date, done: initialDone, initialText, dateLabel, open, onOpenChange } = props;
  const [text, setText] = useState(initialText);
  const [done, setDone] = useState(initialDone);
  const [saving, setSaving] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const lastSavedTextRef = useRef(initialText);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Reset when re-opened on a different date.
  useEffect(() => {
    if (open) {
      setText(initialText);
      setDone(initialDone);
      lastSavedTextRef.current = initialText;
      setSaving('idle');
    }
  }, [open, initialText, initialDone]);

  // Autosave debounced on the castor-main 24-char-delta rule.
  const queueSave = (nextText: string, nextDone: boolean) => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    setSaving('idle');
    saveTimerRef.current = setTimeout(async () => {
      const prevLen = lastSavedTextRef.current.length;
      const nextLen = nextText.length;
      // First save on open: persist immediately. Subsequent: only when
      // the text moved by >= AUTOSAVE_CHAR_DELTA chars (the castor-main
      // heuristic — prevents chatty round-trips on every keystroke).
      const isInitial = lastSavedTextRef.current === initialText && prevLen === initialText.length;
      if (!isInitial && Math.abs(nextLen - prevLen) < AUTOSAVE_CHAR_DELTA) {
        return;
      }
      await persist(nextText, nextDone);
    }, 350);
  };

  const persist = async (nextText: string, nextDone: boolean) => {
    setSaving('saving');
    try {
      const dateDMY = date.split('-').reverse().join('-'); // YYYY-MM-DD -> DD-MM-YYYY
      const res = await fetch(`/api/v1/habits/${habitId}/notes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: dateDMY, done: nextDone, text: nextText }),
      });
      if (!res.ok) {
        setSaving('error');
        return;
      }
      lastSavedTextRef.current = nextText;
      setSaving('saved');
      // Fade back to idle after a moment.
      setTimeout(() => setSaving((s) => (s === 'saved' ? 'idle' : s)), 1200);
    } catch {
      setSaving('error');
    }
  };

  // Flush on close — guarantees the latest text hits the backend even if
  // the user closed before the debounce fired.
  const handleOpenChange = (next: boolean) => {
    if (!next && saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
      const pendingText = text;
      const prevLen = lastSavedTextRef.current.length;
      const isInitial = lastSavedTextRef.current === initialText && prevLen === initialText.length;
      if (isInitial || Math.abs(pendingText.length - prevLen) >= AUTOSAVE_CHAR_DELTA) {
        void persist(pendingText, done);
      }
    }
    onOpenChange?.(next);
  };

  const handleTextChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const next = e.target.value;
    setText(next);
    queueSave(next, done);
  };

  const toggleDone = () => {
    const next = !done;
    setDone(next);
    void persist(text, next);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{habitName}</DialogTitle>
          <DialogDescription>
            Note for {dateLabel}. Saves as you type.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={done}
              onChange={toggleDone}
              className="h-4 w-4 rounded border-border accent-primary"
            />
            <span>Marked done for {dateLabel}</span>
          </label>

          <textarea
            value={text}
            onChange={handleTextChange}
            placeholder="Add a note (optional)..."
            maxLength={4000}
            rows={5}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 resize-none"
            aria-label={`Note for ${habitName} on ${dateLabel}`}
          />

          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>
              {saving === 'saving' && 'Saving…'}
              {saving === 'saved' && 'Saved.'}
              {saving === 'error' && 'Could not save. Try again.'}
              {saving === 'idle' && (done ? 'Done.' : 'Not done.')}
            </span>
            <span>{text.length} / 4000</span>
          </div>

          <div className="flex justify-end">
            <Button variant="ghost" size="sm" onClick={() => handleOpenChange(false)}>
              Close
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
