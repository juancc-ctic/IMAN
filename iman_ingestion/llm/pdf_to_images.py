"""Rasterize PDF pages to PNG for multimodal LLM (same approach as pdftoppm in Node)."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List


def convert_pdf_to_base64_pngs(
    pdf_path: Path,
    *,
    max_pages: int,
    dpi: int = 150,
) -> List[str]:
    """Convert the first ``max_pages`` pages of a PDF to base64-encoded PNG strings.

    Uses ``pdftoppm`` (Poppler), matching ``ai-controller-example.js``:
    ``pdftoppm -png -r <dpi> -f 1 -l <max_pages> <pdf> <out_prefix>``.

    Note: ``-r`` and the DPI value must be **separate argv entries** (e.g. ``-r``,
    ``150``). A single token like ``-r150`` is rejected by Poppler 22.x (exit 99).

    Args:
        pdf_path: Path to an existing PDF file.
        max_pages: Last page to render (``-l``); no pages if <= 0.
        dpi: Raster resolution (default 150).

    Returns:
        Base64 ASCII strings, one per page, sorted by page order.

    Raises:
        FileNotFoundError: If ``pdf_path`` does not exist.
        RuntimeError: If ``pdftoppm`` is missing or exits non-zero.
    """
    if max_pages <= 0:
        return []
    path = pdf_path.resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    if not shutil.which("pdftoppm"):
        raise RuntimeError(
            "pdftoppm not found; install Poppler (e.g. apt install poppler-utils)",
        )

    tmp = tempfile.mkdtemp(prefix="iman-pdf-")
    try:
        out_prefix = Path(tmp) / "page"
        cmd = [
            "pdftoppm",
            "-png",
            "-r",
            str(dpi),
            "-f",
            "1",
            "-l",
            str(max_pages),
            str(path),
            str(out_prefix),
        ]
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"pdftoppm failed ({proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}",
            )

        pngs = sorted(Path(tmp).glob("page-*.png"))
        if not pngs:
            pngs = sorted(Path(tmp).glob("*.png"))
        if not pngs:
            raise RuntimeError("pdftoppm produced no PNG files")

        return [base64.b64encode(p.read_bytes()).decode("ascii") for p in pngs]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def multimodal_max_pages_per_pdf() -> int:
    """Max pages per PDF from ``IMAN_MULTIMODAL_MAX_PAGES_PER_PDF`` (default 20)."""
    return int(os.environ.get("IMAN_MULTIMODAL_MAX_PAGES_PER_PDF", "20"))


def multimodal_dpi() -> int:
    """DPI for pdftoppm from ``IMAN_MULTIMODAL_DPI`` (default 150)."""
    return int(os.environ.get("IMAN_MULTIMODAL_DPI", "150"))


def multimodal_max_images_total() -> int:
    """Cap total rasterized pages per tender (PCAP only) from env (default 60)."""
    return int(os.environ.get("IMAN_MULTIMODAL_MAX_IMAGES_TOTAL", "60"))
