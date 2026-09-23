'use client';

import * as React from 'react';
import {
  Sheet,
  SheetTrigger,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetClose,
} from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import {
  Shield,
  HelpCircle,
  Upload,
  Download,
  LogOut,
  X,
  ChevronRight,
} from 'lucide-react';

export default function MoreSheet() {
  const closeAndNavigate = (url: string) => {
    const sheet = document.querySelector('[data-sheet="more-sheet"]') as HTMLDialogElement;
    if (sheet && typeof sheet.close === 'function') {
      sheet.close();
    }
    window.location.href = url;
  };

  const handleLogout = () => {
    // POST to /logout via a hidden form. The server's 302 to /login
    // happens in the same navigation as the form submission, so the
    // Set-Cookie clearing the session cannot be raced against a
    // subsequent client-side window.location.href assignment.
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = '/logout';
    document.body.appendChild(form);
    form.submit();
  };

  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="More options">
          <span className="sr-only">More options</span>
          <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 5v.01M12 12v.01M12 19v.01M12 6a1 1 0 110-2 1 1 0 010 2zm0 7a1 1 0 110-2 1 1 0 010 2zm0 7a1 1 0 110-2 1 1 0 010 2z" />
          </svg>
        </Button>
      </SheetTrigger>

      <SheetContent side="bottom" className="w-full max-w-[480px] mx-auto rounded-t-2xl p-0" data-sheet="more-sheet">
        <div className="flex flex-col">
          {/* Drag handle */}
          <div className="w-9 h-1 bg-muted rounded mx-auto my-1.5" />

          {/* Title */}
          <SheetHeader className="px-4 pb-2">
            <SheetTitle className="text-[13px] font-semibold">More</SheetTitle>
          </SheetHeader>

          {/* Account Section */}
          <div className="px-4">
            <h3 className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase pt-4 pb-1">
              Account
            </h3>

            <Button
              variant="ghost"
              className="w-full justify-start text-left rounded-none py-3 px-4 hover:bg-accent"
              onClick={() => closeAndNavigate('/security')}
            >
              <div className="flex items-center gap-3 w-full">
                <div className="w-7 h-7 grid place-items-center text-muted-foreground">
                  <Shield className="h-5 w-5" />
                </div>
                <span className="text-[15px]">Security</span>
                <span className="flex-1" />
                <span className="text-[11px] text-muted-foreground">2 passkeys</span>
                <ChevronRight className="h-4 w-4 text-muted-foreground" />
              </div>
            </Button>

            <Button
              variant="ghost"
              className="w-full justify-start text-left rounded-none py-3 px-4 hover:bg-accent"
              onClick={() => closeAndNavigate('/help')}
            >
              <div className="flex items-center gap-3 w-full">
                <div className="w-7 h-7 grid place-items-center text-muted-foreground">
                  <HelpCircle className="h-5 w-5" />
                </div>
                <span className="text-[15px]">Help</span>
                <span className="flex-1" />
                <ChevronRight className="h-4 w-4 text-muted-foreground" />
              </div>
            </Button>
          </div>

          <Separator className="my-1.5" />

          {/* Data Section */}
          <div className="px-4">
            <h3 className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase pt-4 pb-1">
              Data
            </h3>

            <Button
              variant="ghost"
              className="w-full justify-start text-left rounded-none py-3 px-4 hover:bg-accent"
              onClick={() => closeAndNavigate('/import')}
            >
              <div className="flex items-center gap-3 w-full">
                <div className="w-7 h-7 grid place-items-center text-muted-foreground">
                  <Upload className="h-5 w-5" />
                </div>
                <span className="text-[15px]">Import</span>
                <span className="flex-1" />
                <ChevronRight className="h-4 w-4 text-muted-foreground" />
              </div>
            </Button>

            <Button
              variant="ghost"
              className="w-full justify-start text-left rounded-none py-3 px-4 hover:bg-accent"
              onClick={() => closeAndNavigate('/export')}
            >
              <div className="flex items-center gap-3 w-full">
                <div className="w-7 h-7 grid place-items-center text-muted-foreground">
                  <Download className="h-5 w-5" />
                </div>
                <span className="text-[15px]">Export</span>
                <span className="flex-1" />
                <ChevronRight className="h-4 w-4 text-muted-foreground" />
              </div>
            </Button>
          </div>

          <Separator className="my-1.5" />

          {/* Session Section */}
          <div className="px-4 pb-4">
            <h3 className="text-[11px] font-semibold tracking-wider text-destructive uppercase pt-4 pb-1">
              Session
            </h3>

            <Button
              variant="ghost"
              className="w-full justify-start text-left rounded-none py-3 px-4 hover:bg-accent text-destructive"
              onClick={handleLogout}
            >
              <div className="flex items-center gap-3 w-full">
                <div className="w-7 h-7 grid place-items-center text-destructive">
                  <LogOut className="h-5 w-5" />
                </div>
                <span className="text-[15px]">Log out</span>
              </div>
            </Button>
          </div>
        </div>

        <SheetClose className="absolute right-4 top-4 rounded-sm opacity-70 hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:pointer-events-none">
          <X className="h-4 w-4" />
          <span className="sr-only">Close</span>
        </SheetClose>
      </SheetContent>
    </Sheet>
  );
}