"""
mapear_factura.py — Adapta fila MariaDB (fetch_factura_completa) al dict del template.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime
from typing import Any

from assets import logo_data_uri
from codigos_talon import codigos_con_barras
from tarifa_actual import CARGO_FIJO, FECHA_ASAMBLEA, RANGOS_M3

_TALON_MARKERS = (
    "pago fácil", "pago facil", "banex", "supervielle",
    "banco nación", "banco nacion", "pagos link", "vto.",
)

_CATEGORIA_TARIFA = {1: "PARTICULAR", 2: "COMERCIAL", 3: "INDUSTRIAL"}

_CONDICIONES_IVA = frozenset({
    "CONSUMIDOR FINAL",
    "RESPONSABLE INSCRIPTO",
    "RESPONSABLE INSCRIPTO M",
    "MONOTRIBUTO",
    "EXENTO",
    "NO CATEGORIZADO",
})

_GRUPOS_ORDEN = ("CONSUMO AGUA", "OTROS CONCEPTOS", "IVA E IMPUESTOS", "OTROS")

_GRUPO_SUBTOTAL_CAMPO = {
    "CONSUMO AGUA": "subtotal_consumo_agua",
    "OTROS CONCEPTOS": "subtotal_otros_conceptos",
    "IVA E IMPUESTOS": "subtotal_iva_impuestos",
    "OTROS": "subtotal_mora",
}

_GRUPO_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("IVA E IMPUESTOS", ("IVA", "IMPUESTO", "PERCEPCION", "IIBB")),
    ("OTROS", ("MORA", "INTERES", "RECARGO")),
    (
        "CONSUMO AGUA",
        ("AGUA", "CARGO FIJO", "CONSUMO", "BONIFIC", "REDONDEO", "MEDID", "M3", "M³", "BASICO"),
    ),
)


def _importe_valido(value: Any) -> bool:
    if value is None or value == "":
        return False
    try:
        return float(value) != 0
    except (TypeError, ValueError):
        return False


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _sin_acentos(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _fmt_fecha(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    s = _str_or_empty(value)
    if not s:
        return ""
    if re.match(r"^\d{2}/\d{2}/\d{4}$", s):
        return s
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    if "T" in s:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")[:19]).strftime("%d/%m/%Y")
        except ValueError:
            pass
    return s


def _looks_like_talon_text(s: str) -> bool:
    lower = _sin_acentos(s.lower())
    return any(m in lower for m in _TALON_MARKERS)


def _direccion_socio(fac: dict[str, Any]) -> str:
    for key in ("direccion_postal", "domicilio", "direccion"):
        val = _str_or_empty(fac.get(key))
        if val and not _looks_like_talon_text(val):
            return val
    return _str_or_empty(fac.get("ubicacion"))


def _categoria_socio(fac: dict[str, Any]) -> str:
    ct = fac.get("categoria_tarifa")
    if ct is not None and str(ct).strip() != "":
        try:
            return _CATEGORIA_TARIFA.get(int(ct), str(ct))
        except (TypeError, ValueError):
            return str(ct)
    cat = _str_or_empty(fac.get("categoria"))
    if cat and cat.upper() not in _CONDICIONES_IVA:
        return cat
    return ""


def _parse_nro_comprobante(nro: Any) -> tuple[str, str]:
    if not nro:
        return "", ""
    s = re.sub(r"\s+", " ", str(nro).strip())
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 12:
        return digits[:4], digits[4:12]
    parts = s.split()
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "", s


def _tipo_titulo(fac: dict[str, Any]) -> str:
    raw = _str_or_empty(fac.get("tipo_comprobante") or fac.get("tipo_titulo")).upper()
    if "NOTA" in raw and "CRED" in raw:
        return "LIQ. DE SERV. CR."
    if "LIQ" in raw and "SERV" in raw:
        return raw if "CR." in raw else raw.replace("LIQUIDACION", "LIQ. DE SERV. CR. B")
    if raw in ("", "LIQUIDACION", "LIQ") or raw.startswith("LIQUIDACION"):
        return "LIQ. DE SERV. CR. B"
    return "LIQ. DE SERV. CR. B"


def _clasificar_concepto(nombre: str) -> str:
    u = _sin_acentos(_str_or_empty(nombre).upper())
    if u.startswith("TOTAL "):
        return ""
    for grupo, keys in _GRUPO_KEYWORDS:
        if any(k in u for k in keys):
            return grupo
    return "OTROS CONCEPTOS"


def _linea_concepto(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "descripcion": _str_or_empty(item.get("concepto")),
        "cantidad": _float_or_none(item.get("cantidad")),
        "periodo": _str_or_empty(item.get("periodo")) or None,
        "precio": _float_or_none(item.get("precio") or item.get("precio_unitario")),
        "importe": _float_or_none(item.get("importe")) or 0.0,
    }


def _grupos_desde_items(fac: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not items:
        return _grupos_conceptos(fac)

    buckets: dict[str, list[dict[str, Any]]] = {g: [] for g in _GRUPOS_ORDEN}
    for item in items:
        linea = _linea_concepto(item)
        grupo = _clasificar_concepto(linea["descripcion"])
        if not grupo:
            continue
        buckets[grupo].append(linea)

    grupos: list[dict[str, Any]] = []
    for nombre in _GRUPOS_ORDEN:
        lineas = buckets[nombre]
        campo = _GRUPO_SUBTOTAL_CAMPO[nombre]
        subtotal = _float_or_none(fac.get(campo))
        if not lineas and subtotal is None:
            continue
        if subtotal is None and lineas:
            subtotal = round(sum(l["importe"] for l in lineas), 2)
        if subtotal is None:
            continue
        if not lineas:
            lineas = [{
                "descripcion": f"Total {nombre}",
                "cantidad": None,
                "periodo": None,
                "precio": None,
                "importe": subtotal,
            }]
        grupos.append({"nombre": nombre, "subtotal": subtotal, "lineas": lineas})

    return grupos or _grupos_conceptos(fac)


def _deudas_desde_db(deudas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filas: list[dict[str, Any]] = []
    for d in deudas:
        importe = _float_or_none(d.get("importe"))
        if importe is None:
            continue
        filas.append({
            "fecha": _fmt_fecha(d.get("fecha")),
            "comprobante": _str_or_empty(d.get("comprobante_ref") or d.get("comprobante")),
            "importe": importe,
        })
    return filas


def _grupos_conceptos(fac: dict[str, Any]) -> list[dict[str, Any]]:
    bloques = [
        ("CONSUMO AGUA", "subtotal_consumo_agua"),
        ("OTROS CONCEPTOS", "subtotal_otros_conceptos"),
        ("IVA E IMPUESTOS", "subtotal_iva_impuestos"),
    ]
    if _importe_valido(fac.get("subtotal_mora")):
        bloques.append(("OTROS", "subtotal_mora"))

    grupos: list[dict[str, Any]] = []
    for nombre, campo in bloques:
        importe = _float_or_none(fac.get(campo))
        if importe is None:
            continue
        grupos.append({
            "nombre": nombre,
            "subtotal": importe,
            "lineas": [{
                "descripcion": f"Total {nombre}",
                "cantidad": None,
                "periodo": None,
                "precio": None,
                "importe": importe,
            }],
        })
    return grupos


def _medidos(fac: dict[str, Any]) -> list[dict[str, Any]]:
    if not any(fac.get(k) for k in (
        "nro_medidor", "fecha_lect_ant", "lectura_anterior", "consumo_m3",
    )):
        return []
    cat = fac.get("categoria_tarifa") or fac.get("cat") or ""
    try:
        cat_val: int | str = int(cat)
    except (TypeError, ValueError):
        cat_val = cat or "—"

    return [{
        "servicio": "Agua",
        "cat": cat_val,
        "medidor": _str_or_empty(fac.get("nro_medidor")),
        "fec_ant": _fmt_fecha(fac.get("fecha_lect_ant")),
        "est_ant": _float_or_none(fac.get("lectura_anterior")) or 0.0,
        "fec_act": _fmt_fecha(fac.get("fecha_lect_act")),
        "est_act": _float_or_none(fac.get("lectura_actual")) or 0.0,
        "consumo": _float_or_none(fac.get("consumo_m3")) or 0.0,
        "dias": fac.get("dias_periodo") or "",
        "periodo": _str_or_empty(fac.get("periodo_consumo") or fac.get("periodo")),
    }]


def _consumos_grafico(historial: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"periodo": str(h.get("periodo") or ""), "valor": float(h.get("consumo") or 0)}
        for h in historial
        if h.get("periodo")
    ]


def escala_grafico_consumo(valores: list[float]) -> dict[str, Any]:
    """
    Techo del eje Y y marcas según el máximo del histórico (como boleta Fox).
    Ej.: max ~15 → 0–15 paso 5; max ~102 → 0–120 paso 30.
    """
    if not valores:
        return {"y_max": 15.0, "ticks": [0, 5, 10, 15]}

    max_v = max(max(0.0, float(v)) for v in valores)
    if max_v <= 0:
        return {"y_max": 15.0, "ticks": [0, 5, 10, 15]}

    objetivo = max_v * 1.08

    if objetivo <= 20:
        paso = 5
        y_max = int(math.ceil(objetivo / paso) * paso)
        if y_max < 15:
            y_max = 15
    elif objetivo <= 55:
        paso = 10
        y_max = int(math.ceil(objetivo / paso) * paso)
    elif objetivo <= 130:
        paso = 30
        y_max = int(math.ceil(objetivo / paso) * paso)
    else:
        paso = 50
        y_max = int(math.ceil(objetivo / paso) * paso)
        if y_max > 150:
            y_max = int(math.ceil(objetivo / 50) * 50)

    ticks = list(range(0, y_max + 1, paso))
    if not ticks or ticks[-1] != y_max:
        ticks.append(y_max)
    return {"y_max": float(y_max), "ticks": ticks}


def _grafico_escala_desde_consumos(consumos: list[dict[str, Any]]) -> dict[str, Any]:
    valores = [float(c.get("valor") or 0) for c in consumos]
    return escala_grafico_consumo(valores)


def fac_sintetico_desde_datos(datos: dict[str, Any]) -> dict[str, Any]:
    """Reconstruye fila tipo DB para codigos_con_barras en modo --muestra."""
    fac = datos.get("fac") or {}
    socio = datos.get("socio") or {}
    nro = f"{fac.get('suc_com_str', '')}{fac.get('num_com_str', '')}"
    return {
        "tipo_comprobante": fac.get("tipo_titulo"),
        "nro_comprobante": nro,
        "fecha_emision": fac.get("fecha_str"),
        "codigo_afip": fac.get("cod_afip"),
        "periodo": fac.get("periodo"),
        "prox_vencimiento": fac.get("proximo_vto"),
        "vencimiento": fac.get("proximo_vto"),
        "cesp": fac.get("cesp"),
        "total": fac.get("total"),
        "suministro": socio.get("suministro"),
        "codbarPF": fac.get("cod_barras_pf"),
        "codbarNac": fac.get("codbarNac"),
        "codbarCoop": fac.get("codbarCoop"),
        "archivo": fac.get("archivo"),
    }


def enriquecer_presentacion(
    datos: dict[str, Any],
    fac_db: dict[str, Any] | None = None,
    *,
    cod_nac: str | None = None,
    cod_pfs: str | None = None,
    cod_cesp: str | None = None,
) -> dict[str, Any]:
    datos["logo_uri"] = logo_data_uri()
    fac_for_codes = fac_db if fac_db is not None else fac_sintetico_desde_datos(datos)
    codigos = codigos_con_barras(
        fac_for_codes,
        cod_nac=cod_nac,
        cod_pfs=cod_pfs,
        cod_cesp=cod_cesp,
    )
    datos["codigos"] = codigos
    pf = codigos.get("pfs") or codigos.get("link") or datos["fac"].get("cod_barras_pf", "")
    if pf:
        datos["fac"]["cod_barras_pf"] = pf
    consumos = datos.get("consumos_grafico") or []
    datos["grafico_escala"] = _grafico_escala_desde_consumos(consumos)
    return datos


def datos_desde_db(
    data: dict[str, Any],
    *,
    cod_nac: str | None = None,
    cod_pfs: str | None = None,
    cod_cesp: str | None = None,
) -> dict[str, Any]:
    fac = data["factura"]
    historial = data.get("historial_consumo") or []
    items = data.get("items") or []
    deudas_db = data.get("deudas_anteriores") or []

    suc, num = _parse_nro_comprobante(fac.get("nro_comprobante"))
    deuda_total = _float_or_none(fac.get("deuda_total_anterior")) or 0.0
    prox_vto = _fmt_fecha(fac.get("prox_vencimiento"))
    vencimiento = _fmt_fecha(fac.get("vencimiento"))
    if not prox_vto:
        prox_vto = vencimiento
    if not vencimiento:
        vencimiento = prox_vto

    fac_out: dict[str, Any] = {
        "tipo_titulo": _tipo_titulo(fac),
        "suc_com_str": suc,
        "num_com_str": num,
        "fecha_str": _fmt_fecha(fac.get("fecha_emision")),
        "cod_afip": _str_or_empty(fac.get("codigo_afip")),
        "periodo": _str_or_empty(fac.get("periodo")),
        "proximo_vto": prox_vto,
        "vencimiento": vencimiento,
        "cesp": _str_or_empty(fac.get("cesp")),
        "total": _float_or_none(fac.get("total")) or 0.0,
        "iva_total": _float_or_none(fac.get("subtotal_iva_impuestos")) or 0.0,
        "otros_imp": 0.0,
        "cod_barras_pf": "",
    }

    comp_rel = fac.get("comprobante_relacionado") or fac.get("comp_relacionado")
    if comp_rel:
        fac_out["comp_relacionado"] = {
            "descripcion": _str_or_empty(comp_rel),
            "importe": _float_or_none(fac.get("importe_comp_relacionado")) or 0.0,
        }

    resultado = {
        "fac": fac_out,
        "socio": {
            "nombre": _str_or_empty(fac.get("nombre_socio")),
            "codigo": _str_or_empty(fac.get("nro_socio")),
            "direccion": _direccion_socio(fac),
            "suministro": _str_or_empty(fac.get("suministro")),
            "zona": _str_or_empty(fac.get("zona")),
            "sector": _str_or_empty(fac.get("sector")),
            "ruta": _str_or_empty(fac.get("ruta")),
            "tipo_consumidor": _str_or_empty(fac.get("condicion_iva")),
            "categoria": _categoria_socio(fac),
            "cuit": _str_or_empty(fac.get("cuit_cliente")),
            "ubicacion": _str_or_empty(fac.get("ubicacion")),
        },
        "consumos_grafico": _consumos_grafico(historial),
        "medidos": _medidos(fac),
        "grupos_conceptos": _grupos_desde_items(fac, items),
        "deudas": _deudas_desde_db(deudas_db),
        "deudas_mas": 0,
        "deuda_total": deuda_total,
        "tarifa": {
            "fecha_asamblea": FECHA_ASAMBLEA,
            "cargo_fijo": CARGO_FIJO,
            "rangos": RANGOS_M3,
        },
    }

    return enriquecer_presentacion(
        resultado,
        fac_db=fac,
        cod_nac=cod_nac,
        cod_pfs=cod_pfs,
        cod_cesp=cod_cesp,
    )
