"""Northstar CU MEMSYS 4.2 — hostile stand-in core-servicing console.

Legacy-shaped on purpose: iframes, nested tables, no test IDs, no ARIA,
name-only form fields. Built so a computer-use system can exercise
lookup → detail → confirmation, plus deterministic runtime exceptions.
"""

from __future__ import annotations

import asyncio
import copy
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))


def render(name: str, ctx: dict[str, Any]) -> HTMLResponse:
    return templates.TemplateResponse(ctx["request"], name, ctx)

app = FastAPI(title="Northstar MEMSYS", docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("MEMSYS_SECRET", "northstar-lab-secret"),
    max_age=60 * 60,
)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

# --- seed data (resettable) -------------------------------------------------

PRODUCTS = {
    "02": "Secondary Shares",
    "03": "Vacation Club",
    "04": "Money Market Shares",
}


def _seed_members() -> dict[str, dict[str, Any]]:
    return {
        "12345": {
            "name": "HERNANDEZ, MARIA",
            "status": "Active",
            "branch": "001 Austin",
            "perm": "ok",
            "shares": [
                {
                    "id": "S00",
                    "type": "Regular Shares (Savings)",
                    "bal": 1204.55,
                    "avail": 1204.55,
                },
                {
                    "id": "S01",
                    "type": "Draft Checking",
                    "bal": 340.12,
                    "avail": 340.12,
                },
            ],
        },
        "12346": {
            "name": "CHEN, ROBERT",
            "status": "Active",
            "branch": "003 Plano",
            "perm": "ok",
            "shares": [
                {
                    "id": "S00",
                    "type": "Regular Shares (Savings)",
                    "bal": 8910.00,
                    "avail": 8910.00,
                }
            ],
        },
        "77777": {
            "name": "PATEL, AISHA",
            "status": "Active",
            "branch": "009 Restricted",
            "perm": "denied",
            "shares": [
                {
                    "id": "S00",
                    "type": "Regular Shares (Savings)",
                    "bal": 500.00,
                    "avail": 500.00,
                }
            ],
        },
        "88888": {
            "name": "WALKER, JAMES",
            "status": "Locked",
            "branch": "001 Austin",
            "perm": "ok",
            "shares": [
                {
                    "id": "S00",
                    "type": "Regular Shares (Savings)",
                    "bal": 12.00,
                    "avail": 0.00,
                }
            ],
        },
    }


MEMBERS: dict[str, dict[str, Any]] = _seed_members()
SEQ = {"n": 1000}


def money(v: float) -> str:
    return f"{v:,.2f}"


templates.env.globals["money"] = money


def _logged_in(request: Request) -> bool:
    return bool(request.session.get("op"))


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/signon", status_code=303)


def _ctx(request: Request, screen: str, inject: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "request": request,
        "screen": screen,
        "op": request.session.get("op", ""),
        "show_dialog": inject == "dialog",
        **extra,
    }


async def _apply_inject(request: Request) -> str | None:
    inject = request.query_params.get("inject")
    if inject == "slow":
        await asyncio.sleep(3)
    return inject


def _savings(member: dict[str, Any]) -> dict[str, Any] | None:
    for share in member["shares"]:
        if "Savings" in share["type"]:
            return share
    return member["shares"][0] if member["shares"] else None


# --- public ---------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if _logged_in(request):
        return RedirectResponse("/console", status_code=303)
    return RedirectResponse("/signon", status_code=303)


@app.get("/signon", response_class=HTMLResponse)
async def signon_get(request: Request):
    return render(
        "signon.html",
        {"request": request, "screen": "SON01", "err": request.query_params.get("err")},
    )


@app.post("/signon")
async def signon_post(
    request: Request,
    OPR: str = Form(""),
    PWD: str = Form(""),
):
    opr = (OPR or "").strip().upper()
    pwd = PWD or ""
    if opr == "JLEE" and pwd == "demo":
        request.session.clear()
        request.session["op"] = "JLEE"
        return RedirectResponse("/console", status_code=303)
    return RedirectResponse("/signon?err=REJECT", status_code=303)


@app.get("/signoff")
async def signoff(request: Request):
    request.session.clear()
    return RedirectResponse("/signon", status_code=303)


@app.get("/health")
async def health():
    return {"status": "ok", "app": "memsys"}


@app.get("/lab/reset")
async def lab_reset():
    global MEMBERS, SEQ
    MEMBERS = _seed_members()
    SEQ = {"n": 1000}
    return {"reset": True, "members": list(MEMBERS)}


# --- console chrome -------------------------------------------------------


@app.get("/console", response_class=HTMLResponse)
async def console(request: Request):
    if not _logged_in(request):
        return _login_redirect()
    return render("console.html", {"request": request, "op": request.session["op"]})


@app.get("/nav", response_class=HTMLResponse)
async def nav(request: Request):
    if not _logged_in(request):
        return _login_redirect()
    return render("nav.html", {"request": request, "op": request.session["op"]})


# --- inquiry --------------------------------------------------------------


@app.get("/main/inquiry", response_class=HTMLResponse)
async def inquiry_get(request: Request):
    if not _logged_in(request):
        return _login_redirect()
    inject = await _apply_inject(request)
    if inject == "timeout":
        return render("timeout.html", _ctx(request, "SES99", inject=inject))
    err = request.query_params.get("err")
    mem = request.query_params.get("mem", "")
    return render(
        "inquiry.html",
        _ctx(request, "INQ01", inject=inject, err=err, mem=mem),
    )


@app.post("/main/inquiry")
async def inquiry_post(request: Request, MEMID: str = Form("")):
    if not _logged_in(request):
        return _login_redirect()
    mem = (MEMID or "").strip()
    if not mem:
        return RedirectResponse("/main/inquiry?err=REQUIRED", status_code=303)
    if not mem.isdigit():
        return RedirectResponse(f"/main/inquiry?err=NUMERIC&mem={mem}", status_code=303)
    member = MEMBERS.get(mem)
    if member is None:
        return RedirectResponse(f"/main/inquiry?err=NOTFOUND&mem={mem}", status_code=303)
    if member["perm"] == "denied":
        return RedirectResponse(f"/main/member?mem={mem}", status_code=303)
    return RedirectResponse(f"/main/member?mem={mem}", status_code=303)


# --- member detail --------------------------------------------------------


@app.get("/main/member", response_class=HTMLResponse)
async def member_detail(request: Request):
    if not _logged_in(request):
        return _login_redirect()
    inject = await _apply_inject(request)
    if inject == "timeout":
        return render("timeout.html", _ctx(request, "SES99", inject=inject))
    mem = (request.query_params.get("mem") or "").strip()
    member = MEMBERS.get(mem)
    if member is None:
        return RedirectResponse(f"/main/inquiry?err=NOTFOUND&mem={mem}", status_code=303)
    if member["perm"] == "denied":
        return render(
            "denied.html", _ctx(request, "SEC01", inject=inject, mem=mem, member=member)
        )
    if member["status"] == "Locked":
        return render(
            "locked.html", _ctx(request, "LCK01", inject=inject, mem=mem, member=member)
        )
    return render(
        "member.html",
        _ctx(
            request,
            "MEM02",
            inject=inject,
            mem=mem,
            member=member,
            savings=_savings(member),
        ),
    )


# --- open sub-account -----------------------------------------------------


@app.get("/main/subacct", response_class=HTMLResponse)
async def subacct_get(request: Request):
    if not _logged_in(request):
        return _login_redirect()
    inject = await _apply_inject(request)
    if inject == "timeout":
        return render("timeout.html", _ctx(request, "SES99", inject=inject))
    mem = (request.query_params.get("mem") or "").strip()
    member = MEMBERS.get(mem)
    if not member or member["perm"] == "denied" or member["status"] == "Locked":
        return RedirectResponse("/main/inquiry?err=NOTFOUND", status_code=303)
    err = request.query_params.get("err")
    return render(
        "subacct_form.html",
        _ctx(
            request,
            "SUB03",
            inject=inject,
            mem=mem,
            member=member,
            products=PRODUCTS,
            err=err,
            form=request.session.get("draft") or {},
        ),
    )


@app.post("/main/subacct")
async def subacct_post(
    request: Request,
    MEMID: str = Form(""),
    PROD: str = Form(""),
    NICK: str = Form(""),
    AMT: str = Form(""),
    SRC: str = Form("S00"),
):
    if not _logged_in(request):
        return _login_redirect()
    mem = (MEMID or "").strip()
    member = MEMBERS.get(mem)
    if not member:
        return RedirectResponse("/main/inquiry?err=NOTFOUND", status_code=303)

    prod = (PROD or "").strip()
    nick = (NICK or "").strip()
    amt_raw = (AMT or "").strip().replace(",", "")
    src = (SRC or "S00").strip()

    draft = {"PROD": prod, "NICK": nick, "AMT": AMT, "SRC": src, "MEMID": mem}
    request.session["draft"] = draft

    if prod not in PRODUCTS:
        return RedirectResponse(f"/main/subacct?mem={mem}&err=PROD", status_code=303)
    try:
        amt = float(amt_raw)
    except ValueError:
        return RedirectResponse(f"/main/subacct?mem={mem}&err=AMT", status_code=303)
    if amt < 0:
        return RedirectResponse(f"/main/subacct?mem={mem}&err=AMT", status_code=303)

    source = next((s for s in member["shares"] if s["id"] == src), None)
    if source is None:
        return RedirectResponse(f"/main/subacct?mem={mem}&err=SRC", status_code=303)
    if amt > source["avail"]:
        return RedirectResponse(f"/main/subacct?mem={mem}&err=NSF", status_code=303)

    request.session["pending"] = {
        **draft,
        "amt": amt,
        "prod_name": PRODUCTS[prod],
        "src_type": source["type"],
    }
    return RedirectResponse(f"/main/subacct/confirm?mem={mem}", status_code=303)


@app.get("/main/subacct/confirm", response_class=HTMLResponse)
async def subacct_confirm_get(request: Request):
    if not _logged_in(request):
        return _login_redirect()
    inject = await _apply_inject(request)
    if inject == "timeout":
        return render("timeout.html", _ctx(request, "SES99", inject=inject))
    mem = (request.query_params.get("mem") or "").strip()
    pending = request.session.get("pending")
    member = MEMBERS.get(mem)
    if not pending or not member or pending.get("MEMID") != mem:
        return RedirectResponse(f"/main/subacct?mem={mem}", status_code=303)
    return render(
        "subacct_confirm.html",
        _ctx(
            request,
            "SUB04",
            inject=inject,
            mem=mem,
            member=member,
            pending=pending,
        ),
    )


@app.post("/main/subacct/confirm")
async def subacct_confirm_post(request: Request, MEMID: str = Form(""), ACT: str = Form("")):
    if not _logged_in(request):
        return _login_redirect()
    mem = (MEMID or "").strip()
    if ACT == "Cancel":
        request.session.pop("pending", None)
        return RedirectResponse(f"/main/member?mem={mem}", status_code=303)

    pending = request.session.get("pending")
    member = MEMBERS.get(mem)
    if not pending or not member or pending.get("MEMID") != mem:
        return RedirectResponse(f"/main/subacct?mem={mem}", status_code=303)

    amt = float(pending["amt"])
    src = next(s for s in member["shares"] if s["id"] == pending["SRC"])
    if amt > src["avail"]:
        return RedirectResponse(f"/main/subacct?mem={mem}&err=NSF", status_code=303)

    src["avail"] = round(src["avail"] - amt, 2)
    src["bal"] = round(src["bal"] - amt, 2)
    new_id = f"S{pending['PROD']}"
    # allow repeat opens by suffixing if id exists
    existing = {s["id"] for s in member["shares"]}
    share_id = new_id
    n = 1
    while share_id in existing:
        n += 1
        share_id = f"{new_id}{n}"
    member["shares"].append(
        {
            "id": share_id,
            "type": pending["prod_name"]
            + (f" ({pending['NICK']})" if pending.get("NICK") else ""),
            "bal": amt,
            "avail": amt,
        }
    )
    SEQ["n"] += 1
    conf = f"NSC-{datetime.now().strftime('%Y%m%d')}-{SEQ['n']}"
    posted = copy.deepcopy(pending)
    posted["conf"] = conf
    posted["share_id"] = share_id
    request.session["posted"] = posted
    request.session.pop("pending", None)
    request.session.pop("draft", None)
    return RedirectResponse(f"/main/subacct/done?mem={mem}", status_code=303)


@app.get("/main/subacct/done", response_class=HTMLResponse)
async def subacct_done(request: Request):
    if not _logged_in(request):
        return _login_redirect()
    mem = (request.query_params.get("mem") or "").strip()
    posted = request.session.get("posted")
    member = MEMBERS.get(mem)
    if not posted or not member:
        return RedirectResponse(f"/main/member?mem={mem}", status_code=303)
    return render(
        "subacct_done.html",
        _ctx(request, "SUB05", mem=mem, member=member, posted=posted),
    )


@app.get("/main/continue-session")
async def continue_session(request: Request):
    if not _logged_in(request):
        return _login_redirect()
    return RedirectResponse("/main/inquiry", status_code=303)
