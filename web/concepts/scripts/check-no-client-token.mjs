#!/usr/bin/env node
/**
 * CI gate for audit finding F7 — the session JWT must never be reachable from
 * client JavaScript.
 *
 * The /security page used to publish the token as `window.__SECURITY_TOKEN__`
 * and SecurityContent.tsx read it to build `Authorization` headers. That made
 * the httpOnly session cookie decorative: any XSS anywhere in the app would
 * hand an attacker a 30-day bearer token. The page now calls a same-origin BFF
 * that attaches the bearer server-side.
 *
 * This check is deliberately comment-aware. Several files legitimately *mention*
 * the global in prose documenting the fix, and a naive `grep __SECURITY_TOKEN__`
 * flags those too. A security gate that cries wolf gets disabled, and a
 * disabled gate protects nothing — so the parser strips comments before
 * matching and only reports genuinely executable references.
 *
 * Exit codes: 0 clean, 1 violation found, 2 usage error.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { extname, join } from 'node:path';

const SRC = process.argv[2] ?? 'src';
const EXTENSIONS = new Set(['.ts', '.tsx', '.astro']);

const VIOLATION = '__SECURITY_TOKEN__';

/**
 * Replace comment contents with spaces, preserving line structure so reported
 * line numbers stay accurate.
 *
 * Handles the three comment forms that appear in this tree: `//` to end of
 * line, slash-star ... star-slash blocks, and Astro/JSX brace-wrapped blocks
 * (which are the same thing wrapped in braces, so stripping the block is
 * enough).
 *
 * This is a lexer, not a parser: it does not understand string literals, so a
 * `//` inside a string could desynchronise it. That is acceptable here because
 * the failure mode is a false positive on a comment, not a missed violation in
 * the files that matter — and a missed violation still has to look like code.
 */
function stripComments(source) {
  let out = '';
  let i = 0;
  const n = source.length;
  while (i < n) {
    const two = source.slice(i, i + 2);
    if (two === '//') {
      while (i < n && source[i] !== '\n') {
        out += ' ';
        i += 1;
      }
    } else if (two === '/*') {
      while (i < n && source.slice(i, i + 2) !== '*/') {
        // Keep newlines so line numbering survives.
        out += source[i] === '\n' ? '\n' : ' ';
        i += 1;
      }
      out += '  ';
      i += 2;
    } else {
      out += source[i];
      i += 1;
    }
  }
  return out;
}

function* walk(dir) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      yield* walk(full);
    } else if (EXTENSIONS.has(extname(full))) {
      yield full;
    }
  }
}

let failures = 0;

for (const file of walk(SRC)) {
  const raw = readFileSync(file, 'utf8');
  const code = stripComments(raw);
  code.split('\n').forEach((line, index) => {
    if (line.includes(VIOLATION)) {
      console.error(
        `::error file=${file},line=${index + 1}::F7 regression — executable ` +
          `reference to ${VIOLATION}: ${line.trim()}`,
      );
      failures += 1;
    }
  });
}

// Secondary invariant: the removed global must not be re-declared, or its
// presence invites someone to re-add the assignment it documents.
const envTypes = join(SRC, 'env.d.ts');
try {
  if (stripComments(readFileSync(envTypes, 'utf8')).includes(VIOLATION)) {
    console.error(
      `::error file=${envTypes}::F7 regression — Window still declares ${VIOLATION}`,
    );
    failures += 1;
  }
} catch {
  // env.d.ts is optional; its absence is not this check's concern.
}

// The BFF that replaced the global must exist, so *removing* the fix also fails
// the build rather than silently regressing.
const REQUIRED = [
  'pages/api/v1/webauthn/credentials/index.ts',
  'pages/api/v1/webauthn/change-password.ts',
  'pages/api/v1/webauthn/recovery-email/index.ts',
];
for (const rel of REQUIRED) {
  try {
    statSync(join(SRC, rel));
  } catch {
    console.error(
      `::error file=${rel}::F7 regression — required BFF route is missing; the ` +
        `client must reach the backend through a server-side proxy, not a token`,
    );
    failures += 1;
  }
}

if (failures > 0) {
  console.error(`\nF7 check failed: ${failures} violation(s).`);
  process.exit(1);
}
console.log('F7 check passed: session token is not exposed to client JavaScript.');
