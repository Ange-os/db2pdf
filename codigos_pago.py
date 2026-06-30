# Lógica de códigos de pago (BNA, Pago Fácil, CESP, LINK).
# Fuente canónica: d:\Proyecode\DB2PDF\codigos_pago.py

from __future__ import annotations

from pathlib import Path
import importlib.util

_CANDIDATOS = (
    Path(__file__).resolve().parent / "_codigos_pago_impl.py",
)

_mod = None
for _src in _CANDIDATOS:
    if not _src.is_file():
        continue
    _spec = importlib.util.spec_from_file_location("_codigos_pago_src", _src)
    _mod = importlib.util.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(_mod)
    break

if _mod is None:
    raise ImportError(
        "No se encontró codigos_pago. Copiá DB2PDF/codigos_pago.py a "
        "db2pdf2/_codigos_pago_impl.py o mantené el proyecto hermano DB2PDF."
    )

globals().update({k: v for k, v in vars(_mod).items() if not k.startswith("__")})
__all__ = [k for k in vars(_mod) if not k.startswith("__")]
