'use client';

/**
 * HelpDialog — shadcn Dialog triggered from /settings Help card.
 *
 * Mirrors the four links from beaverhabits/frontend/layout.py::show_help_dialog
 * (Wiki, Supporter, YouTube, Issues). Opens in a new tab so the user
 * doesn't lose their place in /settings.
 *
 * Accessibility: aria-label on the trigger, focus is returned to the
 * trigger on close (Radix Dialog handles this by default).
 */

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { HelpCircle, ExternalLink } from 'lucide-react';

const LINKS = [
  { label: 'Documentation', href: 'https://github.com/daya0576/beaverhabits/wiki' },
  { label: 'Supporter', href: 'https://www.beaverhabits.com/pricing' },
  { label: 'YouTube', href: 'https://www.youtube.com/@beaverhabits' },
  { label: 'Bugs & Feature Requests', href: 'https://github.com/daya0576/beaverhabits/issues' },
];

export function HelpDialog() {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline" className="gap-2">
          <HelpCircle className="h-4 w-4" aria-hidden="true" />
          Open help
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Help</DialogTitle>
          <DialogDescription>
            Resources for using Castor and getting support.
          </DialogDescription>
        </DialogHeader>
        <ul className="space-y-2">
          {LINKS.map((link) => (
            <li key={link.href}>
              <a
                href={link.href}
                target="_blank"
                rel="noreferrer noopener"
                className="flex items-center justify-between gap-2 rounded-md px-3 py-2 text-sm hover:bg-accent hover:text-accent-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <span>{link.label}</span>
                <ExternalLink className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              </a>
            </li>
          ))}
        </ul>
        <Separator />
        <p className="text-xs text-muted-foreground">
          Links open in a new tab so you don't lose your place.
        </p>
      </DialogContent>
    </Dialog>
  );
}

export default HelpDialog;
