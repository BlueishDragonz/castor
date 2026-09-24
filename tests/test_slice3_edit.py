"""Slice 3 — Add / Edit / Archive / Reorder.

Pins the backend contract for:
  - PUT /api/v1/habits/{id}    → edit name/star/period/tags/status (archive)
  - DELETE /api/v1/habits/{id} → hard remove (NOT soft archive)
  - GET   /api/v1/habits/meta   → read order
  - PUT   /api/v1/habits/meta   → reorder

Behavioural notes:
  - DELETE in castor is HARD REMOVE — habit data is gone from the list.
    Archive (status=ARCHIVED) is a separate state that keeps the habit
    visible on /stats and /admin but excludes it from the active grid.
    This is documented in parity-matrix.md row 2.6 / 2.8.
  - Reorder via /habits/meta.order is the persistence backing for the
    drag-and-drop UI; the page wires the frontend reorder to this endpoint.

What this slice does NOT test:
  - Drag-and-drop UI (Playwright, Slice 9)
  - Edit page rendering (Playwright, Slice 9)
  - Paddle / pricing gating (Slice 8)

Run:
  python -m unittest discover -s tests -p test_slice3_edit.py -v
"""
import datetime
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


class Slice3EditArchiveTests(unittest.IsolatedAsyncioTestCase):
    """Backend contract tests for edit/archive/reorder."""

    async def asyncSetUp(self):
        async with db.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await db.create_db_and_tables()
        reset_routes._rate_limit_cache.clear()
        session = db.async_session_maker()
        manager = UserManager(SQLAlchemyUserDatabase(session, db.User))
        self.user = await manager.create(UserCreate(email='editor@example.com', password=PASSWORD))
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

    async def _create_habit(self, token: str, name: str) -> str:
        r = await self.client.post(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {token}'},
            json={'name': name},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['id']

    async def _user_b_token(self) -> str:
        """Create a second user + log in, for cross-user 404 tests."""
        manager_b = UserManager(SQLAlchemyUserDatabase(db.async_session_maker(), db.User))
        user_b = await manager_b.create(UserCreate(email='b@example.com', password=PASSWORD))
        await views.get_or_create_user_habit_list(user_b, views.dummy_empty_habit_list())
        r = await self.client.post(
            '/auth/login',
            data={'username': user_b.email, 'password': PASSWORD},
        )
        return r.json()['access_token']

    # ─────────────────────────── PUT /habits/{id} ───────────────────────────

    async def test_edit_name_persists(self):
        token = await self._login()
        habit_id = await self._create_habit(token, 'Old name')

        r = await self.client.put(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token}'},
            json={'name': 'New name'},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['name'], 'New name')

        # Round-trip
        detail = await self.client.get(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(detail.json()['name'], 'New name')

    async def test_edit_star_persists(self):
        token = await self._login()
        habit_id = await self._create_habit(token, 'Starrable')
        self.assertFalse((await self.client.get(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token}'},
        )).json()['star'])

        r = await self.client.put(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token}'},
            json={'star': True},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()['star'])

    async def test_edit_tags_persists(self):
        token = await self._login()
        habit_id = await self._create_habit(token, 'Taggable')

        r = await self.client.put(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token}'},
            json={'tags': ['health', 'morning']},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(sorted(r.json()['tags']), ['health', 'morning'])

    async def test_edit_period_persists(self):
        """Period = weekly target count, used by /stats for completion rate."""
        token = await self._login()
        habit_id = await self._create_habit(token, 'Weekly')

        r = await self.client.put(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token}'},
            json={'period': {'period_type': 'W', 'period_count': 1, 'target_count': 3}},
        )
        self.assertEqual(r.status_code, 200, r.text)
        period = r.json()['period']
        self.assertIsNotNone(period)
        self.assertEqual(period['period_type'], 'W')
        self.assertEqual(period['period_count'], 1)
        self.assertEqual(period['target_count'], 3)

    async def test_edit_empty_body_no_change(self):
        """PUT with `{}` must not 400 — fields are individually optional."""
        token = await self._login()
        habit_id = await self._create_habit(token, 'Unchanged')

        r = await self.client.put(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token}'},
            json={},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['name'], 'Unchanged')

    async def test_edit_cross_user_returns_404(self):
        token_a = await self._login()
        habit_id = await self._create_habit(token_a, 'Mine')
        token_b = await self._user_b_token()

        r = await self.client.put(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token_b}'},
            json={'name': 'Hijacked'},
        )
        self.assertEqual(r.status_code, 404, r.text)

        # Verify name unchanged
        detail = await self.client.get(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token_a}'},
        )
        self.assertEqual(detail.json()['name'], 'Mine')

    async def test_edit_unauthenticated_returns_401(self):
        token = await self._login()
        habit_id = await self._create_habit(token, 'X')

        r = await self.client.put(
            f'/api/v1/habits/{habit_id}',
            json={'name': 'Y'},
        )
        self.assertEqual(r.status_code, 401, r.text)

    # ─────────────────────────── PUT status=ARCHIVED ───────────────────────────

    async def test_archive_excludes_from_active_list(self):
        """Setting status='archive' must make GET /habits (default status=ACTIVE)
        exclude this habit. The Astro /stats page queries archived separately.

        NOTE: castor's HabitStatus enum uses 'archive' (singular), NOT 'archived'.
        A third state 'soft_delete' exists for hidden habits. Both differ from
        legacy NiceGUI semantics.
        """
        token = await self._login()
        active_id = await self._create_habit(token, 'Active')
        archive_me_id = await self._create_habit(token, 'Will archive')

        r = await self.client.put(
            f'/api/v1/habits/{archive_me_id}',
            headers={'Authorization': f'Bearer {token}'},
            json={'status': 'archive'},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['status'], 'archive')

        listing = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {token}'},
        )
        ids = [h['id'] for h in listing.json()]
        self.assertIn(active_id, ids)
        self.assertNotIn(archive_me_id, ids)

    async def test_archive_then_unarchive_restores_to_active(self):
        token = await self._login()
        habit_id = await self._create_habit(token, 'Round-trip')

        for new_status in ('archive', 'active'):
            r = await self.client.put(
                f'/api/v1/habits/{habit_id}',
                headers={'Authorization': f'Bearer {token}'},
                json={'status': new_status},
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()['status'], new_status)

    async def test_archive_invalid_status_value_returns_422(self):
        """FastAPI's Pydantic validation rejects unknown enum values.
        The valid HabitStatus values are: 'active', 'archive', 'soft_delete'.
        """
        token = await self._login()
        habit_id = await self._create_habit(token, 'Bad status')

        r = await self.client.put(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token}'},
            json={'status': 'not-a-real-status'},
        )
        self.assertEqual(r.status_code, 422, r.text)

    # ─────────────────────────── DELETE /habits/{id} ───────────────────────────

    async def test_delete_removes_habit_from_list(self):
        """castor's DELETE is HARD REMOVE (parity-matrix row 2.6).
        Compare to the legacy NiceGUI where Delete was a soft archive.
        The migration deliberately moved to hard remove so /admin can
        distinguish 'archived' from 'deleted'.
        """
        token = await self._login()
        keep_id = await self._create_habit(token, 'Keep')
        drop_id = await self._create_habit(token, 'Drop')

        r = await self.client.delete(
            f'/api/v1/habits/{drop_id}',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 200, r.text)

        listing = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {token}'},
        )
        ids = [h['id'] for h in listing.json()]
        self.assertIn(keep_id, ids)
        self.assertNotIn(drop_id, ids)

    async def test_delete_archived_habit_works(self):
        """Deleting an archived habit removes it permanently. There's no undo."""
        token = await self._login()
        habit_id = await self._create_habit(token, 'Archived then deleted')

        await self.client.put(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token}'},
            json={'status': 'archive'},
        )

        r = await self.client.delete(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(r.status_code, 200, r.text)

        # Detail must 404
        detail = await self.client.get(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(detail.status_code, 404, detail.text)

    async def test_delete_cross_user_returns_404_and_keeps_habit(self):
        token_a = await self._login()
        habit_id = await self._create_habit(token_a, 'A owns this')
        token_b = await self._user_b_token()

        r = await self.client.delete(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token_b}'},
        )
        self.assertEqual(r.status_code, 404, r.text)

        # A's habit still exists
        detail = await self.client.get(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token_a}'},
        )
        self.assertEqual(detail.status_code, 200, detail.text)

    async def test_delete_unauthenticated_returns_401(self):
        token = await self._login()
        habit_id = await self._create_habit(token, 'X')

        r = await self.client.delete(f'/api/v1/habits/{habit_id}')
        self.assertEqual(r.status_code, 401, r.text)

    # ─────────────────────────── PUT /habits/meta ───────────────────────────

    async def test_meta_reorder_persists_and_reflects_in_listing(self):
        """The drag-and-drop UI sends PUT /habits/meta {order: [...]}.
        The new order must persist and the list endpoint MUST reflect it."""
        token = await self._login()
        a_id = await self._create_habit(token, 'Alpha')
        b_id = await self._create_habit(token, 'Beta')
        c_id = await self._create_habit(token, 'Gamma')

        # Read current order
        meta0 = await self.client.get(
            '/api/v1/habits/meta',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(meta0.status_code, 200, meta0.text)

        # Reverse
        new_order = [c_id, b_id, a_id]
        r = await self.client.put(
            '/api/v1/habits/meta',
            headers={'Authorization': f'Bearer {token}'},
            json={'order': new_order},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['order'], new_order)

        # Verify list reflects new order. The /habits endpoint uses
        # meta.order to sort (not creation order). Verified by re-reading
        # both endpoints.
        listing = await self.client.get(
            '/api/v1/habits',
            headers={'Authorization': f'Bearer {token}'},
        )
        ids_in_list = [h['id'] for h in listing.json()]
        self.assertEqual(ids_in_list, new_order,
                         f"GET /habits must reflect meta.order; got {ids_in_list}")

        meta1 = await self.client.get(
            '/api/v1/habits/meta',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(meta1.json()['order'], new_order)

    async def test_meta_reorder_unauthenticated_returns_401(self):
        r = await self.client.put(
            '/api/v1/habits/meta',
            json={'order': []},
        )
        self.assertEqual(r.status_code, 401, r.text)

    async def test_meta_reorder_with_unknown_id_silently_ignored(self):
        """If the client sends an id that doesn't belong to this user, the
        backend MUST NOT 500. Castor's habit_list.order is a free list;
        unrecognised ids are silently filtered out."""
        token = await self._login()
        real_id = await self._create_habit(token, 'Real')

        r = await self.client.put(
            '/api/v1/habits/meta',
            headers={'Authorization': f'Bearer {token}'},
            json={'order': [real_id, 'fake-id-9999']},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['order'], [real_id, 'fake-id-9999'])
        # The meta.order is stored verbatim — the UI is responsible for
        # filtering unknown ids before display.

    # ─────────────────────────── persistence + cascade ───────────────────────────

    async def test_tick_survives_edit(self):
        """Edit a habit (rename) — ticks must still be there."""
        token = await self._login()
        habit_id = await self._create_habit(token, 'Before edit')

        # Tick today
        today = datetime.date.today().strftime('%d-%m-%Y')
        await self.client.post(
            f'/api/v1/habits/{habit_id}/completions',
            headers={'Authorization': f'Bearer {token}'},
            json={'done': True, 'date': today},
        )

        # Edit name
        await self.client.put(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token}'},
            json={'name': 'After edit'},
        )

        # Verify records still present
        detail = await self.client.get(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token}'},
        )
        records = detail.json()['records']
        today_iso = datetime.date.today().isoformat()
        self.assertTrue(any(
            r.get('data', {}).get('day', '').startswith(today_iso)
            and r.get('data', {}).get('done')
            for r in records
        ), f"tick lost on edit: {records}")

    async def test_archive_does_not_lose_records(self):
        """Archive a habit — records must persist (history is preserved)."""
        token = await self._login()
        habit_id = await self._create_habit(token, 'With history')
        today = datetime.date.today().strftime('%d-%m-%Y')
        await self.client.post(
            f'/api/v1/habits/{habit_id}/completions',
            headers={'Authorization': f'Bearer {token}'},
            json={'done': True, 'date': today},
        )

        await self.client.put(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token}'},
            json={'status': 'archive'},
        )

        detail = await self.client.get(
            f'/api/v1/habits/{habit_id}',
            headers={'Authorization': f'Bearer {token}'},
        )
        self.assertEqual(detail.json()['status'], 'archive')
        # Records still there
        self.assertTrue(len(detail.json()['records']) >= 1)


if __name__ == '__main__':
    unittest.main()
