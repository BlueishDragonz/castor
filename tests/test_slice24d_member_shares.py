"""
Slice 24d: Circles member shares OWN habit with the circle.

Pins the new endpoints added to satisfy dogfood finding E
("after joining, invitee is asked if they'd like to share their habits"):

- POST   /api/v1/circles/{id}/members/me/habits          share one of MY
                                                            habits
- GET    /api/v1/circles/{id}/members/me/habits          list MY shares
- DELETE /api/v1/circles/{id}/members/me/habits/{hid}    un-share
- GET    /api/v1/circles/{id}                            shared_habits now
                                                            carries
                                                            owner_user_id
- GET    /api/v1/circles/{id}/feed                       returns per-owner
                                                            entries, not
                                                            only owner's

The welcome page (`circles/[id]/welcome.astro`) consumes these.
"""
import asyncio
import datetime
import os
import tempfile
import unittest
from unittest.mock import patch as mock_patch

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

import castor.main  # noqa: F401
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

PASSWORD = 'TestPass1234!'


def _make_habit_list(habit_id: str, name: str) -> dict:
    """Minimal habit list the storage layer accepts: one named habit."""
    return {
        'habits': [
            {
                'id': habit_id,
                'name': name,
                'records': [],
            },
        ],
    }


class _Harness(unittest.IsolatedAsyncioTestCase):
    """Two-user harness: alice owns a circle, bob joins it."""

    # The current circle id for `_join`'s default — tests set this
    # right before calling `_join` (it's a tiny convenience attr, not
    # a lifecycle field).
    _circle_id: int

    async def asyncSetUp(self):
        async with db.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await db.create_db_and_tables()
        reset_routes._rate_limit_cache.clear()
        self.session = db.async_session_maker()

        # alice (owner) — has habit "Run"
        self.alice = await self._create_user('alice@example.com')
        await views.get_or_create_user_habit_list(
            self.alice, views.dummy_empty_habit_list(),
        )
        # write a real habit so alice can share it
        await self._seed_habit(self.alice, 'alice_run', 'Run')

        # bob (member) — has habit "Read"
        self.bob = await self._create_user('bob@example.com')
        await views.get_or_create_user_habit_list(
            self.bob, views.dummy_empty_habit_list(),
        )
        await self._seed_habit(self.bob, 'bob_read', 'Read')

        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://test',
        )
        self.alice_headers = await self._login('alice@example.com')
        self.bob_headers = await self._login('bob@example.com')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.session.close()

    async def _create_user(self, email: str):
        manager = UserManager(SQLAlchemyUserDatabase(self.session, db.User))
        u = await manager.create(UserCreate(email=email, password=PASSWORD))
        return u

    async def _login(self, email: str) -> dict:
        r = await self.client.post(
            '/auth/login',
            data={'username': email, 'password': PASSWORD},
        )
        assert r.status_code == 200, r.text
        return {'Authorization': f"Bearer {r.json()['access_token']}"}

    async def _seed_habit(self, user, habit_id: str, name: str):
        """Replace the user's habit list with a 1-habit list containing
        a known id.

        `init_user_habit_list` refuses to overwrite an existing row,
        so we go through the cached `DatabasePersistentDict` and mutate
        its `data['habits']` directly — this triggers a debounced
        backup. We then await the debounce + flush window so the write
        is durable before the test reads it back.
        """
        cache = getattr(views.user_storage, 'user', None)
        if not isinstance(cache, dict) or user.id not in cache:
            # No cached list yet — initialise once with the seed habit.
            await views.user_storage.init_user_habit_list(
                user, views.DictHabitList(_make_habit_list(habit_id, name)),
            )
            return
        persistent = cache[user.id]
        # Mutate the underlying dict in place so observers fire.
        persistent.clear()
        persistent.update({'habits': _make_habit_list(habit_id, name)['habits']})
        # Wait out the 250ms debounce window then force a synchronous
        # flush so the next read sees the new list.
        await asyncio.sleep(0.4)
        flush = getattr(persistent, '_flush_backup', None)
        if flush is not None:
            await flush()

    async def _create_circle(self, owner_headers: dict | None = None) -> int:
        headers = owner_headers or self.alice_headers
        r = await self.client.post('/api/v1/circles', json={'name': 'Test'}, headers=headers)
        assert r.status_code == 201, r.text
        return r.json()['id']

    async def _mint_invite(self, circle_id: int) -> str:
        r = await self.client.post(
            f'/api/v1/circles/{circle_id}/invites',
            json={'delivery': 'link'}, headers=self.alice_headers,
        )
        assert r.status_code == 201, r.text
        return r.json()['raw_token']

    async def _join(self, token: str, headers: dict | None = None) -> httpx.Response:
        h = headers or self.bob_headers
        return await self.client.post(
            '/api/v1/circles/' + str(self._circle_id) + '/join',
            json={'token': token}, headers=h,
        )


class Slice24dMemberShareTests(_Harness):
    """Member shares OWN habit; admin endpoints respect ownership."""

    async def test_member_can_share_own_habit(self):
        cid = await self._create_circle()
        token = await self._mint_invite(cid)
        self._circle_id = cid
        r = await self._join(token)
        assert r.status_code == 201, r.text

        # bob shares his own habit "Read" with the circle
        r = await self.client.post(
            f'/api/v1/circles/{cid}/members/me/habits',
            json={'habit_id': 'bob_read', 'visibility': 'ticks'},
            headers=self.bob_headers,
        )
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body['habit_id'], 'bob_read')
        self.assertEqual(body['visibility'], 'ticks')

    async def test_member_cannot_share_owners_habit(self):
        """Sharing someone else's habit (by id) is 404, not silently allowed."""
        cid = await self._create_circle()
        token = await self._mint_invite(cid)
        self._circle_id = cid
        await self._join(token)

        r = await self.client.post(
            f'/api/v1/circles/{cid}/members/me/habits',
            json={'habit_id': 'alice_run', 'visibility': 'ticks'},
            headers=self.bob_headers,
        )
        self.assertEqual(r.status_code, 404, r.text)

    async def test_list_my_shared_habits_returns_only_mine(self):
        cid = await self._create_circle()
        token = await self._mint_invite(cid)
        self._circle_id = cid
        await self._join(token)

        # bob shares "Read"
        await self.client.post(
            f'/api/v1/circles/{cid}/members/me/habits',
            json={'habit_id': 'bob_read', 'visibility': 'ticks'},
            headers=self.bob_headers,
        )

        # alice (owner) shares "Run" too — via the owner endpoint
        await self.client.post(
            f'/api/v1/circles/{cid}/habits',
            json={'habit_id': 'alice_run', 'visibility': 'ticks+streak'},
            headers=self.alice_headers,
        )

        # bob lists his
        r = await self.client.get(
            f'/api/v1/circles/{cid}/members/me/habits',
            headers=self.bob_headers,
        )
        self.assertEqual(r.status_code, 200, r.text)
        mine = r.json()
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]['habit_id'], 'bob_read')

        # alice lists hers
        r = await self.client.get(
            f'/api/v1/circles/{cid}/members/me/habits',
            headers=self.alice_headers,
        )
        self.assertEqual(r.status_code, 200)
        mine = r.json()
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]['habit_id'], 'alice_run')

    async def test_unshare_my_habit_removes_only_my_row(self):
        cid = await self._create_circle()
        token = await self._mint_invite(cid)
        self._circle_id = cid
        await self._join(token)

        # both share a habit (different ids, both present)
        await self.client.post(
            f'/api/v1/circles/{cid}/habits',
            json={'habit_id': 'alice_run', 'visibility': 'ticks'},
            headers=self.alice_headers,
        )
        await self.client.post(
            f'/api/v1/circles/{cid}/members/me/habits',
            json={'habit_id': 'bob_read', 'visibility': 'ticks'},
            headers=self.bob_headers,
        )

        # bob un-shares
        r = await self.client.delete(
            f'/api/v1/circles/{cid}/members/me/habits/bob_read',
            headers=self.bob_headers,
        )
        self.assertEqual(r.status_code, 204, r.text)

        # alice's still present
        detail = await self.client.get(
            f'/api/v1/circles/{cid}', headers=self.alice_headers,
        )
        ids = [h['habit_id'] for h in detail.json()['shared_habits']]
        self.assertIn('alice_run', ids)
        self.assertNotIn('bob_read', ids)

    async def test_non_member_cannot_share_into_circle(self):
        cid = await self._create_circle()
        # eve is registered but not a member of this circle
        await self._create_user('eve@example.com')
        eve_headers = await self._login('eve@example.com')

        r = await self.client.post(
            f'/api/v1/circles/{cid}/members/me/habits',
            json={'habit_id': 'whatever', 'visibility': 'ticks'},
            headers=eve_headers,
        )
        self.assertEqual(r.status_code, 403, r.text)

    async def test_owner_can_also_use_member_endpoint(self):
        """The owner is auto-seeded as a member; member endpoint works for them too."""
        cid = await self._create_circle()
        await self.client.post(
            f'/api/v1/circles/{cid}/members/me/habits',
            json={'habit_id': 'alice_run', 'visibility': 'ticks'},
            headers=self.alice_headers,
        )
        r = await self.client.get(
            f'/api/v1/circles/{cid}/members/me/habits',
            headers=self.alice_headers,
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()), 1)


class Slice24dSharedHabitsOwnerFieldTests(_Harness):
    """GET /circles/{id}.shared_habits now carries owner_user_id."""

    async def test_shared_habits_response_includes_owner_user_id(self):
        cid = await self._create_circle()
        token = await self._mint_invite(cid)
        self._circle_id = cid
        await self._join(token)

        await self.client.post(
            f'/api/v1/circles/{cid}/members/me/habits',
            json={'habit_id': 'bob_read', 'visibility': 'ticks'},
            headers=self.bob_headers,
        )

        r = await self.client.get(
            f'/api/v1/circles/{cid}', headers=self.alice_headers,
        )
        self.assertEqual(r.status_code, 200, r.text)
        rows = r.json()['shared_habits']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['habit_id'], 'bob_read')
        self.assertEqual(rows[0]['owner_user_id'], str(self.bob.id))


class Slice24dFeedIncludesMemberSharesTests(_Harness):
    """GET /circles/{id}/feed returns one entry per shared habit, keyed by the
    actual owner_email — not only the circle owner's email."""

    async def test_feed_returns_per_owner_entries(self):
        cid = await self._create_circle()
        token = await self._mint_invite(cid)
        self._circle_id = cid
        await self._join(token)

        # both share
        await self.client.post(
            f'/api/v1/circles/{cid}/habits',
            json={'habit_id': 'alice_run', 'visibility': 'ticks+streak'},
            headers=self.alice_headers,
        )
        await self.client.post(
            f'/api/v1/circles/{cid}/members/me/habits',
            json={'habit_id': 'bob_read', 'visibility': 'ticks'},
            headers=self.bob_headers,
        )

        r = await self.client.get(
            f'/api/v1/circles/{cid}/feed',
            headers=self.alice_headers,
        )
        self.assertEqual(r.status_code, 200, r.text)
        feed = r.json()
        self.assertEqual(len(feed), 2)
        by_owner = {f['owner_email']: f for f in feed}
        self.assertIn('alice@example.com', by_owner)
        self.assertIn('bob@example.com', by_owner)
        self.assertEqual(by_owner['alice@example.com']['habit_id'], 'alice_run')
        self.assertEqual(by_owner['bob@example.com']['habit_id'], 'bob_read')

    async def test_two_members_same_habit_id_no_collision(self):
        """The new (circle_id, habit_id, owner_user_id) unique index lets two
        members share a habit with the same string id (e.g. a coincidence
        in the short-hash generator).

        We bypass the share endpoint and write directly to the
        CircleHabit table to demonstrate the schema accepts two rows
        that would have collided under the old 2-column unique constraint.
        """
        cid = await self._create_circle()
        token = await self._mint_invite(cid)
        self._circle_id = cid
        await self._join(token)

        from castor.app.circles import CircleHabit
        from sqlalchemy import insert
        async with db.async_session_maker() as session:
            async with session.begin():
                await session.execute(insert(CircleHabit).values(
                    circle_id=cid,
                    habit_id='shared_id',
                    owner_user_id=str(self.bob.id),
                    visibility='ticks',
                    share_notes=False,
                ))
                await session.execute(insert(CircleHabit).values(
                    circle_id=cid,
                    habit_id='shared_id',
                    owner_user_id=str(self.alice.id),
                    visibility='ticks',
                    share_notes=False,
                ))

        # Both show up in feed — proving the new 3-column unique index
        # permits it (old 2-column constraint would have raised IntegrityError).
        # Note: the feed endpoint looks up each share's habit by id in
        # the owner's HabitListModel; since the test wrote CircleHabit
        # rows directly without seeding matching habits, the feed
        # will gracefully skip them. What we're pinning here is that
        # the DB accepts two rows with the same (circle_id, habit_id)
        # and different owner_user_id — proven by the inserts above
        # completing without IntegrityError.
        r = await self.client.get(
            f'/api/v1/circles/{cid}/feed', headers=self.alice_headers,
        )
        self.assertEqual(r.status_code, 200, r.text)
        # Confirm directly from the DB that the 3-column index allows
        # the two rows to coexist.
        from castor.app.circles import CircleHabit
        from sqlalchemy import select
        async with db.async_session_maker() as session:
            rows = (await session.execute(
                select(CircleHabit).where(
                    CircleHabit.circle_id == cid,
                    CircleHabit.habit_id == 'shared_id',
                )
            )).scalars().all()
            self.assertEqual(len(rows), 2)
            self.assertEqual(
                {str(r.owner_user_id) for r in rows},
                {str(self.alice.id), str(self.bob.id)},
            )


if __name__ == '__main__':
    unittest.main()
