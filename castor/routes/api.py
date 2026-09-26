import datetime
from copy import deepcopy
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from loguru import logger
from pydantic import BaseModel, Field, StrictBool

from castor import views
from castor.app import crud
from castor.app.auth import user_from_token
from castor.app.crud import (
    create_user_api_token,
    delete_user_api_token,
    get_user_api_token,
    get_user_by_api_token,
    get_user_configs,
    reset_user_api_token,
    update_user_configs,
)
from castor.app.db import User
from castor.app.dependencies import current_active_user
from castor.css_sanitizer import sanitize_css
from castor.core.completions import CStatus, get_habit_date_completion
from castor.events import HabitListChanged, publish
from castor.realtime import manager
from castor.storage import get_user_dict_storage
from castor.storage.storage import (
    Habit,
    HabitFrequency,
    HabitList,
    HabitListBuilder,
    HabitListNotFoundError,
    HabitStatus,
    habits_in_group_order,
)

from castor.app.rate_limits import api_user_limit, consume
from castor.configs import settings

api_router = APIRouter(dependencies=[Depends(api_user_limit)])


class AccountDeleteRequest(BaseModel):
    """Step-up proof for irreversible account erasure.

    F18: the identity erased is ALWAYS the principal the bearer token resolved
    to. A separate, client-writable channel (the non-httpOnly ``castor_user``
    cookie) can no longer choose whose account is destroyed, because the
    backend never reads it. The password is verified against that principal's
    own hash inside the erasing transaction.
    """

    password: str = Field(min_length=1, max_length=4096)


@api_router.delete("/account", status_code=204, tags=["account"])
async def delete_account(
    payload: AccountDeleteRequest,
    user: User = Depends(current_active_user),
) -> Response:
    """Erase an account after fresh-password step-up, in a single transaction.

    The password is mandatory: a stolen session token alone must never be
    sufficient to destroy an account and all of its data.
    """
    from castor.app.security_actions import (
        SecurityActionError,
        delete_account as _delete_account,
    )

    try:
        await _delete_account(user.id, user.token_version, payload.password)
    except SecurityActionError as exc:
        # Fixed, public-safe message; never leaks input or backend detail.
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    return Response(status_code=204)


async def current_habit_list(user: User = Depends(current_active_user)) -> HabitList:
    habit_list = await views.get_user_habit_list(user)
    if not habit_list:
        raise HTTPException(status_code=404, detail="No habits found")
    return habit_list


class HabitListMeta(BaseModel):
    order: list[str] | None = None


@api_router.get("/habits/meta", tags=["habits"])
async def get_habits_meta(
    habit_list: HabitList = Depends(current_habit_list),
):
    return HabitListMeta(order=habit_list.order)


@api_router.put("/habits/meta", tags=["habits"])
async def put_habits_meta(
    meta: HabitListMeta,
    habit_list: HabitList = Depends(current_habit_list),
):
    if meta.order is not None:
        habit_list.order = meta.order
    return {"order": habit_list.order}


@api_router.get("/habits", tags=["habits"])
async def get_habits(
    status: HabitStatus = HabitStatus.ACTIVE,
    order_by: str | None = Query(
        default=None,
        description="Sort order: 'manual' (default), 'name', 'tags', 'status'.",
    ),
    habit_list: HabitList = Depends(current_habit_list),
):
    """List habits. Order is controlled by ``order_by`` (query) or the
    persisted ``habit_list.order_by`` (fallback). Invalid values fall back
    to manual.

    Slice 9-alt: this endpoint previously accepted only ``status`` and
    ignored any client sort preference. The parity matrix row 2.10 said
    sort was supported by the backend; the HTTP surface didn't expose it.
    """
    from castor.storage.storage import HabitOrder
    builder = HabitListBuilder(habit_list).status(status)
    if order_by:
        # HabitOrder uses canonical member names: MANUALLY, NAME, CATEGORY.
        # We accept lowercase too for Astro UI convenience.
        canonical = order_by.upper()
        try:
            builder.order_by = HabitOrder[canonical]
        except KeyError:
            pass
    habits = builder.build()
    return [{"id": x.id, "name": x.name} for x in habits]


class CreateHabit(BaseModel):
    name: str


@api_router.post("/habits", tags=["habits"])
async def post_habits(
    habit: CreateHabit,
    user: User = Depends(current_active_user),
):
    habit_list = await views.get_or_create_user_habit_list(
        user, views.dummy_empty_habit_list()
    )

    # Enforce MAX_HABIT_COUNT server-side
    active_habits = [
        h for h in habit_list.habits if h.status == HabitStatus.ACTIVE
    ]
    if len(active_habits) >= settings.MAX_HABIT_COUNT > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum habit count ({settings.MAX_HABIT_COUNT}) reached",
        )

    id = await habit_list.add(habit.name)
    logger.info(f"Created new habit {id} for user {user.email}")

    return {"id": id, "name": habit.name}


# ---------------------------------------------------------------------------
# Full-sync endpoints for native clients.
#
# Export preserves the raw records but arranges habits in the same grouped
# order as the web homepage. Import remains a whole-dict passthrough. This is
# distinct from the web import flow (which renames collisions and merges
# server-side); here the client has already merged and sends the final state.
#
# NOTE: defined before /habits/{habit_id} so "export"/"import" are not captured
# as a habit_id path param.
# ---------------------------------------------------------------------------


def _habit_list_export_data(habit_list: HabitList) -> dict:
    """Serialise the user's habit_list for the JSON export.

    The raw ``habit_list.data`` dict contains each habit as a dict written by
    ``DictHabit`` setters — fields like ``name``, ``tags``, ``period``, ``star``
    land in the dict whenever a PUT /habits/{id} updates them. The exception
    is ``status``, which is only set in the Python object by default and only
    written to the dict when explicitly set via the archive/active path.

    This function guarantees ``status`` is always present in the export so a
    round-trip (export → import on another device) preserves it. Defensive
    stringification ensures the value is a string (the enum's ``.value``)
    rather than an enum instance.
    """
    snapshot = deepcopy(habit_list.data)
    active_habits = HabitListBuilder(habit_list).status(HabitStatus.ACTIVE).build()
    grouped_active = habits_in_group_order(active_habits)
    all_habits = HabitListBuilder(habit_list).build()

    ordered_ids = [str(habit.id) for habit in grouped_active]
    seen = set(ordered_ids)
    for habit in all_habits:
        habit_id = str(habit.id)
        if habit_id not in seen:
            ordered_ids.append(habit_id)
            seen.add(habit_id)

    raw_habits = snapshot.get("habits", [])
    by_id = {str(habit["id"]): habit for habit in raw_habits if "id" in habit}
    for habit in raw_habits:
        habit_id = str(habit.get("id"))
        if habit_id not in seen:
            ordered_ids.append(habit_id)
            seen.add(habit_id)

    def _stringify(value):
        """Castor Habit enums (HabitStatus, HabitFrequency) store their
        ``.value`` (string) when serialised via ``.data``. After deepcopy
        they remain as enum instances in the list snapshot. This helper is
        defensive in case upstream changes touch the layer."""
        return getattr(value, "value", value)

    export_habits = []
    for habit_id in ordered_ids:
        if habit_id not in by_id:
            continue
        raw = by_id[habit_id]
        # Guarantee ``status`` is in the export. See function docstring.
        if "status" not in raw:
            raw["status"] = _stringify(HabitStatus.ACTIVE)
        # Normalise records to the nested ``{data: {...}}`` shape that
        # ``/habits/{id}`` returns and the Astro UI consumes (see
        # ``format_json_response`` and the ``r.data?.day`` reads in
        # ``web/concepts/src/pages/{habits/index,stats,[id]}.astro``).
        # The storage layer keeps records flat; the export must match the
        # detail endpoint's shape so a round-trip (export → import → tick
        # read) uses one canonical record shape end-to-end.
        flat_records = raw.get("records", [])
        raw["records"] = [
            {"data": {k: v for k, v in rec.items()}}
            for rec in flat_records
        ]
        export_habits.append(raw)

    snapshot["habits"] = export_habits
    snapshot["order"] = ordered_ids
    return snapshot


@api_router.get("/habits/export", tags=["habits"])
async def export_habit_list(user: User = Depends(current_active_user)):
    try:
        habit_list = await views.user_storage.get_user_habit_list(user)
    except HabitListNotFoundError:
        return {"habits": []}
    return _habit_list_export_data(habit_list)


@api_router.get("/habits/{habit_id}", tags=["habits"])
async def get_habit_detail(
    habit_id: str,
    user: User = Depends(current_active_user),
):
    habit = await views.get_user_habit(user, habit_id)
    return format_json_response(habit)


class UpdateHabit(BaseModel):
    class UpdateHabitPeriod(BaseModel):
        period_type: Literal["D", "W", "M", "Y"]
        period_count: int
        target_count: int

    name: str | None = None
    star: bool | None = None
    status: HabitStatus | None = None
    period: UpdateHabitPeriod | None = None
    tags: list[str] | None = None


@api_router.put("/habits/{habit_id}", tags=["habits"])
async def put_habit(
    habit_id: str,
    habit: UpdateHabit,
    user: User = Depends(current_active_user),
):
    existing_habit = await views.get_user_habit(user, habit_id)
    if habit.name is not None:
        existing_habit.name = habit.name
    if habit.star is not None:
        existing_habit.star = habit.star
    if habit.status is not None:
        existing_habit.status = habit.status
    if habit.period is not None:
        existing_habit.period = HabitFrequency(
            target_count=habit.period.target_count,
            period_count=habit.period.period_count,
            period_type=habit.period.period_type,
        )
    if habit.tags is not None:
        existing_habit.tags = habit.tags

    return format_json_response(existing_habit)


@api_router.delete("/habits/{habit_id}", tags=["habits"])
async def delete_habit(
    habit_id: str,
    user: User = Depends(current_active_user),
):
    habit = await views.get_user_habit(user, habit_id)
    await views.remove_user_habit(user, habit)
    return format_json_response(habit)


@api_router.get("/habits/{habit_id}/completions", tags=["habits"])
async def get_habit_completions(
    habit_id: str,
    status: str | None = None,
    date_fmt: str = "%d-%m-%Y",
    date_start: str | None = None,
    date_end: str | None = None,
    limit: int | None = 10,
    sort="asc",
    user: User = Depends(current_active_user),
):
    # Parse date range
    start, end = datetime.date.min, datetime.date.max
    if date_start:
        try:
            start = datetime.datetime.strptime(date_start, date_fmt.strip()).date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format")
    if date_end:
        try:
            end = datetime.datetime.strptime(date_end, date_fmt.strip()).date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format")
    if start > end:
        raise HTTPException(
            status_code=400, detail="date_start cannot be after date_end"
        )

    # Parse status filter
    cstatus_list = [CStatus.DONE]
    if status:
        cstatus_list = []
        for s in status.split(","):
            try:
                cstatus_list.append(CStatus[s.strip().upper()])
            except KeyError:
                raise HTTPException(status_code=400, detail=f"Invalid status: {s}")

    habit = await views.get_user_habit(user, habit_id)
    status_map = get_habit_date_completion(habit, start, end)
    ticked_days = [
        day
        for day, stat in status_map.items()
        if any(s in stat for s in cstatus_list) and start <= day <= end
    ]

    if sort not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="Invalid sort value")
    ticked_days = sorted(ticked_days, reverse=sort == "desc")

    # Cap limit to prevent DoS via unbounded queries
    MAX_LIMIT = 10000
    if limit is None or limit > MAX_LIMIT:
        limit = MAX_LIMIT
    ticked_days = ticked_days[:limit]

    return [x.strftime(date_fmt) for x in ticked_days]


class Tick(BaseModel):
    done: bool
    date: str
    text: str | None = None
    date_fmt: str = "%d-%m-%Y"


@api_router.post("/habits/{habit_id}/completions", tags=["habits"])
async def put_habit_completions(
    habit_id: str,
    tick: Tick,
    user: User = Depends(current_active_user),
):
    try:
        day = datetime.datetime.strptime(tick.date, tick.date_fmt.strip()).date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    habit = await views.get_user_habit(user, habit_id)
    text = "" if "text" in tick.model_fields_set and tick.text is None else tick.text
    await habit.tick(day, tick.done, text)
    return {"day": day.strftime(tick.date_fmt), "done": tick.done}


def format_json_response(habit: Habit) -> dict:
    return {
        "id": habit.id,
        "name": habit.name,
        "star": habit.star,
        "records": habit.records,
        "status": habit.status,
        "period": habit.period,
        "tags": habit.tags,
    }


# ---------------------------------------------------------------------------
# Realtime tick over WebSocket.
#
# Each device opens one authenticated socket (?token=<jwt|api_token>). A tick
# is persisted (reusing habit.tick) and fanned out to the user's OTHER sockets,
# which apply it directly -- the payload is self-contained, so no follow-up
# pull is needed. Single worker (gunicorn -w 1) => in-process broadcast, no
# external broker required.
# ---------------------------------------------------------------------------


def _websocket_tick_text(message: dict) -> str | None:
    text = message.get("text")
    return "" if "text" in message and text is None else text


async def _authenticate_ws(token: str | None) -> User | None:
    if not token:
        return None
    if user := await user_from_token(token):
        return user
    if user := await get_user_by_api_token(token):
        return user
    return None


async def _apply_push_habit_list(user: User, msg: dict) -> None:
    """Merge incoming habit metadata into the stored list, preserving all records."""
    habit_list = await views.get_user_habit_list(user)
    if habit_list is None:
        return

    incoming_habits: list[dict] = msg.get("habits", [])
    incoming_by_id = {h["id"]: h for h in incoming_habits if "id" in h}

    # Update metadata for existing habits; add new ones.
    existing_ids = {str(h.id) for h in habit_list.habits}
    for h in habit_list.habits:
        if (incoming := incoming_by_id.get(str(h.id))) is None:
            continue
        if "name" in incoming:
            h.name = incoming["name"]
        if "star" in incoming:
            h.star = incoming["star"]
        if "status" in incoming:
            from castor.storage.storage import HabitStatus as _HS

            try:
                h.status = _HS(incoming["status"])
            except ValueError:
                pass
        if "period" in incoming:
            from castor.storage.storage import HabitFrequency as _HF

            p = incoming["period"]
            h.period = _HF.from_dict(p) if p else None
        if "tags" in incoming:
            h.tags = incoming["tags"]
        if "reminders" in incoming:
            h.data["reminders"] = incoming["reminders"]

    for habit_id, incoming in incoming_by_id.items():
        if habit_id not in existing_ids:
            await habit_list.add(incoming.get("name", ""), tags=incoming.get("tags"))
            # Set id to client-generated value.
            for h in habit_list.habits:
                if h.name == incoming.get("name") and str(h.id) != habit_id:
                    h.id = habit_id
                    break

    if "order" in msg:
        habit_list.order = msg["order"]
    if "order_by" in msg:
        from castor.storage.storage import HabitOrder as _HO

        try:
            habit_list.order_by = _HO(msg["order_by"])
        except (ValueError, KeyError):
            pass


@api_router.websocket("/sync/ws")
async def sync_ws(websocket: WebSocket, token: str | None = Query(default=None)):
    ip = websocket.client.host if websocket.client else "unknown"
    if not await consume("api-ip", ip, settings.API_RATE_IP_PER_MINUTE, 60):
        await websocket.close(code=1008)
        return
    user = await _authenticate_ws(token)
    if user is None:
        await websocket.close(code=1008)  # policy violation
        return

    user_id = str(user.id)
    await manager.connect(user_id, websocket)
    try:
        while True:
            msg = await websocket.receive_json()
            if not await consume("api-user", user.id, settings.API_RATE_USER_PER_MINUTE, 60):
                await websocket.close(code=1008)
                break
            msg_type = msg.get("type")

            if msg_type == "push_tick":
                logger.info(
                    f"[ws] received push_tick user={user_id} "
                    f"request={msg.get('request_id')} habit={msg.get('habit_id')} "
                    f"day={msg.get('day')}"
                )
                try:
                    day = datetime.datetime.strptime(msg["day"], "%Y-%m-%d").date()
                    habit = await views.get_user_habit(user, msg["habit_id"])
                    text = _websocket_tick_text(msg)
                    record = await habit.tick(day, bool(msg.get("done", False)), text)
                    await websocket.send_json(
                        {
                            "type": "tick_ack",
                            "request_id": msg["request_id"],
                            "timestamp": record.timestamp,
                        }
                    )
                    # F2: the HTTP durability middleware only covers HTTP
                    # requests, so the WebSocket path would otherwise rely on
                    # the 50ms debounce alone — and a tick acked here could
                    # still be lost to a restart in that window. Flush before
                    # acknowledging, so the ack means it is on disk.
                    await get_user_dict_storage().flush_all()
                except Exception as e:
                    # F15: this used to `logger.warning` and continue. The
                    # client had already sent a request_id and was waiting for
                    # a tick_ack; on failure it got nothing, waited, and
                    # concluded the tick had worked. A partial write with no
                    # detection on either side.
                    #
                    # An explicit error frame closes that loop: the client can
                    # distinguish "not applied" from "in flight". `raise` is
                    # deliberately NOT used here — one malformed message must
                    # not tear down an otherwise healthy connection — so the
                    # handler owns the failure and reports it.
                    logger.exception(
                        f"[ws] failed to tick habit for user {user_id}: {e}"
                    )
                    await websocket.send_json(
                        {
                            "type": "tick_error",
                            "request_id": msg.get("request_id"),
                            "error": "Could not save the tick. Please retry.",
                        }
                    )

            elif msg_type == "push_habit_list":
                logger.info(
                    f"[ws] received push_habit_list user={user_id} "
                    f"request={msg.get('request_id')} "
                    f"habits={len(msg.get('habits', []))}"
                )
                try:
                    await _apply_push_habit_list(user, msg)
                    timestamp = int(
                        datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000
                    )
                    await websocket.send_json(
                        {
                            "type": "habit_list_ack",
                            "request_id": msg["request_id"],
                            "timestamp": timestamp,
                        }
                    )
                    payload = {
                        k: v for k, v in msg.items() if k not in ("type", "request_id")
                    }
                    publish(HabitListChanged(user_id=user_id, payload=payload))
                except Exception as e:
                    # F15: same defect as push_tick above — a silent failure
                    # left the client believing its habit list had synced.
                    logger.exception(
                        f"[ws] failed to apply habit list for user {user_id}: {e}"
                    )
                    await websocket.send_json(
                        {
                            "type": "habit_list_error",
                            "request_id": msg.get("request_id"),
                            "error": "Could not save your habits. Please retry.",
                        }
                    )

    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(user_id, websocket)


def init_api_routes(app: FastAPI) -> None:
    app.include_router(api_router, prefix="/api/v1")


# ---------------------------------------------------------------------------
# API token management.
#
# Tokens are created by castor and used by mobile/native clients to call
# /api/v1/habits and /sync/ws. Stored as hashes in user_api_tokens; the raw
# token is returned ONLY on create/reset, never on read.
# ---------------------------------------------------------------------------


@api_router.get("/tokens", tags=["tokens"])
async def get_token(user: User = Depends(current_active_user)):
    """Return the user's masked API token (or null if not yet created)."""
    return {"token": await get_user_api_token(user)}


@api_router.post("/tokens", status_code=201, tags=["tokens"])
async def create_token(user: User = Depends(current_active_user)):
    """Create a new API token. Returns the raw token — the user must copy
    it now; subsequent GETs return only the masked form."""
    raw = await create_user_api_token(user)
    return {"token": raw}


@api_router.post("/tokens/rotate", tags=["tokens"])
async def rotate_token(user: User = Depends(current_active_user)):
    """Rotate the API token. The previous token is invalidated immediately.
    Returns the new raw token."""
    raw = await reset_user_api_token(user)
    return {"token": raw}


@api_router.delete("/tokens", status_code=204, tags=["tokens"])
async def revoke_token(user: User = Depends(current_active_user)) -> Response:
    """Revoke the API token. Subsequent calls with the old token 401."""
    await delete_user_api_token(user)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# User-configs (per-user preferences).
#
# Stored as a JSON blob on `user_configs.config_data`. The migration branch
# was previously writing/reading custom_css only via localStorage on the
# client (web/concepts/src/components/SettingsClient.tsx). This exposes a
# real backend so the custom CSS textarea on /settings persists across
# devices, browsers, and sessions. Schema is a flat dict for v1 — schema
# versioning is a Phase 4 concern.
#
# Sanitisation: keys whose values are CSS strings get run through
# css_sanitizer.sanitize_css() before persistence. Sanitiser returns ""
# for any rejected input (the allowlist blocks URL(), @import, @font-face,
# animation, transforms, custom properties, attr(), and every at-rule
# other than @media). We surface that as 422 with the raw text echoed back
# so the UI can highlight which input was rejected.
# ---------------------------------------------------------------------------


class UserConfigsUpdate(BaseModel):
    """PUT body for /api/v1/user-configs. Fields are optional and merged.

    All keys are best-effort: missing keys leave the stored value
    untouched. Boolean fields (`show_streak`, `show_total`,
    `date_reverse`) are validated by Pydantic — strict booleans only.
    `custom_css` runs through castor.css_sanitizer.sanitize_css().
    """

    custom_css: str | None = None
    show_streak: StrictBool | None = None
    show_total: StrictBool | None = None
    date_reverse: StrictBool | None = None


@api_router.get("/user-configs", tags=["user-configs"])
async def get_user_configs_route(
    user: User = Depends(current_active_user),
) -> dict:
    """Return the user's persisted config dict (empty dict if none yet)."""
    return await get_user_configs(user) or {}


@api_router.put("/user-configs", tags=["user-configs"])
async def put_user_configs_route(
    payload: UserConfigsUpdate,
    user: User = Depends(current_active_user),
) -> dict:
    """Merge the supplied keys into user_configs.config_data.

    Setting ``custom_css`` to an empty string is permitted and clears the
    stored value. A non-empty value must pass css_sanitizer.sanitize_css();
    rejected input returns 422 and does not mutate the row.
    """
    diff: dict = {}

    if payload.custom_css is not None:
        if not isinstance(payload.custom_css, str):
            raise HTTPException(status_code=422, detail="custom_css must be a string")
        if payload.custom_css:
            sanitized = sanitize_css(payload.custom_css)
            if not sanitized:
                raise HTTPException(
                    status_code=422,
                    detail="custom_css rejected by sanitiser (unsupported rules)",
                )
            diff["custom_css"] = sanitized
        else:
            diff["custom_css"] = ""

    # Display preferences (parity row 12.6). Pydantic StrictBool
    # rejects 0/1/"yes"/"no" coercion — anything that isn't a real JSON
    # boolean fails validation upstream with a 422 we never reach here.
    for bool_key in ("show_streak", "show_total", "date_reverse"):
        value = getattr(payload, bool_key)
        if value is not None:
            diff[bool_key] = value

    if not diff:
        # Nothing to change; return current state without rewriting the row.
        return await get_user_configs(user) or {}

    await update_user_configs(user, diff)
    return await get_user_configs(user) or {}
