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
    __SECURITY_TOKEN__: string;
    __IMPORT_TOKEN__: string;
    __REORDER_TOKEN__: string;
  }
}
