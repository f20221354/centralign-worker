"""The tool registry: names, descriptions and typed argument contracts. This is all the model can ask for."""
from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel

from src.contracts import (AskHumanArgs, CreateInternalInvoiceArgs, ExtractInvoiceArgs, FinishArgs, ListInvoicesArgs,
                           OpenInvoiceArgs, SearchInternalInvoicesArgs, SearchVendorArgs)

# name: (argument model, description, channel)
REGISTRY: dict[str, tuple[type[BaseModel], str, str]] = {
    "search_vendor": (SearchVendorArgs, "Search the vendor portal for vendors by name. Returns vendor ids.", "browser"),
    "list_invoices": (ListInvoicesArgs, "List a vendor's invoices on the portal (number, invoice date, due date, "
                                        "amount, status).", "browser"),
    "open_invoice": (OpenInvoiceArgs, "Open one invoice's page on the portal and read its details.", "browser"),
    "extract_invoice": (ExtractInvoiceArgs, "Download the invoice PDF and extract structured fields from it. "
                                            "Cross-checked against the portal.", "file"),
    "search_internal_invoices": (SearchInternalInvoicesArgs, "Search the internal ERP for invoices of a vendor, "
                                                             "optionally one invoice number.", "api"),
    "create_internal_invoice": (CreateInternalInvoiceArgs, "Enter a payable invoice in the internal ERP through its "
                                                            "web form. Policy-checked; may pause for approval.",
                                "browser"),
    "ask_human": (AskHumanArgs, "Ask the finance team a clarifying question and wait for the answer.", "runtime"),
    "finish": (FinishArgs, "Declare the goal done (or impossible). Triggers independent verification.", "runtime"),
}


def _simplify(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Inline $refs, collapse Optional, drop keywords some OpenAI-compatible providers reject."""
    if "$ref" in schema:
        return _simplify(copy.deepcopy(defs[schema["$ref"].split("/")[-1]]), defs)
    if "anyOf" in schema:
        options = [o for o in schema["anyOf"] if o.get("type") != "null"]
        merged = {**_simplify(options[0], defs), **{k: v for k, v in schema.items() if k in ("description",)}}
        return merged
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k in ("title", "default", "$defs", "pattern", "additionalProperties", "maxLength", "minLength",
                 "exclusiveMinimum"):
            continue
        if k == "format":
            out["description"] = (schema.get("description", "") + " (YYYY-MM-DD)").strip()
            continue
        if k == "properties":
            out[k] = {name: _simplify(p, defs) for name, p in v.items()}
        elif k == "items" and isinstance(v, dict):
            out[k] = _simplify(v, defs)
        else:
            out[k] = v
    return out


def openai_tools() -> list[dict[str, Any]]:
    tools = []
    for name, (model, desc, _channel) in REGISTRY.items():
        raw = model.model_json_schema()
        params = _simplify(raw, raw.get("$defs", {}))
        params.setdefault("properties", {})["action_rationale"] = {
            "type": "string", "description": "One short factual sentence: why this action now."}
        params["required"] = [*params.get("required", []), "action_rationale"]
        tools.append({"type": "function", "function": {"name": name, "description": desc, "parameters": params}})
    return tools
