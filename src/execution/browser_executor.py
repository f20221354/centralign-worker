"""The worker's hands on the screen: a real Chromium driven by Playwright.

Portal reads (search, vendor page, invoice page) and the ERP write (New invoice form) happen here, visibly.
Every method returns an Observation; none decides success for the runtime. A screenshot is taken after each
action and copied to `live.png`, which the console shows as the worker's live screen.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from src.contracts import CreateInternalInvoiceArgs, Observation, ObsStatus
from src.execution.api_executor import erp_url, portal_url

LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-extensions",
               "--disable-background-networking", "--renderer-process-limit=1"]
DEFAULT_SUBMIT = "Submit Invoice"


class BrowserExecutor:
    def __init__(self, run_dir: Path):
        self.dir = run_dir / "shots"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.live = run_dir / "live.png"
        self.n = len(list(self.dir.glob("*.png")))
        self._pw: Any = None
        self.page: Any = None

    def _ensure(self) -> Any:
        if self.page is None:
            record = os.environ.get("RECORD_DIR")
            self._pw = sync_playwright().start()
            browser = self._pw.chromium.launch(headless=os.environ.get("HEADED") != "1", args=LAUNCH_ARGS,
                                               slow_mo=int(os.environ.get("BROWSER_SLOWMO", "250" if record else "0")))
            opts: dict[str, Any] = {"viewport": {"width": 1280, "height": 760}}
            if record:
                opts.update(record_video_dir=record, record_video_size={"width": 1280, "height": 760})
            self.ctx = browser.new_context(**opts)
            self.page = self.ctx.new_page()
            self.page.set_default_timeout(int(os.environ.get("BROWSER_TIMEOUT_MS", "20000")))
        return self.page

    def close(self) -> None:
        if self._pw:
            try:
                self.ctx.close()
            finally:
                self._pw.stop()
            self._pw = self.page = None

    def shot(self, name: str) -> str:
        self.n += 1
        path = self.dir / f"{self.n:02d}-{name}.png"
        self.page.screenshot(path=path)
        shutil.copyfile(path, self.live)
        return str(path)

    def _fail(self, action: str, e: Exception) -> Observation:
        transient = isinstance(e, PlaywrightTimeout) or "net::" in str(e)
        shot = None
        try:
            shot = self.shot(f"{action}-error")
        except PlaywrightError:
            pass
        return Observation(action_name=action, channel="browser", screenshot_path=shot,
                           status=ObsStatus.HTTP_ERROR if transient else ObsStatus.DOM_ERROR,
                           error_message=f"{'timeout/network' if transient else 'browser'}: {str(e)[:300]}")

    # ------------------------------------------------------------------ vendor portal (reads, visible)

    def search_vendor(self, query: str) -> Observation:
        try:
            p = self._ensure()
            p.goto(portal_url() + "/")
            p.get_by_label("Vendor name").fill(query)
            p.get_by_role("button", name="Search").click()
            p.wait_for_load_state()
            rows = p.locator("#results tr").all()[1:]
            hits = [dict(zip(["vendor_id", "name", "city"], [c.inner_text().strip() for c in r.locator("td").all()]))
                    for r in rows]
            return Observation(action_name="search_vendor", channel="browser",
                               status=ObsStatus.SUCCESS if hits else ObsStatus.NOT_FOUND,
                               data={"query": query, "matches": hits}, screenshot_path=self.shot("portal-search"))
        except PlaywrightError as e:
            return self._fail("search_vendor", e)

    def list_invoices(self, vendor_id: str) -> Observation:
        try:
            p = self._ensure()
            resp = p.goto(f"{portal_url()}/vendors/{quote(vendor_id)}")
            if resp and resp.status == 404:
                return Observation(action_name="list_invoices", channel="browser", status=ObsStatus.NOT_FOUND,
                                   http_status=404, error_message=f"vendor {vendor_id} not found")
            keys = ["invoice_number", "invoice_date", "due_date", "amount", "status"]
            rows = [dict(zip(keys, [c.inner_text().strip() for c in r.locator("td").all()]))
                    for r in p.locator("#invoices tr").all()[1:]]
            return Observation(action_name="list_invoices", channel="browser", status=ObsStatus.SUCCESS,
                               data={"vendor_id": vendor_id, "vendor_name": p.locator("h1").inner_text().strip(),
                                     "invoices": rows}, screenshot_path=self.shot("portal-invoices"))
        except PlaywrightError as e:
            return self._fail("list_invoices", e)

    def open_invoice(self, vendor_id: str, number: str) -> Observation:
        try:
            p = self._ensure()
            resp = p.goto(f"{portal_url()}/vendors/{quote(vendor_id)}/invoices/{quote(number)}")
            if resp and resp.status == 404:
                return Observation(action_name="open_invoice", channel="browser", status=ObsStatus.NOT_FOUND,
                                   http_status=404, error_message=f"invoice {number} not found")
            fields = {d.get_attribute("data-field"): d.inner_text().strip() for d in p.locator("#invoice dd").all()}
            return Observation(action_name="open_invoice", channel="browser", status=ObsStatus.SUCCESS,
                               data={"vendor_id": vendor_id, "fields": fields},
                               screenshot_path=self.shot(f"portal-{number}"))
        except PlaywrightError as e:
            return self._fail("open_invoice", e)

    # ------------------------------------------------------------------ ERP (the side effect, visible)

    def _erp_login(self) -> None:
        p = self.page
        p.get_by_label("Username").fill(os.environ.get("ERP_USER", "ops"))  # credentials never reach the model
        p.get_by_label("Password").fill(os.environ.get("ERP_PASS", "northwind-demo"))
        p.get_by_role("button", name="Sign in").click()
        p.wait_for_load_state()
        if "/login" in p.url:
            raise PermissionError("ERP rejected ERP_USER / ERP_PASS")

    def create_invoice(self, args: CreateInternalInvoiceArgs, key: str,
                       after_submit: Callable[[], None] = lambda: None) -> Observation:
        action = "create_internal_invoice"
        label = (args.ui_hints.submit_button_label if args.ui_hints and args.ui_hints.submit_button_label
                 else DEFAULT_SUBMIT)
        try:
            p = self._ensure()
            p.goto(erp_url() + "/invoices/new")
            if "/login" in p.url:
                self._erp_login()
                if "/invoices/new" not in p.url:
                    p.goto(erp_url() + "/invoices/new")
            values = {"Vendor name": args.vendor_name, "Invoice number": args.invoice_number,
                      "Invoice date": args.invoice_date.isoformat(), "Due date": args.due_date.isoformat(),
                      "Amount (INR)": args.amount_inr, "PO number": args.po_number or "", "Reference key": key}
            for lbl, v in values.items():
                p.get_by_label(lbl, exact=True).fill(v)
            button = p.locator("form").get_by_role("button", name=label, exact=True)
            if not button.count():  # the UI changed: report what is there; the model proposes the adaptation
                names = [b.inner_text().strip() for b in p.locator("form").get_by_role("button").all()]
                return Observation(action_name=action, channel="browser", status=ObsStatus.DOM_ERROR,
                                   error_message=f"button '{label}' not found on the New invoice form",
                                   data={"expected_button": label, "available_buttons": names},
                                   screenshot_path=self.shot("erp-form-changed"))
            self.shot("erp-form-filled")
            with p.expect_navigation():
                button.click()
            after_submit()  # crash simulation hook: the write happened, the result is not recorded yet
            outcome = p.locator("main").get_attribute("data-outcome") or ""
            alert = p.get_by_role("alert")
            msg = alert.inner_text().strip() if alert.count() else ""
            record = {r.locator("th").inner_text().strip(): r.locator("td").inner_text().strip()
                      for r in p.locator("#record tr").all()}
            shot = self.shot(f"erp-{outcome or 'result'}")
            if outcome in ("created", "exists"):
                return Observation(action_name=action, channel="browser", status=ObsStatus.SUCCESS, screenshot_path=shot,
                                   data={"erp_outcome": outcome, "erp_url": p.url, "record": record})
            if outcome == "conflict":
                return Observation(action_name=action, channel="browser", status=ObsStatus.CONFLICT, http_status=409,
                                   error_message=msg, data={"record": record}, screenshot_path=shot)
            if outcome == "validation_error":
                return Observation(action_name=action, channel="browser", status=ObsStatus.VALIDATION_ERROR,
                                   http_status=422, error_message=msg, screenshot_path=shot)
            status = 500 if "500" in p.title() else None
            return Observation(action_name=action, channel="browser", status=ObsStatus.HTTP_ERROR, http_status=status,
                               error_message=msg or f"unexpected page {p.url}", screenshot_path=shot)
        except PermissionError as e:
            return Observation(action_name=action, channel="browser", status=ObsStatus.VALIDATION_ERROR,
                               error_message=str(e), data={"fatal": True})  # configuration: retrying can't help
        except PlaywrightError as e:
            return self._fail(action, e)
