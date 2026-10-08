"""Northwind internal ERP (mock): the system of record for payable invoices.

Persists to a real database (SQLite by default, Postgres via ERP_DATABASE_URL). The database enforces
UNIQUE(vendor_key, invoice_number) and UNIQUE(idempotency_key). Re-submitting identical data is an idempotent
success ("exists"); the same invoice id with different financial data is a 409 conflict, never silently accepted.
Failure injection (`/api/admin/chaos`): fail the next create with HTTP 500, or rename the form's submit button.
"""
from __future__ import annotations

import os
import re
import secrets
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

DB_URL = os.environ.get("ERP_DATABASE_URL", "sqlite:///runs/erp.db")
if DB_URL.startswith("sqlite:///"):
    Path(DB_URL.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
API_KEY = os.environ.get("ERP_API_KEY", "erp-dev-key")
USER, PASSWORD = os.environ.get("ERP_USER", "ops"), os.environ.get("ERP_PASS", "northwind-demo")

engine = create_engine(DB_URL, pool_pre_ping=True)
md = MetaData()
invoices = Table(
    "invoices", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("vendor_name", String(80), nullable=False),
    Column("vendor_key", String(80), nullable=False),
    Column("invoice_number", String(32), nullable=False),
    Column("invoice_date", String(10), nullable=False),
    Column("due_date", String(10), nullable=False),
    Column("amount_paise", BigInteger, nullable=False),
    Column("po_number", String(32)),
    Column("idempotency_key", String(64), unique=True),
    Column("created_by", String(40), nullable=False, default="ops"),
    Column("created_at", Float, nullable=False),
    UniqueConstraint("vendor_key", "invoice_number", name="uq_vendor_invoice"),
)
settings = Table("settings", md, Column("key", String(40), primary_key=True), Column("value", String(80)))
sessions = Table("sessions", md, Column("token", String(64), primary_key=True), Column("expires", Float))
md.create_all(engine)

SEED = [  # already entered before any demo run
    {"vendor_name": "Acme Corp", "invoice_number": "INV-1039", "invoice_date": "2026-09-12", "due_date": "2026-10-12",
     "amount_paise": 3800000, "po_number": "PO-5518"},
    {"vendor_name": "Beta Manufacturing", "invoice_number": "BM-2207", "invoice_date": "2026-09-30",
     "due_date": "2026-10-30", "amount_paise": 2840000, "po_number": "PO-6133"},
]

app = FastAPI(title="Northwind ERP (mock)")


def vkey(name: str) -> str:
    return " ".join(name.lower().split())


def row_dict(r: Any) -> dict:
    d = dict(r._mapping)
    d.pop("vendor_key", None)
    d["amount_inr"] = f"{d['amount_paise'] // 100}.{d['amount_paise'] % 100:02d}"
    return d


def get_setting(k: str, default: str = "") -> str:
    with engine.connect() as c:
        v = c.execute(select(settings.c.value).where(settings.c.key == k)).scalar()
    return default if v is None else v


def set_setting(k: str, v: Any) -> None:
    with engine.begin() as c:
        if c.execute(select(settings.c.key).where(settings.c.key == k)).first():
            c.execute(update(settings).where(settings.c.key == k).values(value=str(v)))
        else:
            c.execute(insert(settings).values(key=k, value=str(v)))


def reset() -> None:
    with engine.begin() as c:
        for t in (invoices, settings, sessions):
            c.execute(t.delete())
        for s in SEED:
            c.execute(insert(invoices).values(**s, vendor_key=vkey(str(s["vendor_name"])), created_by="seed",
                                              created_at=time.time(), idempotency_key=None))


with engine.connect() as _c:
    if not _c.execute(select(func.count()).select_from(invoices)).scalar():
        try:
            reset()
        except IntegrityError:
            pass  # another worker seeded first


# ------------------------------------------------------------------------------------------------ business logic


class Rejected(Exception):
    def __init__(self, status: int, outcome: str, message: str, record: dict | None = None):
        super().__init__(message)
        self.status, self.outcome, self.message, self.record = status, outcome, message, record


def parse(data: dict) -> dict:
    errors = []
    vendor = str(data.get("vendor_name", "")).strip()
    number = str(data.get("invoice_number", "")).strip()
    if not vendor:
        errors.append("Vendor name is required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_-]{1,30}", number):
        errors.append("Invoice number is missing or malformed")
    dates = {}
    for k, label in (("invoice_date", "Invoice date"), ("due_date", "Due date")):
        try:
            dates[k] = date.fromisoformat(str(data.get(k, "")).strip())
        except ValueError:
            errors.append(f"{label} must be YYYY-MM-DD")
    try:
        amt = Decimal(str(data.get("amount_inr", "")).replace(",", "").strip())
        if amt <= 0 or amt != amt.quantize(Decimal("0.01")):
            raise InvalidOperation
        paise = int(amt * 100)
    except InvalidOperation:
        errors.append("Amount must be a positive number with at most 2 decimals")
        paise = 0
    if len(dates) == 2 and dates["due_date"] < dates["invoice_date"]:
        errors.append("Due date cannot be before the invoice date")
    if errors:
        raise Rejected(422, "validation_error", "; ".join(errors))
    return {"vendor_name": vendor, "vendor_key": vkey(vendor), "invoice_number": number,
            "invoice_date": dates["invoice_date"].isoformat(), "due_date": dates["due_date"].isoformat(),
            "amount_paise": paise, "po_number": (str(data.get("po_number") or "").strip() or None),
            "idempotency_key": (str(data.get("idempotency_key") or "").strip() or None)}


FINANCIAL = ("invoice_date", "due_date", "amount_paise")


def existing_for(c: Any, rec: dict) -> Any:
    cond = (invoices.c.vendor_key == rec["vendor_key"]) & (invoices.c.invoice_number == rec["invoice_number"])
    if rec["idempotency_key"]:
        cond = or_(cond, invoices.c.idempotency_key == rec["idempotency_key"])
    return c.execute(select(invoices).where(cond)).first()


def same_or_conflict(old: Any, rec: dict) -> tuple[int, str, dict]:
    o = row_dict(old)
    diffs = [f"{k}: ERP has {o[k]}, submitted {rec[k]}" for k in FINANCIAL if str(o[k]) != str(rec[k])]
    if o["invoice_number"] != rec["invoice_number"] or vkey(o["vendor_name"]) != rec["vendor_key"]:
        diffs.append("idempotency key belongs to a different invoice")
    if diffs:
        raise Rejected(409, "conflict", f"Conflict with existing invoice #{o['id']}: {'; '.join(diffs)}", o)
    return 200, "exists", o


def create(data: dict, created_by: str = "ops") -> tuple[int, str, dict]:
    rec = parse(data)
    if int(get_setting("fail_next_create", "0")) > 0:  # injected outage: fails before any write
        set_setting("fail_next_create", int(get_setting("fail_next_create")) - 1)
        raise Rejected(500, "error", "Internal Server Error: ledger service unavailable")
    with engine.begin() as c:
        old = existing_for(c, rec)
        if old:
            return same_or_conflict(old, rec)
    try:
        with engine.begin() as c:
            pk = c.execute(insert(invoices).values(**rec, created_by=created_by, created_at=time.time())
                           ).inserted_primary_key
            assert pk is not None
            new_id = pk[0]
            row = c.execute(select(invoices).where(invoices.c.id == new_id)).first()
        return 201, "created", row_dict(row)
    except IntegrityError:  # lost a race with an identical concurrent submit: the constraint decides
        with engine.begin() as c:
            return same_or_conflict(existing_for(c, rec), rec)


# ------------------------------------------------------------------------------------------------ auth


def logged_in(request: Request) -> bool:
    tok = request.cookies.get("erp_session", "")
    with engine.connect() as c:
        exp = c.execute(select(sessions.c.expires).where(sessions.c.token == tok)).scalar()
    return bool(exp and exp > time.time())


def key(x_api_key: str = Header(default="")) -> None:
    if x_api_key != API_KEY:
        raise HTTPException(401, "bad API key")


# ------------------------------------------------------------------------------------------------ web UI

CSS = """body{font:14px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:0;background:#f3f3f1;color:#222}
header{background:#2d3436;color:#fff;padding:12px 28px;display:flex;gap:22px;align-items:center}header b{font-size:17px}
header a{color:#dfe6e9;text-decoration:none}main{max-width:900px;margin:26px auto;padding:0 24px}
table{border-collapse:collapse;width:100%;background:#fff}th,td{padding:8px 10px;border-bottom:1px solid #e1e1dc;
text-align:left}th{background:#ecece8;font-size:12px;text-transform:uppercase;color:#636e72}
form.f{background:#fff;padding:20px 24px;border:1px solid #dcdcd6;border-radius:6px;display:grid;
grid-template-columns:170px 300px;gap:10px 14px;align-items:center}input{font:inherit;padding:7px 9px;
border:1px solid #b2bec3;border-radius:4px}button{font:inherit;padding:8px 16px;border:0;border-radius:4px;
background:#0984e3;color:#fff;cursor:pointer}.alert{background:#ffeaa7;border:1px solid #fdcb6e;padding:10px 14px;
border-radius:4px;margin:12px 0}.alert.err{background:#fab1a0;border-color:#e17055}.ok{background:#d5f5e3;
border:1px solid #55efc4;padding:10px 14px;border-radius:4px}"""


def page(request: Request, title: str, body: str, outcome: str = "", status: int = 200) -> HTMLResponse:
    b = request.scope.get("root_path", "")
    return HTMLResponse(f"<!doctype html><html><head><meta charset=utf-8><title>{escape(title)} · Northwind ERP</title>"
                        f"<style>{CSS}</style></head><body><header><b>Northwind ERP</b><a href='{b}/invoices'>Invoices"
                        f"</a><a href='{b}/invoices/new'>New invoice</a></header><main data-outcome='{outcome}'>"
                        f"<h1>{escape(title)}</h1>{body}</main></body></html>", status_code=status)


def record_table(r: dict) -> str:
    rows = [("ERP id", r["id"]), ("Vendor", r["vendor_name"]), ("Invoice number", r["invoice_number"]),
            ("Invoice date", r["invoice_date"]), ("Due date", r["due_date"]), ("Amount (INR)", r["amount_inr"]),
            ("PO number", r["po_number"] or "—"), ("Entered by", r["created_by"])]
    return "<table id=record>" + "".join(f"<tr><th>{k}<td>{escape(str(v))}</tr>" for k, v in rows) + "</table>"


@app.get("/")
def root(request: Request) -> RedirectResponse:
    return RedirectResponse(request.scope.get("root_path", "") + "/invoices", 303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/invoices", error: str = "") -> HTMLResponse:
    err = f"<div class='alert err' role=alert>{escape(error)}</div>" if error else ""
    return page(request, "Sign in", f"{err}<form class=f method=post><input type=hidden name=next value='{escape(next)}'>"
                "<label for=u>Username</label><input id=u name=username><label for=p>Password</label>"
                "<input id=p name=password type=password><span></span><button>Sign in</button></form>")


@app.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form(""), next: str = Form("/invoices")):
    b = request.scope.get("root_path", "")
    if username != USER or password != PASSWORD:
        return login_form(request, next, "Wrong username or password")
    tok = secrets.token_hex(24)
    with engine.begin() as c:
        c.execute(insert(sessions).values(token=tok, expires=time.time() + 3600))
    r = RedirectResponse(b + (next if next.startswith("/") else "/invoices"), 303)
    r.set_cookie("erp_session", tok, httponly=True, samesite="lax")
    return r


def need_login(request: Request, path: str) -> RedirectResponse | None:
    if logged_in(request):
        return None
    return RedirectResponse(f"{request.scope.get('root_path', '')}/login?next={path}", 303)


@app.get("/invoices", response_class=HTMLResponse)
def list_page(request: Request, q: str = ""):
    if (r := need_login(request, "/invoices")):
        return r
    b = request.scope.get("root_path", "")
    stmt = select(invoices).order_by(invoices.c.id.desc())
    if q:
        stmt = stmt.where(or_(invoices.c.vendor_key.contains(vkey(q)), invoices.c.invoice_number.contains(q)))
    with engine.connect() as c:
        rows = [row_dict(r) for r in c.execute(stmt)]
    trs = "".join(f"<tr><td><a href='{b}/invoices/{r['id']}'>{r['id']}</a><td>{escape(r['vendor_name'])}"
                  f"<td>{r['invoice_number']}<td>{r['invoice_date']}<td>{r['due_date']}<td>₹{r['amount_paise'] / 100:,.2f}"
                  f"<td>{escape(r['created_by'])}</tr>" for r in rows)
    return page(request, "Payable invoices",
                f"<form method=get style='margin:0 0 14px'><input name=q value='{escape(q)}' placeholder='Search vendor"
                f" or invoice'> <button>Search</button></form><table id=invoices><tr><th>ID<th>Vendor<th>Invoice<th>"
                f"Invoice date<th>Due date<th>Amount<th>Entered by</tr>{trs}</table>")


def form_html(request: Request, values: dict, error: str = "") -> str:
    b = request.scope.get("root_path", "")
    label = "Add Invoice" if get_setting("rename_submit") == "1" else "Submit Invoice"
    fields = [("vendor_name", "Vendor name"), ("invoice_number", "Invoice number"), ("invoice_date", "Invoice date"),
              ("due_date", "Due date"), ("amount_inr", "Amount (INR)"), ("po_number", "PO number"),
              ("idempotency_key", "Reference key")]
    inputs = "".join(f"<label for={k}>{lbl}</label><input id={k} name={k} value='{escape(str(values.get(k, '')))}'"
                     f"{' placeholder=YYYY-MM-DD' if 'date' in k else ''}>" for k, lbl in fields)
    err = f"<div class='alert err' role=alert>{escape(error)}</div>" if error else ""
    return (f"{err}<form class=f method=post action='{b}/invoices'>{inputs}<span></span>"
            f"<div><button type=submit>{label}</button> <button type=reset style='background:#b2bec3'>Clear"
            "</button></div></form>")


@app.get("/invoices/new", response_class=HTMLResponse)
def new_form(request: Request):
    if (r := need_login(request, "/invoices/new")):
        return r
    return page(request, "New payable invoice", form_html(request, {}))


@app.post("/invoices")
async def submit(request: Request):
    if (r := need_login(request, "/invoices/new")):
        return r
    data = dict(await request.form())
    b = request.scope.get("root_path", "")
    try:
        status, outcome, rec = create(data, created_by="ops (web)")
    except Rejected as e:
        if e.status == 422:
            return page(request, "New payable invoice", form_html(request, data, e.message), "validation_error", 422)
        if e.status == 409:
            return page(request, "Invoice conflict", f"<div class='alert err' role=alert>{escape(e.message)}</div>"
                        + record_table(e.record or {}), "conflict", 409)
        return page(request, "500 Internal Server Error", f"<div class='alert err' role=alert>{escape(e.message)}</div>",
                    "error", 500)
    if outcome == "exists":
        return page(request, "Invoice already exists", "<div class=ok role=status>An identical invoice is already "
                    f"recorded; nothing was changed.</div>{record_table(rec)}", "exists", 200)
    return RedirectResponse(f"{b}/invoices/{rec['id']}?created=1", 303)


@app.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def detail(request: Request, invoice_id: int, created: int = 0):
    if (r := need_login(request, f"/invoices/{invoice_id}")):
        return r
    with engine.connect() as c:
        row = c.execute(select(invoices).where(invoices.c.id == invoice_id)).first()
    if not row:
        return page(request, "Not found", "<p>No such invoice.</p>", "error", 404)
    rec = row_dict(row)
    head = "<div class=ok role=status>Invoice created.</div>" if created else ""
    return page(request, f"Invoice #{rec['id']}", head + record_table(rec), "created" if created else "view")


# ------------------------------------------------------------------------------------------------ JSON API


@app.get("/api/invoices", dependencies=[Depends(key)])
def api_search(vendor_name: str = "", invoice_number: str = "", idempotency_key: str = "") -> list[dict]:
    stmt = select(invoices).order_by(invoices.c.id)
    if vendor_name:
        stmt = stmt.where(invoices.c.vendor_key == vkey(vendor_name))
    if invoice_number:
        stmt = stmt.where(invoices.c.invoice_number == invoice_number)
    if idempotency_key:
        stmt = stmt.where(invoices.c.idempotency_key == idempotency_key)
    with engine.connect() as c:
        return [row_dict(r) for r in c.execute(stmt)]


@app.get("/api/invoices/{invoice_id}", dependencies=[Depends(key)])
def api_get(invoice_id: int) -> dict:
    with engine.connect() as c:
        row = c.execute(select(invoices).where(invoices.c.id == invoice_id)).first()
    if not row:
        raise HTTPException(404, "not found")
    return row_dict(row)


@app.post("/api/invoices", dependencies=[Depends(key)])
def api_create(body: dict):
    from fastapi.responses import JSONResponse
    try:
        status, outcome, rec = create(body, created_by="api")
        return JSONResponse({"outcome": outcome, "record": rec}, status_code=status)
    except Rejected as e:
        return JSONResponse({"outcome": e.outcome, "error": e.message, "record": e.record}, status_code=e.status)


@app.post("/api/admin/reset", dependencies=[Depends(key)])
def api_reset() -> dict:
    reset()
    return {"ok": True}


@app.post("/api/admin/chaos", dependencies=[Depends(key)])
def api_chaos(body: dict) -> dict:
    for k in ("fail_next_create", "rename_submit"):
        if k in body:
            set_setting(k, int(body[k]))
    return {k: get_setting(k, "0") for k in ("fail_next_create", "rename_submit")}
