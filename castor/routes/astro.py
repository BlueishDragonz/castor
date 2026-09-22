"""Landing-page + SEO static-file mount.

In P3-B we dropped the NiceGUI pages for /terms, /privacy, /admin,
and /admin/backup. The replacements land in subsequent commits:
  - /terms, /privacy: Astro static pages (P3-D)
  - /admin, /admin/backup: Astro admin page + API route (P3-E)

What's left here is the static file mount: the landing page built
out of statics/astro/dist/ is served by the FastAPI app so the
reverse proxy in front of both services can route to it. The
landing page is a separate Astro project (not the migration
target in web/concepts/).
"""
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from nicegui import app

from castor.logger import logger


def init_astro_routes(fastapi_app: FastAPI) -> None:
    """Mount Astro static files (landing page + SEO assets)."""
    ASTRO_DIST_PATH = "statics/astro/dist"

    @fastapi_app.get("/robots.txt")
    async def robots():
        return FileResponse("statics/robots.txt", media_type="text/plain")

    @fastapi_app.get("/sitemap.xml")
    async def sitemap():
        return FileResponse("statics/sitemap.xml", media_type="application/xml")

    if os.path.exists(ASTRO_DIST_PATH):
        logger.info(f"Mounting Astro static files from {ASTRO_DIST_PATH}")
        # Use NiceGUI's add_static_files to serve Astro static files
        app.add_static_files(
            "/_astro", "statics/astro/dist/_astro", max_cache_age=7 * 24 * 60 * 60
        )
        app.add_static_files(
            "/landing", "statics/astro/dist/landing", max_cache_age=7 * 24 * 60 * 60
        )

        # Add explicit routes for landing page
        @fastapi_app.get("/")
        @fastapi_app.get("/pricing")
        @fastapi_app.get("/pricing/")
        async def landing_index():
            return FileResponse("statics/astro/dist/index.html", media_type="text/html")

        @fastapi_app.get("/privacy/ios")
        @fastapi_app.get("/privacy/ios/")
        async def ios_privacy():
            return FileResponse("statics/astro/dist/privacy/ios/index.html", media_type="text/html")

    else:
        logger.warning(
            f"Astro dist path not found: {ASTRO_DIST_PATH}, skipping static file mount"
        )
