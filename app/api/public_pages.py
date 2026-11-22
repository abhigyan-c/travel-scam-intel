"""Fixed public product/policy pages; no dynamic user content or directory serving."""

import base64
import hashlib
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["product"])
ROOT = Path(__file__).parents[1] / "public"


def page(name):
    content = (ROOT / (name + ".html")).read_text(encoding="utf-8")
    style = content.split("<style>", 1)[1].split("</style>", 1)[0]
    digest = base64.b64encode(hashlib.sha256(style.encode()).digest()).decode()
    return HTMLResponse(
        content,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; "
            f"style-src 'sha256-{digest}'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'none'",
        },
    )


@router.api_route("/about", methods=["GET", "HEAD"], include_in_schema=False)
async def home():
    return page("index")


@router.api_route("/privacy", methods=["GET", "HEAD"], include_in_schema=False)
async def privacy():
    return page("privacy")


@router.api_route("/terms", methods=["GET", "HEAD"], include_in_schema=False)
async def terms():
    return page("terms")


@router.api_route("/support", methods=["GET", "HEAD"], include_in_schema=False)
async def support():
    return page("support")
