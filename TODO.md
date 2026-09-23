# Castor Astro Migration - Implementation Tasks

## P0 - Mobile Navigation & Core Habit UX
- [ ] 1. Create BottomNav component + mobile detection in Layout
- [ ] 2. Create MoreSheet component using shadcn Sheet (side=bottom)
- [ ] 3. Implement multi-day habit grid with past-day checkboxes
- [ ] 4. Create HabitCheckBox component for grid cells
- [ ] 12. Verify visual quality: screenshots of mobile nav, habit grid, security page

## P1 - Passkeys & Settings Completeness
- [ ] 5. Create /security page with passkey list, add, remove, password change
- [ ] 6. Add WebAuthn client helpers (navigator.credentials.create/get)
- [ ] 7. Update auth.ts proxy to include /users/* and /webauthn/*
- [ ] 8. Add passkey login button to /login page
- [ ] 9. Enhance settings page: Import/Export/Help/PWA/Custom CSS

## P2 - Power User Features
- [ ] 10. Add calendar heatmap + history + best streaks to habit detail
- [ ] 11. Add tag filtering and long-press notes to habit grid
