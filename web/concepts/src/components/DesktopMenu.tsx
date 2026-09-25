'use client';

/**
 * DesktopMenu — desktop (≥640px) hamburger menu as a right-side drawsheet.
 *
 * Replaces the previous Radix DropdownMenu with a shadcn `Sheet` (slide-in
 * from right on desktop). Matches the MobileMore component's pattern so
 * the two surfaces look and behave the same; only the side differs.
 *
 * Items: Reorder habits, Import, Export, Statistics, Security, Settings,
 * Log out. Clicking an item closes the sheet then navigates. The logout
 * item POSTs to /logout and clears the cookie server-side before redirect.
 */

import * as React from 'react';
import {
  Sheet,
  SheetTrigger,
  SheetContent,
  SheetTitle,
  SheetClose,
} from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { Avatar } from '@/components/ui/avatar';
import {
  LayoutGrid,
  Upload,
  Download,
  BarChart2,
  Shield,
  Settings as SettingsIcon,
  LogOut,
  Menu as MenuIcon,
  X,
  ChevronRight,
  Users,
} from 'lucide-react';

interface MenuItem {
  label: string;
  Icon: React.ComponentType<{ className?: string }>;
  onClick: () => void;
  destructive?: boolean;
}

interface DesktopMenuProps {
  email?: string | null;
}

export function DesktopMenu({ email = null }: DesktopMenuProps) {
  // Open state lifted to drive the close-then-navigate pattern. Radix's
  // <Sheet onOpenChange> handles Esc + overlay click automatically.
  const [open, setOpen] = React.useState(false);

  const closeAndNavigate = (url: string) => {
    setOpen(false);
    // Defer the navigation a frame so the sheet's exit animation can
    // start before the page tears down. 50ms is below the visible
    // "pop" threshold for users.
    setTimeout(() => { window.location.href = url; }, 50);
  };

  const handleLogout = async () => {
    setOpen(false);
    // POST to /logout via a hidden form so the Set-Cookie clearing the
    // session is applied to the cookie jar in the same navigation that
    // follows the 302 to /login. Using fetch + window.location.href had
    // two race conditions:
    //   1. With redirect: 'follow' (default), fetch follows the 302 to
    //      GET /login, and the Set-Cookie from the 302 is consumed
    //      against the login GET but the page itself doesn't navigate.
    //   2. With redirect: 'manual', fetch returns opaqueredirect with
    //      status 0; the Set-Cookie is applied to the cookie jar but
    //      `window.location.href = '/login'` fired immediately afterwards
    //      was being preempted by React's re-render from setOpen(false).
    // Form POST is synchronous-ish and the browser navigates as part of
    // the response handling, so it cannot be raced.
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = '/logout';
    document.body.appendChild(form);
    form.submit();
  };

  const groups: { title: string; items: MenuItem[] }[] = [
    {
      title: 'Tools',
      items: [
        { label: 'Reorder habits', Icon: LayoutGrid, onClick: () => closeAndNavigate('/habits/order') },
        { label: 'Import', Icon: Upload, onClick: () => closeAndNavigate('/import') },
        { label: 'Export', Icon: Download, onClick: () => closeAndNavigate('/export') },
        { label: 'Statistics', Icon: BarChart2, onClick: () => closeAndNavigate('/stats') },
      ],
    },
    {
      // Mobile's MoreSheet has had a Private Circles entry under its
      // own "Sharing" section since before the migration; the desktop
      // drawer didn't. Putting it in its own group (not under Tools)
      // matches the mobile surface and signals that circles are a
      // first-class navigation destination, not just another tool.
      title: 'Sharing',
      items: [
        { label: 'Private Circles', Icon: Users, onClick: () => closeAndNavigate('/circles') },
      ],
    },
    {
      title: 'Account',
      items: [
        { label: 'Security', Icon: Shield, onClick: () => closeAndNavigate('/security') },
        { label: 'Settings', Icon: SettingsIcon, onClick: () => closeAndNavigate('/settings') },
      ],
    },
    {
      title: 'Session',
      items: [
        { label: 'Log out', Icon: LogOut, onClick: handleLogout, destructive: true },
      ],
    },
  ];

  return (
    <div className="hidden lg:block">
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="h-10 w-10"
            aria-label="Open menu"
            aria-haspopup="dialog"
          >
            <MenuIcon className="h-5 w-5" />
          </Button>
        </SheetTrigger>

        <SheetContent
          side="right"
          className="w-full sm:max-w-[400px] p-0"
          data-sheet="desktop-menu"
        >
          <div className="flex flex-col h-full">
            {/* Drag handle (top edge of side sheets) */}
            <div className="w-9 h-1 bg-muted rounded mx-auto my-2" />

            {/* sr-only title for screen readers — Radix requires the
                dialog to have an accessible name; the visible heading
                is the avatar + email block below. */}
            <SheetTitle className="sr-only">Menu</SheetTitle>

            {/* Account row — avatar + email, moved off the page header
                so each page's title isn't crowded by who-is-here copy. */}
            <div className="px-6 pt-2 pb-4 flex items-center gap-3">
              <Avatar email={email} size="md" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-foreground truncate">
                  {email ?? 'Signed in'}
                </p>
                <p className="text-xs text-muted-foreground">Account</p>
              </div>
            </div>

            <Separator />

            <div className="flex-1 overflow-y-auto px-4 pb-4">
              {groups.map((group, gi) => (
                <React.Fragment key={group.title}>
                  {gi > 0 && <Separator className="my-2" />}
                  <h3 className={
                    'text-[11px] font-semibold tracking-wider uppercase px-2 pt-3 pb-1 ' +
                    (group.items.some(i => i.destructive)
                      ? 'text-destructive'
                      : 'text-muted-foreground')
                  }>
                    {group.title}
                  </h3>
                  {group.items.map(({ label, Icon, onClick, destructive }) => (
                    <Button
                      key={label}
                      variant="ghost"
                      className={
                        'w-full justify-start text-left rounded-none py-3 px-4 hover:bg-accent ' +
                        (destructive ? 'text-destructive' : '')
                      }
                      onClick={onClick}
                    >
                      <div className="flex items-center gap-3 w-full">
                        <div className={
                          'w-7 h-7 grid place-items-center ' +
                          (destructive ? 'text-destructive' : 'text-muted-foreground')
                        }>
                          <Icon className="h-5 w-5" />
                        </div>
                        <span className="text-[15px]">{label}</span>
                        <span className="flex-1" />
                        {!destructive && <ChevronRight className="h-4 w-4 text-muted-foreground" />}
                      </div>
                    </Button>
                  ))}
                </React.Fragment>
              ))}
            </div>
          </div>

          <SheetClose className="absolute right-4 top-4 rounded-sm opacity-70 hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:pointer-events-none">
            <X className="h-4 w-4" />
            <span className="sr-only">Close</span>
          </SheetClose>
        </SheetContent>
      </Sheet>
    </div>
  );
}
