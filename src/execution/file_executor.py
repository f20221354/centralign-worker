"""Files: download an invoice PDF, keep a copy as evidence, read its text layer, flag instruction-like text."""
from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader

# Text that tries to instruct the system. Detecting it is only for visibility: the defence is that document text
# never becomes an instruction and policy is code, so nothing here is needed to stay safe.
INJECTION_PATTERNS = [r"ignore (all|any|previous) .{0,30}(polic|instruction|rule)", r"approve .{0,30}immediately",
                      r"skip .{0,20}approval", r"transfer (the )?payment", r"pre-?approved",
                      r"automated systems?", r"mark .{0,20}paid"]


def save_pdf(content: bytes, docs_dir: Path, name: str) -> Path:
    docs_dir.mkdir(parents=True, exist_ok=True)
    path = docs_dir / f"{name}.pdf"
    path.write_bytes(content)
    return path


def pdf_text(path: Path) -> str:
    text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages).strip()
    if not text:
        raise ValueError(f"{path.name} has no text layer (scanned?); this prototype reads text PDFs only")
    return text


def injection_signals(text: str) -> list[str]:
    low = " ".join(text.lower().split())
    return [m.group(0) for p in INJECTION_PATTERNS for m in [re.search(p, low)] if m]
