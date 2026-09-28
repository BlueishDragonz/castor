/// <reference path="../.astro/types.d.ts" />

declare namespace App {
  interface Locals {
    session: {
      email: string;
      token: string;
    } | null;
  }
}

declare global {
  interface Window {
    closeAndNavigate: (url: string) => void;
    handleLogout: () => Promise<void>;
    __HABIT_TOKEN__: string;
    __HABIT_ID__: string;
    // F7: __SECURITY_TOKEN__ was removed. The /security page used to publish
    // the session JWT here for SecurityContent.tsx to read, which defeated the
    // httpOnly cookie. It now calls the same-origin /api/v1/webauthn/* BFF.
    // Do not re-add it; add a BFF route instead.
    __IMPORT_TOKEN__: string;
    __REORDER_TOKEN__: string;
  }
}
