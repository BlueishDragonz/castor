#!/usr/bin/env python3
"""Audit the parity matrix against actual file presence.

For every row whose status is ❌ / 🔵 / 🟡, walk the row's evidence
section and resolve every backtick-quoted path:

- `web/concepts/...`         → exists in the Astro tree
- `castor/...` / `tests/...` → exists in the Python tree
- absolute paths             → checked as-is
- `frontend/...` / `beaverhabits/...` etc → ignored (legacy refs)

A row that has any "MISSING" path keeps its current status.
A row that has ONLY "OK" paths AND zero intentionally-missing keys
is a stale-evidence candidate.

Note: this script is a fast file-existence check. It cannot detect
rows whose evidence files exist but are NOT WIRED TOGETHER (e.g.
row 2.13 had all referenced files present, but the event chain
between them had to be traced manually). For such cases, follow the
stale-evidence candidate with a manual event-chain inspection before
marking the row ✅.

Run from repo root:
    python Desktop/migration/scripts/audit_parity_matrix.py
"""
import os, re, sys

ROOT = '/home/joel/castor-repo'
MATRIX = f'{ROOT}/Desktop/migration/parity-matrix.md'

# Statuses we care about: untracked work or known partials.
OPEN_STATUSES = ('❌', '🔵', '🟡')

# Path prefixes the audit script attempts in order. The order matters —
# the first matching prefix wins.
CANDIDATE_PREFIXES = (
    '',                                       # absolute / relative-as-is
    f'{ROOT}/',                               # repo root
    f'{ROOT}/web/concepts/src/',              # Astro sources
    f'{ROOT}/web/concepts/src/pages/',        # Astro pages dir
    f'{ROOT}/web/concepts/src/components/',   # Astro components dir
    f'{ROOT}/web/concepts/src/lib/',          # Astro lib dir
)

# Backtick-quoted filename pattern. Allows .astro, .tsx, .ts, .py.
FILE_RE = re.compile(r'`([\w\[\]\./_:-]+\.(?:astro|tsx|ts|py))`')

# Custom-rows where the matrix explicitly says "intentional drop".
INTENTIONAL_DROPS = (
    '12.3',  # Paddle pricing page
    '12.4',  # Google One Tap
)


def resolve(path: str) -> str | None:
    """Try the candidate prefixes; first hit wins."""
    if path.startswith('http'):
        return None
    if 'beaverhabits' in path:  # legacy module-name reference
        return None
    if os.path.isabs(path):
        return path if os.path.exists(path) else None
    for prefix in CANDIDATE_PREFIXES:
        candidate = prefix + path
        if os.path.exists(candidate):
            return candidate
    return None


def parse_matrix(path: str) -> list[tuple[str, str, str, list[str]]]:
    """Return [(num, hdr, status, paths)] for every open-status row."""
    with open(path) as f:
        text = f.read()
    out = []
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        if lines[i].startswith('### ') and re.match(r'### \d+\.\d+', lines[i]):
            m = re.match(r'### (\d+\.\d+)\s+([^\n]+)', lines[i])
            if m:
                num, hdr = m.group(1), m.group(2)
                j = i + 1
                while j < len(lines) and not lines[j].startswith('### '):
                    j += 1
                block = '\n'.join(lines[i + 1:j])
                sm = re.search(
                    r'\|\s*\*\*Status\*\*\s*\|\s*([^\n|]+?)\s*\|',
                    block,
                )
                if sm:
                    status = sm.group(1).strip()
                    if any(s in status for s in OPEN_STATUSES):
                        out.append((num, hdr, status, FILE_RE.findall(block)))
                i = j
                continue
        i += 1
    return out


def main():
    rows = parse_matrix(MATRIX)
    sys.stderr.write(f'open-status rows: {len(rows)}\n')
    stale = []
    genuine = []
    for num, hdr, status, paths in rows:
        if num in INTENTIONAL_DROPS:
            continue
        if not paths:
            genuine.append((num, hdr, status, ['(no paths in evidence)']))
            continue
        results = []
        all_ok = True
        for p in paths:
            hit = resolve(p)
            results.append((p, hit is not None))
            if hit is None:
                all_ok = False
        if all_ok:
            stale.append((num, hdr, status, results))
        else:
            genuine.append((num, hdr, status, results))

    if stale:
        print('# Stale-evidence candidates (file present, status not ✅):')
        for num, hdr, status, results in stale:
            print(f'\n## {num} {hdr} (current status: {status})')
            for p, ok in results:
                print(f'  - OK: `{p}`')
        print()

    if genuine:
        print('# Genuine gaps (file MISSING or no paths):')
        for num, hdr, status, results in genuine:
            print(f'\n## {num} {hdr} (current status: {status})')
            for entry in results:
                if isinstance(entry, str):
                    print(f'  - {entry}')
                else:
                    p, ok = entry
                    mark = 'OK' if ok else 'MISSING'
                    print(f'  - {mark}: `{p}`')
        print()

    sys.exit(0)


if __name__ == '__main__':
    main()
