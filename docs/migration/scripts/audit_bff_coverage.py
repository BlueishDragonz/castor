#!/usr/bin/env python3
"""Audit BFF route coverage: does every backend /api/v1/* route have an
Astro BFF proxy that the **browser** can reach?

Two perspectives on the same backend endpoint:

1. **Server-side** — Astro pages calling `backendFetch()` make Node-side
   `fetch()` requests directly to `BACKEND_URL`. They bypass Astro's
   own /api/v1/* tree entirely. These never need a BFF route.

2. **Client-side** — Browser JS (React islands, vanilla <script>) calls
   `/api/v1/*` on the same origin. Astro's vite proxy only forwards
   `/auth`, `/users`, `/webauthn`, `/health` to the backend; everything
   else needs an explicit `pages/api/v1/*.ts` proxy.

The original slice-15 inspection report listed 5 missing BFF routes.
After writing user-configs + tokens, we discovered that
**admin/users, habits/meta, and circles/* are all server-side only**.
The BFF proxy is only needed when a browser-context call hits the path.

This script lists the backend's /api/v1/* routes and the Astro BFF
files, then flags:

  - **MISSING BFF** — backend route exists, no Astro BFF file, and we
    can show a server-side caller (i.e. the BFF really isn't needed)
    OR no caller at all (potentially dead code, but defensive).

  - **CLIENT CALLER WITHOUT BFF** — backend route exists, no Astro BFF
    file, and there's a client-side caller (browser JS). This is the
    case that breaks at runtime in production.

Run from repo root:
    python3 docs/migration/scripts/audit_bff_coverage.py
"""
import os, re, sys

ROOT = '/home/joel/castor-repo'

# Pull backend /api/v1/* routes from castor OpenAPI (if backend running)
# or fall back to source-grep.
def backend_paths():
    """Return list of /api/v1/* paths from the FastAPI app source."""
    paths = []
    for fn in ['castor/routes/api.py', 'castor/app/circle_routes.py',
               'castor/app/admin_routes.py']:
        full = os.path.join(ROOT, fn)
        if not os.path.exists(full):
            continue
        with open(full) as f:
            for m in re.finditer(
                r'@api_router\.(get|post|put|delete|patch)\(\s*"([^"]+)"',
                f.read(),
            ):
                verb, p = m.group(1).upper(), m.group(2)
                if not p.startswith('/'):
                    p = '/api/v1/' + p.lstrip('/')
                else:
                    p = '/api/v1' + p
                # Normalise {param} → [param] for matching against Astro
                norm = re.sub(r'\{(\w+)\}', r'[\1]', p)
                paths.append((verb, p, norm))
    return paths

# Pull Astro BFF files
def astro_bff_files():
    """Return map of Astro file path → set of methods it handles."""
    api_root = os.path.join(ROOT, 'web/concepts/src/pages/api')
    out = {}
    for dirpath, _, filenames in os.walk(api_root):
        for fn in filenames:
            if not fn.endswith('.ts'):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, api_root)
            methods = set()
            with open(full) as f:
                txt = f.read()
            for m in re.finditer(r'export const (GET|POST|PUT|DELETE|PATCH):', txt):
                methods.add(m.group(1))
            # Map file path → URL pattern
            url = '/api/' + rel.replace(os.sep, '/').removesuffix('.ts').removesuffix('/index')
            url = url.replace('[', '{').replace(']', '}')
            # Astro uses [id] → matches backend {id}
            out[url] = (rel, methods)
    return out

# Find client-side fetch() callers (browser context)
def client_callers():
    """Grep for fetch(...) calls to /api/v1/* in pages/components dirs."""
    callers = []  # [(url_pattern, file)]
    for root in ['web/concepts/src/components',
                 'web/concepts/src/pages',
                 'web/concepts/src/layouts']:
        full_root = os.path.join(ROOT, root)
        for dirpath, _, filenames in os.walk(full_root):
            for fn in filenames:
                if not fn.endswith(('.tsx', '.ts', '.astro')):
                    continue
                full = os.path.join(dirpath, fn)
                with open(full) as f:
                    txt = f.read()
                # Skip server-side markers: file is in pages/api/, has
                # `Astro.locals` context, has backendFetch() helper.
                # We just look for literal fetch(`/api/v1/...`)
                for m in re.finditer(
                    r'''fetch\(\s*[`'"]/api/v1/([^`'"]+)''', txt
                ):
                    url = '/api/v1/' + m.group(1)
                    # JS template-literal `${...}` is a runtime
                    # interpolation. Replace just the `${...}` with a
                    # {param} marker so subsequent path segments stay
                    # in the URL (the strip-everything-after pattern
                    # ate trailing path parts like /notes).
                    url = re.sub(r'\$\{([^}]+)\}', r'{\1}', url)
                    callers.append((url, os.path.relpath(full, ROOT)))
    return callers

def main():
    paths = backend_paths()
    bff = astro_bff_files()
    callers = client_callers()

    # Invert BFF dict
    bff_norm = {}
    for url, (rel, methods) in bff.items():
        norm = re.sub(r'\{(\w+)\}', r'[\1]', url)
        bff_norm[norm] = (url, rel, methods)

    # Categorise each backend route
    missing_bff = []
    has_bff = []
    for verb, p, norm in paths:
        if norm in bff_norm:
            has_bff.append((verb, p, norm, bff_norm[norm]))
        else:
            missing_bff.append((verb, p, norm))

    # Client callers without BFF (THE BUG)
    print('# Client-side callers of /api/v1/* without an Astro BFF proxy:')
    bugs = 0
    # Build a list of (segment-counts, paths) so caller URLs can match by
    # segment count, not by param name. /api/v1/habits/[id]/notes has
    # the same shape as /api/v1/habits/[habitId]/notes.
    def shape(url: str) -> tuple:
        """URL → tuple of ('literal'|'param') per segment."""
        parts = url.split('?')[0].split('/')
        return tuple('param' if (p.startswith('[') or (p.startswith('{') and p.endswith('}'))) else 'literal' for p in parts)
    bff_shapes = {}
    for url, (rel, methods) in bff.items():
        s = shape(url)
        bff_shapes.setdefault(s, []).append(url)

    for url, src in callers:
        s = shape(url)
        if s not in bff_shapes:
            print(f'  ⚠ {src} → {url}  (no BFF proxy)')
            bugs += 1
    if not bugs:
        print('  (none)')

    print()
    print('# Backend routes WITHOUT an Astro BFF proxy (likely server-side only):')
    if not missing_bff:
        print('  (none)')
    for verb, p, norm in missing_bff:
        print(f'  {verb:6s} {p}')

    print()
    print('# Backend routes WITH an Astro BFF proxy:')
    if not has_bff:
        print('  (none)')
    for verb, p, norm, (url, rel, methods) in has_bff:
        ms = ','.join(sorted(methods)) or '?'
        print(f'  {verb:6s} {p:50s} ← {rel:50s} [{ms}]')

    sys.exit(1 if bugs else 0)


if __name__ == '__main__':
    main()
