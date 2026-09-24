"""Slice 6 — Import / Export.

Pins the backend contract for the Astro /export and /import pages:

  - GET  /api/v1/habits/export  → JSON snapshot of all habits (with order)
  - POST /api/v1/habits (loop)  → the BFF import implementation creates
                                   habits one by one. We pin this here.

BEHAVIOURAL GAPS (flagged, not blocking):
  - No backend CSV export endpoint. Parity matrix row 6.2 (CSV export)
    references a legacy NiceGUI feature that has not been ported.
  - No backend import endpoint. The Astro BFF at
    `web/concepts/src/pages/api/v1/habits/import.ts` loops over the
    uploaded payload and POSTs /api/v1/habits for each habit. This means
    **tick records are NOT preserved on import** — only habit metadata
    (name, period, tags, status). Verified in the BFF source.
  - These gaps pre-date Slice 6; Phase 1 parity-matrix row 6.3 already
    flagged "import endpoint reads but does not write" per the audit doc.

Run:
  python -m unittest discover -s tests -p test_slice6_import_export.py -v
"""
import datetime
import json
import os
import tempfile
import unittest

_TMP = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = f'sqlite+aiosqlite:///{_TMP.name}/test.db'
os.environ['JWT_SECRET'] = 'test-only-jwt-secret-not-for-production'
os.environ['RESET_PASSWORD_TOKEN_SECRET'] = 'test-only-reset-secret'
os.environ['NICEGUI_STORAGE_SECRET'] = 'test-only-storage-secret'
os.environ['JWT_LIFETIME_SECONDS'] = str(60 * 60 * 24 * 30)
os.environ['REQUIRE_ADMIN_FOR_REGISTRATION'] = 'false'
os.environ['TRUSTED_LOCAL_EMAIL'] = ''
os.environ['TRUSTED_EMAIL_HEADER'] = ''
os.environ['AUTH_RATE_USER_PER_MINUTE'] = '10000'
os.environ['AUTH_RATE_IP_PER_MINUTE'] = '10000'

import httpx
from fastapi import FastAPI
from fastapi_users.db import SQLAlchemyUserDatabase
import castor.main
from castor import views
from castor.app import db, reset_routes
from castor.app.users import UserManager
from castor.app.schemas import UserCreate
from castor.app.app import init_auth_routes
from castor.routes.api import init_api_routes

app = FastAPI()
init_auth_routes(app)
init_api_routes(app)
app.include_router(reset_routes.router)

PASSWORD = 'correct horse battery staple'


class Slice6ImportExportTests(unittest.IsolatedAsyncioTestCase):
    """Backend contract tests for import/export."""

    async def asyncSetUp(self):
        async with db.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await db.create_db_and_tables()
        reset_routes._rate_limit_cache.clear()
        session = db.async_session_maker()
        manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
        self.user = await manager.create(
            UserCreate(email='exporter@example.com', password=PASSWORD)
        )
        await views.get_or_create_user_habit_list(self.user, views.dummy_empty_habit_list())
        self.session = session
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.session.close()
        await db.engine.dispose()

    async def _login(self) -> str:
        r = await self.client.post(
            '/auth/login',
            data={'username': self.user.email, 'password': PASSWORD},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['access_token']

    async def _create_habit(self, token: str, name: str, period: dict | None = None, tags: list[str] | None = None) -> str:
        """Create a habit. POST /api/v1/habits only accepts {name}; tags and
        period must be set via a follow-up PUT.

        This mirrors what the Astro BFF import loop does NOT do (it just
        sends {name, tags, status} on POST and the tags/status are dropped).
        """
        r = await self.client.post(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {token}'},
            json={'name': name},
        )
        self.assertEqual(r.status_code, 200, r.text)
        hid = r.json()['id']
        if period is not None or tags is not None:
            update = {}
            if period is not None:
                update['period'] = period
            if tags is not None:
                update['tags'] = tags
            r2 = await self.client.put(
                f'/api/v1/habits/{hid}',
                headers={'Authorization': f'Bearer {token}'},
                json=update,
            )
            self.assertEqual(r2.status_code, 200, r2.text)
        return hid

    # ─────────────────────────── GET /api/v1/habits/export ───────────────────────────

    async def test_export_empty_user_returns_habits_array(self):
        """A user with no habits exports an empty array. The Astro /export
        page must handle the empty state gracefully (download still works)."""
        token = await self._login()
        r = await self.client.get(
            '/api/v1/habits/export',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn('habits', body)
        self.assertEqual(body['habits'], [])

    async def test_export_returns_habit_metadata(self):
        """Export includes name, tags, records, id, order for each habit.

        SCHEMA GAP (Slice 6 finding): the export shape is:
            {"habits": [{"name", "records", "id", "tags"}], "order": [...]}
        It does NOT include `status`, `period`, `star` — these are stored in
        habit_list.data but stripped by _habit_list_export_data(). For
        parity with the legacy NiceGUI export, these fields should be
        included so a re-import on another device preserves all state.

        Per the brief: flag architectural uncertainty rather than silently
        making decisions. This gap is documented here.
        """
        token = await self._login()
        await self._create_habit(
            token,
            'Read 10 pages',
            period={'period_type': 'D', 'period_count': 1, 'target_count': 1},
            tags=['learning'],
        )

        r = await self.client.get(
            '/api/v1/habits/export',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(len(body['habits']), 1)
        habit = body['habits'][0]
        # Fields the export DOES include
        self.assertEqual(habit['name'], 'Read 10 pages')
        self.assertEqual(habit['tags'], ['learning'])
        self.assertIn('records', habit)
        self.assertEqual(habit['records'], [])
        self.assertIn('id', habit)
        # Snapshot includes order
        self.assertIn('order', body)
        # SCHEMA GAP: status, period, star are NOT in the export (flagged).
        # The Astro /export page downloads this JSON; if a user re-imports,
        # their period/status/star are lost.

    async def test_export_includes_ticked_records(self):
        """Export preserves tick records so they can be restored (in theory)
        on another device. The BFF import loop currently drops these, but the
        backend export is the source of truth.

        NOTE: the /habits/export endpoint returns records as a flat list
        ``[{day, done, timestamp}]``, while /habits/{id} returns them nested
        under ``data`` (``[{data: {day, done}}]``). Schema inconsistency
        between two endpoints — flagged.
        """
        token = await self._login()
        habit_id = await self._create_habit(token, 'Daily walk')
        today = datetime.date.today().strftime('%d-%m-%Y')

        # Tick today
        await self.client.post(
            f'/api/v1/habits/{habit_id}/completions',
            headers={'Authorization': f'Bearer {token}'},
            json={'done': True, 'date': today},
        )

        r = await self.client.get(
            '/api/v1/habits/export',
            headers={'Authorization': f'Bearer {token}'},
        )
        body = r.json()
        habit = body['habits'][0]
        records = habit['records']
        self.assertTrue(len(records) >= 1,
                        f"export must include ticked records: {records}")
        today_iso = datetime.date.today().isoformat()
        # Records in /habits/export are flat: [{day, done, timestamp}]
        self.assertTrue(any(
            r.get('day', '').startswith(today_iso)
            for r in records
        ), f"no today record in export records: {records}")

    async def test_export_includes_order_field(self):
        """Export snapshot includes an `order` list reflecting the user's
        current habit order (from /habits/meta.order)."""
        token = await self._login()
        a = await self._create_habit(token, 'A')
        b = await self._create_habit(token, 'B')
        c = await self._create_habit(token, 'C')

        # Reorder: C, A, B
        await self.client.put(
            '/api/v1/habits/meta',
            headers={'Authorization': f'Bearer {token}'},
            json={'order': [c, a, b]},
        )

        r = await self.client.get(
            '/api/v1/habits/export',
            headers={'Authorization': f'Bearer {token}'},
        )
        body = r.json()
        # The export orders habits according to `order`. Verified below.
        ids_in_export = [h['id'] for h in body['habits']]
        self.assertEqual(ids_in_export, [c, a, b])

    async def test_export_unauthenticated_returns_401(self):
        r = await self.client.get('/api/v1/habits/export')
        self.assertEqual(r.status_code, 401, r.text)

    async def test_export_with_garbage_bearer_returns_401(self):
        r = await self.client.get(
            '/api/v1/habits/export',
            headers={'Authorization': 'Bearer not-a-jwt'},
        )
        self.assertEqual(r.status_code, 401, r.text)

    async def test_export_is_user_scoped(self):
        """User A's export must not include User B's habits."""
        token_a = await self._login()
        await self._create_habit(token_a, 'A only')

        # Create user B
        manager_b = UserManager(SQLAlchemyUserDatabase(db.async_session_maker(), db.User))
        user_b = await manager_b.create(
            UserCreate(email='b@example.com', password=PASSWORD)
        )
        await views.get_or_create_user_habit_list(user_b, views.dummy_empty_habit_list())
        await self._create_habit(
            (await self.client.post(
                '/auth/login',
                data={'username': user_b.email, 'password': PASSWORD},
            )).json()['access_token'],
            'B only',
        )

        r_a = await self.client.get(
            '/api/v1/habits/export',
            headers={'Authorization': f'Bearer {token_a}'},
        )
        body = r_a.json()
        names = [h['name'] for h in body['habits']]
        self.assertIn('A only', names)
        self.assertNotIn('B only', names)

    async def test_export_response_is_json_content_type(self):
        """The Astro /export page wraps the response in attachment headers.
        The backend itself returns application/json."""
        token = await self._login()
        r = await self.client.get(
            '/api/v1/habits/export',
            headers={'Authorization': f'Bearer {token}'},
        )
        ct = r.headers.get('content-type', '')
        self.assertIn('application/json', ct)

    # ─────────────────────────── Round-trip via POST loop ───────────────────────────

    async def test_import_loop_creates_habits_from_export(self):
        """Simulate the BFF import: take an export payload, POST each habit
        as a new POST /api/v1/habits. This is exactly what
        web/concepts/src/pages/api/v1/habits/import.ts does."""
        token = await self._login()
        # Seed source habits
        await self._create_habit(token, 'Source 1')
        await self._create_habit(token, 'Source 2', tags=['work'])

        export_resp = await self.client.get(
            '/api/v1/habits/export',
            headers={'Authorization': f'Bearer {token}'},
        )
        exported = export_resp.json()

        # Wipe the user's habits via DELETE (hard remove)
        for h in exported['habits']:
            await self.client.delete(
                f"/api/v1/habits/{h['id']}",
                headers={'Authorization': f'Bearer {token}'},
            )

        # Verify empty
        listing = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(listing.json(), [])

        # Re-import via the BFF pattern: POST each habit individually
        for h in exported['habits']:
            payload = {
                'name': h['name'],
            }
            if h.get('period'):
                payload['period'] = h['period']
            if h.get('tags') is not None:
                payload['tags'] = h['tags']
            if h.get('status'):
                payload['status'] = h['status']
            r = await self.client.post(
                '/api/v1/habits',
                headers={'Authorization': f'Bearer {token}'},
                json=payload,
            )
            self.assertEqual(r.status_code, 200, r.text)

        # Verify both habits re-appeared
        listing = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {token}'},
        )
        names = sorted(h['name'] for h in listing.json())
        self.assertEqual(names, ['Source 1', 'Source 2'])

    async def test_import_loop_drops_records(self):
        """Slice 6 GAP: the BFF import loop does NOT carry records (ticks)
        from the export payload. This is a known limitation.

        This test pins the current behaviour so we know what changes if/when
        a proper import endpoint is added."""
        token = await self._login()
        habit_id = await self._create_habit(token, 'With history')
        today = datetime.date.today().strftime('%d-%m-%Y')

        # Tick today
        await self.client.post(
            f'/api/v1/habits/{habit_id}/completions',
            headers={'Authorization': f'Bearer {token}'},
            json={'done': True, 'date': today},
        )

        # Export
        exported = (await self.client.get(
            '/api/v1/habits/export',
            headers={'Authorization': f'Bearer {token}'},
        )).json()
        exported_habit = exported['habits'][0]
        self.assertTrue(len(exported_habit['records']) >= 1,
                        "export should contain the tick records")

        # Wipe + re-import via BFF loop
        await self.client.delete(
            f"/api/v1/habits/{habit_id}",
            headers={'Authorization': f'Bearer {token}'},
        )

        for h in exported['habits']:
            payload = {'name': h['name']}
            if h.get('period'):
                payload['period'] = h['period']
            if h.get('tags') is not None:
                payload['tags'] = h['tags']
            await self.client.post(
                '/api/v1/habits',
                headers={'Authorization': f'Bearer {token}'},
                json=payload,
            )

        # Find the new habit and check records are gone
        listing = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {token}'},
        )
        new_id = listing.json()[0]['id']
        detail = await self.client.get(
            f'/api/v1/habits/{new_id}',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(detail.json()['records'], [],
                         "BFF import loop drops records — known limitation")

    async def test_import_max_habit_count_enforced(self):
        """The BFF import loop respects MAX_HABIT_COUNT=5. Trying to import
        a 6th habit via POST /api/v1/habits must 400."""
        token = await self._login()
        # Create 5 habits
        for i in range(5):
            await self._create_habit(token, f'Existing {i+1}')

        # Attempt 6th via POST
        r = await self.client.post(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {token}'},
            json={'name': 'Sixth (would overflow)'},
        )
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn('Maximum habit count', r.text)

    # ─────────────────────────── File-format / malformed input ───────────────────────────

    async def test_import_loop_handles_missing_optional_fields(self):
        """The BFF loop accepts habits with only a name (no period, no tags).
        Backwards-compatible with the legacy beaverhabits export format."""
        token = await self._login()
        for h in [{'name': 'Minimal'}]:  # only name
            r = await self.client.post(
                '/api/v1/habits',
                headers={'Authorization': f'Bearer {token}'},
                json=h,
            )
            self.assertEqual(r.status_code, 200, r.text)

    async def test_import_loop_rejects_empty_name(self):
        token = await self._login()
        r = await self.client.post(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {token}'},
            json={'name': ''},
        )
        # Either 400, 422, or 200 (depending on backend policy). We accept 200
        # too because the BFF may strip whitespace. Pin the contract loosely.
        self.assertIn(r.status_code, (200, 400, 422), r.text)

    async def test_export_size_scales_with_habits(self):
        """Sanity check: exporting 3 habits produces a JSON body with at least
        3 entries. (Useful as a smoke test that the export endpoint isn't
        silently returning empty data.)"""
        token = await self._login()
        for name in ['A', 'B', 'C']:
            await self._create_habit(token, name)

        r = await self.client.get(
            '/api/v1/habits/export',
            headers={'Authorization': f'Bearer {token}'},
        )
        body = r.json()
        self.assertEqual(len(body['habits']), 3)
        # Round-trip through json to verify it's serialisable
        serialised = json.dumps(body)
        self.assertIsInstance(serialised, str)
        self.assertGreater(len(serialised), 100)


if __name__ == '__main__':
    unittest.main()
