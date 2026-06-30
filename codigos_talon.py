"""Resuelve códigos de pago para el talón y genera barras ITF / I2of5 (PNG)."""

from __future__ import annotations

import base64
from io import BytesIO
from typing import Any

from codigos_pago import (
    _codigos_desde_db,
    build_cesp_talon,
    build_link,
    cesp_desde_archivo,
    resumen_codigos_pago,
    solo_digitos,
)

# ITF legacy cobranzas AR (python-barcode default): angosta:ancha = 2:5
_ITF_NARROW = 2
_ITF_WIDE = 5
_ITF_QUIET_MM = 2.0
_ITF_DPI = 600


def _barcode_spec(code_len: int) -> dict[str, float | bool]:
    """Dimensiones objetivo por tipo de código (mm)."""
    if code_len > 30:
        return {"width_mm": 86.0, "height_mm": 6.0, "wide": True}
    if code_len > 18:
        return {"width_mm": 84.0, "height_mm": 5.0, "wide": True}
    return {"width_mm": 72.0, "height_mm": 5.0, "wide": True}


def _target_width_mm(code_len: int) -> float:
    return float(_barcode_spec(code_len)["width_mm"])


def _target_height_mm(code_len: int) -> float:
    return float(_barcode_spec(code_len)["height_mm"])


def _itf_module_count(code: str) -> int:
    import barcode
    from barcode.writer import SVGWriter

    cls = barcode.get_barcode_class("itf")
    bc = cls(code, writer=SVGWriter(), narrow=_ITF_NARROW, wide=_ITF_WIDE)
    return len(bc.build()[0])


def _fuente(param: str | None, db_val: str | None, calc_val: str | None) -> str:
    if param:
        return "param"
    if db_val:
        return "db"
    if calc_val:
        return "calculado"
    return ""


def resolve_codigos_pago(
    fac: dict[str, Any],
    *,
    cod_nac: str | None = None,
    cod_pfs: str | None = None,
    cod_cesp: str | None = None,
) -> dict[str, Any]:
    db = _codigos_desde_db(fac)
    nac_param = solo_digitos(cod_nac)
    pfs_param = solo_digitos(cod_pfs)
    cesp_param = solo_digitos(cod_cesp)

    calc = resumen_codigos_pago(
        fac,
        nac_esperado=nac_param,
        pfs_esperado=pfs_param,
    )

    nac_db = db.get("nac", "")
    pfs_db = db.get("pfs", "")
    nac = nac_param or nac_db or calc.get("nac", "")
    pfs = pfs_param or pfs_db or calc.get("pfs", "")

    # Barra cooperativa (codbarCoop): distinto de facturas.cesp (número CESP del talón).
    coop_db = db.get("coop", "") or solo_digitos(fac.get("codbarCoop"))
    coop_archivo = cesp_desde_archivo(fac)
    coop_calc = coop_archivo or build_cesp_talon(fac) or calc.get("cesp", "")
    coop_barra = cesp_param or coop_db or coop_calc

    suministro = fac.get("suministro")
    link = calc.get("link", "") or (build_link(suministro) if suministro else "")

    if cesp_param:
        coop_fuente = "param"
    elif coop_db and coop_barra == coop_db:
        coop_fuente = "db"
    elif coop_archivo and coop_barra == coop_archivo:
        coop_fuente = "archivo"
    elif coop_barra == build_cesp_talon(fac):
        coop_fuente = "calculado"
    else:
        coop_fuente = _fuente(None, None, calc.get("cesp"))

    out: dict[str, Any] = {
        "nac": nac,
        "pfs": pfs,
        "cesp": coop_barra,
        "coop": coop_barra,
        "link": link,
        "nac_fuente": _fuente(nac_param, nac_db, calc.get("nac")),
        "pfs_fuente": _fuente(pfs_param, pfs_db, calc.get("pfs")),
        "cesp_fuente": coop_fuente,
        "coop_fuente": coop_fuente,
        "link_fuente": "calculado" if link else "",
        "coop_esperado": coop_calc,
        "cesp_esperado": coop_calc,
    }
    if cesp_param and coop_calc and cesp_param != coop_calc:
        out["coop_advertencia"] = (
            f"Código cooperativo del parámetro ({cesp_param}) no coincide con el del comprobante/archivo "
            f"({coop_calc}). Usá --cod-cesp {coop_calc} para igualar el original."
        )
    return out


def _itf_module_width(code: str) -> float:
    """Calcula el ancho de módulo para que el código ocupe el talón sin deformar."""
    modules = _itf_module_count(code)
    bar_area = _target_width_mm(len(code)) - 2 * _ITF_QUIET_MM
    return bar_area / modules


def _render_barcode_bitmap(code: str) -> Any:
    import barcode
    from barcode.writer import ImageWriter
    from PIL import Image

    spec = _barcode_spec(len(code))
    cls = barcode.get_barcode_class("itf")
    bc = cls(code, writer=ImageWriter(), narrow=_ITF_NARROW, wide=_ITF_WIDE)
    buf = BytesIO()
    bc.write(
        buf,
        options={
            "module_width": _itf_module_width(code),
            "module_height": float(spec["height_mm"]),
            "quiet_zone": _ITF_QUIET_MM,
            "write_text": False,
            "dpi": _ITF_DPI,
        },
    )
    return Image.open(buf).convert("L")


def barcode_embed(digits: str) -> tuple[str | None, int, int, str, str]:
    code = solo_digitos(digits)
    if not code:
        return None, 0, 0, "0", "0"
    try:
        img = _render_barcode_bitmap(code)
    except Exception:
        return None, 0, 0, "0", "0"

    img = img.point(lambda p: 255 if p > 140 else 0, mode="L")
    img = img.convert("1")

    w_px, h_px = img.size
    w_mm = _target_width_mm(len(code))
    h_mm = _target_height_mm(len(code))

    out_buf = BytesIO()
    img.save(out_buf, format="PNG", optimize=False)
    encoded = base64.b64encode(out_buf.getvalue()).decode("ascii")
    return (
        f"data:image/png;base64,{encoded}",
        w_px,
        h_px,
        f"{w_mm:.2f}",
        f"{h_mm:.2f}",
    )


def _attach_barcode(out: dict[str, Any], key: str, val: str) -> None:
    uri, w, h, w_mm, h_mm = barcode_embed(val)
    spec = _barcode_spec(len(val))
    out[f"{key}_img"] = uri
    out[f"{key}_w"] = w
    out[f"{key}_h"] = h
    out[f"{key}_w_mm"] = w_mm
    out[f"{key}_h_mm"] = h_mm
    out[f"{key}_wide"] = bool(spec["wide"])


def codigos_con_barras(
    fac: dict[str, Any],
    *,
    cod_nac: str | None = None,
    cod_pfs: str | None = None,
    cod_cesp: str | None = None,
) -> dict[str, Any]:
    base = resolve_codigos_pago(
        fac,
        cod_nac=cod_nac,
        cod_pfs=cod_pfs,
        cod_cesp=cod_cesp,
    )
    out: dict[str, Any] = dict(base)
    for key in ("nac", "pfs", "cesp", "link"):
        val = solo_digitos(base.get(key) or "")
        out[key] = val
        if val:
            _attach_barcode(out, key, val)
        else:
            out[f"{key}_img"] = None
            out[f"{key}_w"] = 0
            out[f"{key}_h"] = 0
            out[f"{key}_w_mm"] = "0"
            out[f"{key}_h_mm"] = "0"
            out[f"{key}_wide"] = False
    out["tiene_alguno"] = any(out.get(k) for k in ("nac", "pfs", "cesp", "link"))
    return out
