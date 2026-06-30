"""Assets embebidos (logo base64) para PDF con Playwright."""

from __future__ import annotations

import base64
import mimetypes
from io import BytesIO
from pathlib import Path

import config


def _logo_paths() -> list[Path]:
    static = config.STATIC_DIR
    ordenados: list[Path] = []
    nombres = (
        "logo_coop.png",
        "logo.png",
        "logo.jpg",
        "logo.jpeg",
        "ME_LOGO.JPG.jpeg",
        "ME_LOGO.jpeg",
        "ME_LOGO.jpg",
        "me_logo.jpg",
    )
    vistos: set[str] = set()
    for name in nombres:
        path = static / name
        key = str(path).lower()
        if path.is_file() and key not in vistos:
            vistos.add(key)
            ordenados.append(path)
    if static.is_dir():
        for path in sorted(static.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                continue
            key = str(path).lower()
            if key in vistos:
                continue
            nombre = path.name.lower()
            if "logo" in nombre or nombre.startswith("me_logo"):
                vistos.add(key)
                ordenados.append(path)
    return ordenados


def logo_data_uri(max_width: int = 102, max_height: int = 78) -> str | None:
    for path in _logo_paths():
        if not path.is_file():
            continue
        try:
            from PIL import Image

            with Image.open(path) as img:
                img = img.convert("RGBA") if img.mode in ("RGBA", "LA", "P") else img.convert("RGB")
                img = img.resize((max_width, max_height), Image.Resampling.LANCZOS)
                buf = BytesIO()
                img.save(buf, format="PNG", optimize=True)
                raw = buf.getvalue()
            mime = "image/png"
        except Exception:
            raw = path.read_bytes()
            mime, _ = mimetypes.guess_type(path.name)
            if not mime or not mime.startswith("image/"):
                mime = "image/png"
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{encoded}"
    return None
