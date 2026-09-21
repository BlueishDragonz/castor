/// <reference path="../.astro/types.d.ts" />

declare namespace App {
  interface Locals {
    session: {
      email: string;
      token: string;
    } | null;
  }
}
