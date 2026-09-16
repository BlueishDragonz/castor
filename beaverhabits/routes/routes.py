import io
import re
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from nicegui import Client, app, ui

from beaverhabits import const, views
from beaverhabits.app.auth import (
    user_authenticate,
    user_get_by_email,
)
from beaverhabits.app.crud import get_user_count
from beaverhabits.app.db import User
from beaverhabits.app.dependencies import (
    current_active_user,
    current_admin_user,
    get_reset_user,
)
from beaverhabits.configs import settings
from beaverhabits.frontend import paddle_page
from beaverhabits.frontend.add_page import add_page_ui
from beaverhabits.frontend.admin import admin_page
from beaverhabits.frontend.components import (
    auth_card,
    auth_email,
    auth_forgot_password,
    auth_password,
    auth_redirect,
    webauthn_login_button,
    webauthn_register_button,
)
from beaverhabits.frontend.export_page import export_page
from beaverhabits.frontend.habit_page import habit_page_ui
from beaverhabits.frontend.import_page import import_ui_page
from beaverhabits.frontend.index_page import (
    index_page_ui,
    refresh_habit_list_when_today_changes,
)
from beaverhabits.frontend.layout import custom_headers, redirect
from beaverhabits.frontend.chip_sets_page import chip_sets_page
from beaverhabits.frontend.order_page import order_page_ui
from beaverhabits.frontend.security_page import security_page
from beaverhabits.frontend.settings_page import settings_page
from beaverhabits.frontend.stats_page import stats_page_ui
from beaverhabits.frontend.streaks import heatmap_page
from beaverhabits.frontend.tokens_page import tokens_page
from beaverhabits.logger import logger
from beaverhabits.routes.google_one_tap import google_one_tap_login
from beaverhabits.storage import image_storage
from beaverhabits.storage.meta import GUI_ROOT_PATH
from beaverhabits.utils import dummy_days, fetch_user_dark_mode, get_user_today_date

UNRESTRICTED_PAGE_ROUTES = ("/login", "/register")


# Email validation regex - matches standard email format
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def normalize_email(email: str) -> str:
    """Normalize email by stripping whitespace and zero-width characters."""
    # Remove zero-width characters (U+200B, U+200C, U+200D, U+FEFF, etc.)
    zero_width_chars = re.compile(r'[\u200b-\u200f\ufeff]')
    return zero_width_chars.sub('', (email or '').strip())


def is_valid_email(email: str) -> bool:
    """Validate email format."""
    normalized = normalize_email(email)
    return bool(EMAIL_REGEX.match(normalized))


async def _check_user_has_passkeys(user: User) -> bool:
    """Check if user has any registered passkeys."""
    from beaverhabits.app.users import get_user_manager
    from beaverhabits.app.auth import get_async_session_context, get_user_db_context, get_user_manager_context
    
    async with get_async_session_context() as session:
        async with get_user_db_context(session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                credentials = await user_manager.get_webauthn_credentials(user)
                return len(credentials) > 0


@ui.page("/demo")
async def demo_index_page() -> None:
    days = await dummy_days(settings.INDEX_HABIT_DATE_COLUMNS)
    habit_list = views.get_or_create_session_habit_list(days)
    index_page_ui(days, habit_list)
    refresh_habit_list_when_today_changes(days, habit_list)
    google_one_tap_login()


@ui.page("/demo/add")
async def demo_add_page() -> None:
    days = await dummy_days(settings.INDEX_HABIT_DATE_COLUMNS)
    habit_list = views.get_or_create_session_habit_list(days)
    add_page_ui(habit_list)


@ui.page("/demo/stats")
async def demo_stats_page() -> None:
    days = await dummy_days(settings.INDEX_HABIT_DATE_COLUMNS)
    habit_list = views.get_or_create_session_habit_list(days)
    today = await get_user_today_date()
    stats_page_ui(today, habit_list)


@ui.page("/demo/order")
async def demo_order_page() -> None:
    days = await dummy_days(settings.INDEX_HABIT_DATE_COLUMNS)
    habit_list = views.get_or_create_session_habit_list(days)
    order_page_ui(habit_list)


@ui.page("/demo/habits/{habit_id}")
async def demo_habit_page(habit_id: str) -> None:
    today = await get_user_today_date()
    habit = await views.get_session_habit(habit_id)
    if habit is None:
        redirect("")
        return
    habit_page_ui(today, habit)


@ui.page("/demo/habits/{habit_id}/streak")
@ui.page("/demo/habits/{habit_id}/heatmap")
async def demo_habit_page_heatmap(habit_id: str) -> None:
    today = await get_user_today_date()
    habit = await views.get_session_habit(habit_id)
    if habit is None:
        redirect("")
        return
    heatmap_page(today, habit)


@ui.page("/demo/completion-status")
async def demo_chip_sets() -> None:
    await chip_sets_page()


@ui.page("/demo/export")
async def demo_export() -> None:
    habit_list = views.get_session_habit_list()
    if not habit_list:
        ui.notify("No habits to export", color="negative")
        return
    await views.export_user_habit_list(habit_list, "demo")


@ui.page("/gui")
@ui.page("/")
async def index_page(
    user: User = Depends(current_active_user),
) -> None:
    days = await dummy_days(settings.INDEX_HABIT_DATE_COLUMNS)
    habit_list = await views.get_user_habit_list(user)
    index_page_ui(days, habit_list)
    refresh_habit_list_when_today_changes(days, habit_list)
    await views.set_user_cookies(user)


@ui.page("/gui/add")
async def add_page(user: User = Depends(current_active_user)) -> None:
    habit_list = await views.get_user_habit_list(user)
    add_page_ui(habit_list)


@ui.page("/gui/stats")
async def stats_page(user: User = Depends(current_active_user)) -> None:
    habit_list = await views.get_user_habit_list(user)
    today = await get_user_today_date()
    stats_page_ui(today, habit_list)


@ui.page("/gui/order")
async def order_page(user: User = Depends(current_active_user)) -> None:
    habit_list = await views.get_user_habit_list(user)
    order_page_ui(habit_list)


@ui.page("/gui/habits/{habit_id}")
async def habit_page(habit_id: str, user: User = Depends(current_active_user)) -> None:
    today = await get_user_today_date()
    habit = await views.get_user_habit(user, habit_id)
    habit_page_ui(today, habit)


@ui.page("/gui/habits/{habit_id}/streak")
@ui.page("/gui/habits/{habit_id}/heatmap")
async def gui_habit_page_heatmap(
    habit_id: str, user: User = Depends(current_active_user)
) -> None:
    habit = await views.get_user_habit(user, habit_id)
    today = await get_user_today_date()
    heatmap_page(today, habit)


@ui.page("/gui/export")
async def gui_export(user: User = Depends(current_active_user)) -> None:
    habit_list = await views.get_user_habit_list(user)
    if not habit_list:
        ui.notify("No habits to export", color="negative")
        return
    await export_page(habit_list, user)


@ui.page("/gui/import")
async def gui_import(user: User = Depends(current_active_user)) -> None:
    import_ui_page(user)


@ui.page("/settings")
@ui.page("/gui/settings")
async def gui_settings(user: User = Depends(current_active_user)) -> None:
    await settings_page(user)


@ui.page("/gui/tokens")
async def gui_tokens(user: User = Depends(current_active_user)) -> None:
    await tokens_page(user)


@ui.page("/gui/security")
async def gui_security(user: User = Depends(current_active_user)) -> None:
    await security_page(user)


@ui.page("/gui/completion-status")
async def gui_chip_sets(user: User = Depends(current_active_user)) -> None:
    await chip_sets_page(user)


async def _check_user_has_passkeys(user: User) -> bool:
    """Check if user has any registered passkeys."""
    from beaverhabits.app.users import get_user_manager
    from beaverhabits.app.auth import get_async_session_context, get_user_db_context, get_user_manager_context
    
    async with get_async_session_context() as session:
        async with get_user_db_context(session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                credentials = await user_manager.get_webauthn_credentials(user)
                return len(credentials) > 0


@ui.page("/login")
async def login_page(client: Client) -> Optional[RedirectResponse]:
    if await views.is_gui_authenticated():
        return RedirectResponse(GUI_ROOT_PATH)

    custom_headers()
    google_one_tap_login()

    from beaverhabits.frontend.components import add_webauthn_javascript
    add_webauthn_javascript()

    # Soft fade-slide whenever step-2 content rebuilds (mode toggles, etc).
    ui.add_css(
        "@keyframes bhFadeSlide {"
        " from { opacity: 0; transform: translateY(6px); }"
        " to { opacity: 1; transform: none; } }"
        " .bh-anim > * { animation: bhFadeSlide .28s ease; }"
    )

    # Animated dot-grid background (Vanta.js, vendored locally), palette-matched
    # to the app theme: light #f9f9f9 bg + #1976d2 dots, dark #121212 + #6796cf.
    ui.add_head_html(
        '<script src="/statics/libs/three.r134.min.js"></script>'
        '<script src="/statics/libs/vanta.dots.min.js"></script>'
    )
    ui.add_body_html(
        """
        <datalist id="bh-email-history"></datalist>
        <script>
        // Remember used emails per browser (localStorage) and offer them as
        // you type, across tabs and visits. Max 5, most-recent first.
        (function () {
            var STORE_KEY = "bh_emails";
            var EMAIL_RE = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$/;
            function load() {
                try { return JSON.parse(localStorage.getItem(STORE_KEY) || "[]"); }
                catch (e) { return []; }
            }
            function fill(input) {
                var dl = document.getElementById("bh-email-history");
                if (!dl) return;
                dl.innerHTML = "";
                load().forEach(function (em) {
                    var o = document.createElement("option");
                    o.value = em;
                    dl.appendChild(o);
                });
                input.setAttribute("list", "bh-email-history");
                input.setAttribute("autocomplete", "email");
            }
            function remember(email) {
                email = (email || "").trim().toLowerCase();
                if (!EMAIL_RE.test(email)) return;
                var list = load().filter(function (e) { return e !== email; });
                list.unshift(email);
                try { localStorage.setItem(STORE_KEY, JSON.stringify(list.slice(0, 5))); }
                catch (e) {}
            }
            var tries = 0;
            (function hook() {
                var input = document.querySelector('input[placeholder="Enter your email"]');
                if (!input) {
                    if (++tries < 25) setTimeout(hook, 300);
                    return;
                }
                fill(input);
                input.addEventListener("blur", function () {
                    remember(input.value);
                    fill(input);
                });
                input.addEventListener("keydown", function (ev) {
                    if (ev.key === "Enter") {
                        remember(input.value);
                        fill(input);
                    }
                });
            })();
        })();
        </script>
        <div id="vanta-bg" style="position:fixed;inset:0;z-index:0;pointer-events:none;"></div>
        <script>
        (function () {
            if (window._vantaBg) { window._vantaBg.destroy(); window._vantaBg = null; }
            function initVanta() {
                if (typeof VANTA === "undefined" || !VANTA.DOTS) return;
                var dark = document.body.classList.contains("q-dark")
                    || document.body.classList.contains("body--dark")
                    || (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches);
                window._vantaBg = VANTA.DOTS({
                    el: "#vanta-bg",
                    mouseControls: true,
                    touchControls: true,
                    gyroControls: false,
                    minHeight: 200.00,
                    minWidth: 200.00,
                    scale: 1.00,
                    scaleMobile: 1.00,
                    showLines: false,
                    backgroundColor: dark ? 0x121212 : 0xf9f9f9,
                    color: dark ? 0x6796cf : 0x1976d2,
                    size: 2.5,
                    spacing: 30.0
                });
            }
            if (document.readyState === "complete") initVanta();
            else window.addEventListener("load", initVanta);
        })();
        </script>
        """
    )

    try:
        await client.connected(timeout=3)
    except TimeoutError:
        logger.warning("Client not connected, skipping...")

    # Per-page mutable state. A dict avoids nonlocal/global aliasing bugs.
    state = {"user": None, "has_passkeys": False, "pending_user": None,
             "email_locked": False, "show_password": False}
    refs = {"email": None, "password": None, "confirm": None, "dynamic": None,
            "back": None, "continue": None}

    def current_email() -> str:
        email_el = refs["email"]
        raw = email_el.value if email_el is not None else None
        return normalize_email(raw or "")

    def show_email_error(msg: str) -> None:
        email_el = refs["email"]
        if email_el is None:
            return
        if msg:
            email_el.props(f'error="{msg}"')
        else:
            email_el.props(remove="error")

    def render() -> None:
        """Rebuild the section below the email field from current state."""
        dyn = refs["dynamic"]
        if dyn is None:
            return
        dyn.clear()
        refs["password"] = None
        refs["confirm"] = None
        email_val = current_email()
        # Step 2 (valid email committed): lock the field, show the back arrow.
        locked = state["email_locked"]
        email_el = refs["email"]
        if email_el is not None:
            if locked:
                email_el.props("disable")
            else:
                email_el.props(remove="disable")
        back_btn = refs.get("back")
        if back_btn is not None:
            back_btn.set_visibility(locked)

        def branch_continue() -> None:
            """Per-branch Continue button, placed after the branch content."""
            refs["continue"] = (
                ui.button("Continue", on_click=do_submit)
                .props("dense")
                .classes("w-full")
            )

        with dyn:
            pending = state["pending_user"]
            user = state["user"]
            if pending is not None:
                # Just authenticated with password, no passkeys yet: prompt.
                ui.label("Create a passkey for faster sign-in next time:").classes(
                    "text-center text-gray-500"
                )
                webauthn_register_button(pending.email)
                ui.button("Skip for now", on_click=finish_pending_login).props(
                    "flat dense"
                ).classes("w-full")
                branch_continue()
            elif user is not None and state["has_passkeys"]:
                if not state["show_password"]:
                    # Passkey mode: primary CTA + quiet switch link.
                    with ui.button(
                        "Login with Passkey",
                        icon="fingerprint",
                        on_click=lambda: ui.run_javascript(
                            f'authenticateWithWebAuthn("{user.email}")'
                        ),
                    ).classes("w-full") as pk_btn:
                        pk_btn.tooltip("Sign in with a registered passkey")
                    ui.button("Use password", on_click=show_password_mode).props(
                        "flat dense no-caps"
                    ).classes("w-full text-gray-500")
                    refs["continue"] = None
                else:
                    # Password mode: field + forgot left / use-passkey right.
                    refs["password"] = auth_password().on("keydown.enter", do_submit)
                    refs["password"].props("autofocus")
                    with ui.row().classes("w-full items-center"):
                        auth_forgot_password(refs["email"], views.forgot_password)
                        ui.space()
                        ui.button("Use passkey", on_click=show_passkey_mode).props(
                            "flat dense no-caps"
                        ).classes("text-gray-500")
                    branch_continue()
            elif user is not None:
                refs["password"] = auth_password().on("keydown.enter", do_submit)
                refs["password"].props("autofocus")
                with ui.row().classes("gap-2 w-full items-center"):
                    auth_forgot_password(refs["email"], views.forgot_password)
                    ui.space()
                branch_continue()
            elif email_val and state["email_locked"]:
                # Valid email committed, unknown: inline account creation.
                ui.label("No account for this email yet. Create one:").classes(
                    "text-center text-gray-500"
                )
                refs["password"] = auth_password("Create a password").on(
                    "keydown.enter", do_submit
                )
                refs["confirm"] = auth_password("Confirm password").on(
                    "keydown.enter", do_submit
                )
                branch_continue()
            else:
                # Step 1: Continue first, account link beneath it.
                branch_continue()
                with ui.row().classes("gap-2 w-full items-center justify-center"):
                    ui.label("New here?").classes("text-gray-500")
                    auth_redirect("Create account", "/register")

    async def check_email() -> None:
        """Validate format on blur/enter; only hit the DB when format is valid."""
        val = current_email()
        if not val:
            state["user"] = None
            state["has_passkeys"] = False
            state["email_locked"] = False
            show_email_error("")
            render()
            return
        if not is_valid_email(val):
            state["user"] = None
            state["has_passkeys"] = False
            state["email_locked"] = False
            show_email_error("Enter a valid email address")
            render()
            return
        show_email_error("")
        try:
            user = await user_get_by_email(val)
        except Exception:
            logger.exception("Email lookup failed")
            ui.notify("Could not check this email, try again", color="negative")
            return
        state["user"] = user
        if user is not None:
            try:
                state["has_passkeys"] = await _check_user_has_passkeys(user)
            except Exception:
                logger.exception("Passkey lookup failed")
                state["has_passkeys"] = False
        else:
            state["has_passkeys"] = False
        state["email_locked"] = True
        state["show_password"] = False
        render()

    async def finish_pending_login() -> None:
        pending = state["pending_user"]
        if pending is None:
            return
        state["pending_user"] = None
        await views.login_user(pending)
        ui.navigate.to(GUI_ROOT_PATH)

    async def do_submit() -> None:
        val = current_email()
        if not val:
            show_email_error("Email is required")
            return
        if not is_valid_email(val):
            show_email_error("Enter a valid email address")
            return
        # Reconcile state in case blur never fired (e.g. autofill + Enter).
        user = state["user"]
        if user is None or user.email != val:
            try:
                user = await user_get_by_email(val)
            except Exception:
                logger.exception("Email lookup failed")
                ui.notify("Could not check this email, try again", color="negative")
                return
            state["user"] = user
            state["has_passkeys"] = (
                await _check_user_has_passkeys(user) if user is not None else False
            )
            state["email_locked"] = True
            state["show_password"] = False
            # Capture password BEFORE render() recreates the field
            pw_value = refs["password"].value if refs.get("password") else None
            confirm_value = refs["confirm"].value if refs.get("confirm") else None
            render()
            user = state["user"]
            # Restore values to new fields
            if pw_value and refs.get("password"):
                refs["password"].value = pw_value
            if confirm_value and refs.get("confirm"):
                refs["confirm"].value = confirm_value

        pw_el = refs["password"]
        if user is not None:
            if pw_el is None or not pw_el.value:
                ui.notify("Enter your password, or use your passkey", color="warning")
                return
            logger.info(f"Trying to login with {val}")
            authed = await user_authenticate(email=val, password=pw_el.value)
            if authed is None:
                ui.notify("email or password wrong!", color="negative")
                return
            if not state["has_passkeys"]:
                state["pending_user"] = authed
                render()
                return
            await views.login_user(authed)
            ui.navigate.to(GUI_ROOT_PATH)
        else:
            if pw_el is None or not pw_el.value:
                ui.notify("Choose a password to create your account", color="warning")
                return
            confirm_el = refs["confirm"]
            if confirm_el is not None and pw_el.value != confirm_el.value:
                ui.notify("Passwords do not match", color="negative")
                return
            try:
                await views.validate_max_user_count()
                new_user = await views.register_user(email=val, password=pw_el.value)
                await views.login_user(new_user)
            except Exception as e:
                ui.notify(str(e), color="negative")
            else:
                ui.navigate.to(GUI_ROOT_PATH)

    def show_password_mode() -> None:
        state["show_password"] = True
        render()

    def show_passkey_mode() -> None:
        state["show_password"] = False
        render()

    def go_back() -> None:
        """Return to step 1: unlock the email field, keep the typed value."""
        state["user"] = None
        state["has_passkeys"] = False
        state["pending_user"] = None
        state["email_locked"] = False
        state["show_password"] = False
        show_email_error("")
        render()
        email_el = refs["email"]
        if email_el is not None:
            email_el.run_method("focus")

    with auth_card(title="Sign in", func=do_submit, logo=True,
                   on_back=go_back, show_continue=False) as slot:
        refs["back"] = slot["back"]
        refs["email"] = (
            auth_email().on("blur", check_email).on("keydown.enter", check_email)
        )
        refs["email"].props('placeholder="Enter your email" autocomplete="email"')
        refs["email"].props("autofocus")
        refs["dynamic"] = ui.column().classes("w-full gap-2 bh-anim")
        render()

    with ui.element("div").style(
        "position:fixed;right:24px;bottom:20px;text-align:right;pointer-events:none;"
    ):
        ui.label("beaverhabits").style(
            "font-family:Inter,sans-serif;font-weight:700;font-size:18px;"
            "letter-spacing:4px;color:#888;margin:0;"
        )


@ui.page("/register")
async def register_page():
    if await views.is_gui_authenticated():
        return RedirectResponse(GUI_ROOT_PATH)

    custom_headers()
    google_one_tap_login()

    async def try_register():
        if not email.value:
            ui.notify("Email is required", color="negative")
            return
        val = normalize_email((email.value or ""))
        if not is_valid_email(val):
            ui.notify("Enter a valid email address", color="negative")
            return
        if not password1.value or not password2.value or password1.value != password2.value:
            ui.notify("Passwords do not match", color="negative")
            return

        try:
            await views.validate_max_user_count()
            user = await views.register_user(email=val, password=password2.value)
            await views.login_user(user)
        except Exception as e:
            ui.notify(str(e), color="negative")
        else:
            ui.navigate.to(GUI_ROOT_PATH)

    await views.validate_max_user_count()

    with auth_card(title="Sign up", func=try_register):
        email = auth_email().props('autofocus placeholder="Enter your email"')
        password1 = auth_password().on("keydown.enter", try_register)
        password2 = auth_password("Confirm password").on("keydown.enter", try_register)

        with ui.row().classes("gap-2 w-full items-center"):
            auth_forgot_password(email, views.forgot_password)
            ui.space()
            auth_redirect("Sign in", "/login")


@ui.page("/reset-password")
async def forgot_password_page(user: User = Depends(get_reset_user)):
    custom_headers()

    async def try_reset():
        if not password1.value or not password2.value:
            ui.notify("Password is required", color="negative")
            return
        if password1.value != password2.value:
            ui.notify("Passwords do not match", color="negative")
            return

        logger.info(f"Trying to reset password for {user.email}")
        await views.reset_password(user, password1.value)

    with auth_card(title="Reset password", func=try_reset):
        auth_email(user.email).disable()
        password1 = auth_password("New password")
        password2 = auth_password("Confirm password")


@app.post("/assets")
async def upload_note_image(
    file: Annotated[bytes, File()], user: User = Depends(current_active_user)
):
    if not file:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No file provided")
    return await image_storage.save(file, user)


@app.get("/assets/{image_id}")
async def get_note_image(image_id: str, user: User = Depends(current_active_user)):
    img = await image_storage.get(image_id, user)
    if not (img and img.blob):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    return StreamingResponse(io.BytesIO(img.blob), media_type="image/png")


def init_gui_routes(fastapi_app: FastAPI):
    def handle_exception(exception: Exception):
        if isinstance(exception, HTTPException):
            if exception.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                ui.notify(f"An error occurred: {exception}", type="negative")

    @app.middleware("http")
    async def AuthMiddleware(request: Request, call_next):
        logger.debug(f"AuthMiddleware: {request.url.path}")
        token = app.storage.user.get("auth_token") or request.cookies.get("beaver_auth")
        if token:
            request.scope["headers"] = [e for e in request.scope["headers"] if e[0] != b"authorization"]
            request.scope["headers"].append((b"authorization", f"Bearer {token}".encode()))
        response = await call_next(request)
        if response.status_code == 401:
            return RedirectResponse("/login")
        return response

    @app.middleware("http")
    async def StaticFilesCacheMiddleware(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/_nicegui/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    oneyear = 365 * 24 * 60 * 60
    app.add_static_files("/statics", "statics", max_cache_age=oneyear)
    app.on_exception(handle_exception)
    app.on_connect(fetch_user_dark_mode)

    ui.run_with(
        fastapi_app,
        title=const.PAGE_TITLE,
        storage_secret=settings.NICEGUI_STORAGE_SECRET,
        favicon="statics/images/favicon.svg",
        dark=settings.DARK_MODE,
        reconnect_timeout=10,
        viewport="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no",
    )