"""
Códigos de pago — patrones extraídos de PDFs Merlos (sin depender de FoxPro).

Estructuras confirmadas en boletas:
  CESP talón:  prefijo_AFIP(2) + PV_impreso(4) + nro_impreso(8)   [prefijo = 79 + codigo_afip]
  LINK:        376 + suministro(8)
  Banco Nación: 2699 + 4120 + nro_interno(8) + importe(7) + DV(1)
  Pago Fácil:  833 + saldo(8) + venc/código(8) + medio(14) + rep.importe(8|10) + cola(6)

  Layout “equipo” (1-based, ej. 03/2026): ver armar_pfs_equipo / partes_desde_pfs_ref.
  Layout “legacy” (0-based, ej. 04/2026): 833 + saldo8 + venc8 + medio14 + saldo8 + cola6.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterator

ENTE_BANCO_NACION = 2699
ENTE_PAGO_FACIL = 833
LINK_PREFIJO = "376"
PV_FACTURADOR = "4120"  # PV interno BNA/PFS (≠ 0006 impreso en talón)
PV_IMPRESO_DEFAULT = "0006"
PFS_BLOQUE_MEDIO = "00000020"
# Posición fija del bloque medio en PFS de 47 dígitos: 833(3) + saldo(8) + venc(8)
PFS_OFFSET_MEDIO = 3 + 8 + 8

# Vencimiento numérico Pago Fácil (≠ fecha del talón); ampliar al calibrar períodos
VENC_PFS_POR_PERIODO: dict[str, str] = {
    "02/2026": "26072410",
    "03/2026": "26105410",
    "04/2026": "26134410",
}

# Períodos que usan el armado PFS documentado por el equipo (rep. importe 10 dígitos)
PERIODO_PFS_LAYOUT_EQUIPO: frozenset[str] = frozenset({"03/2026"})

_CAMPOS_BARRA_DB = (
    # Tabla facturas (Fox / importación local)
    ("codbarNac", "nac"),
    ("codbarPF", "pfs"),
    ("codbarPFauto", "pfs"),
    ("codbarCoop", "cesp"),
    # socio_facturas / variantes legacy
    ("cod_bar_nac", "nac"),
    ("codBarNac", "nac"),
    ("cod_bar_pfs", "pfs"),
    ("codBarPFS", "pfs"),
    ("cod_bar_pf", "pfs"),
    ("codBarPf", "pfs"),
    ("cod_fac", "cod_fac"),
    ("codigo_fac", "cod_fac"),
)


def solo_digitos(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def importe_centavos(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def prefijo_afip(codigo_afip: Any) -> str:
    """Código AFIP 18 → 97, 17 → 96 (regla 79 + codigo_afip)."""
    d = solo_digitos(codigo_afip)
    if not d:
        return "97"
    cod = int(d)
    if cod <= 0:
        return "97"
    return str(79 + cod)


def parse_fecha_digitos(value: Any) -> dict[str, str]:
    s = str(value or "").strip()
    digits = solo_digitos(s)
    out: dict[str, str] = {}
    if len(digits) >= 8:
        if len(digits) == 8:
            if digits[:2] in ("19", "20"):
                out["yyyymmdd"] = digits
                out["ddmmyyyy"] = digits[6:8] + digits[4:6] + digits[0:4]
                out["ddmmyy"] = digits[6:8] + digits[4:6] + digits[2:4]
            else:
                out["ddmmyyyy"] = digits
                out["yyyymmdd"] = digits[4:8] + digits[2:4] + digits[0:2]
                out["ddmmyy"] = digits[0:2] + digits[2:4] + digits[6:8]
    elif len(digits) == 6:
        out["ddmmyy"] = digits
        out["ddmmyyyy"] = digits[0:2] + digits[2:4] + "20" + digits[4:6]
    return out


def digito_mod10_simple(cadena: str) -> str:
    total = sum(int(c) for c in cadena if c.isdigit())
    return str(total % 10)


def digito_mod10_peso13(cadena: str) -> str:
    pesos = [1, 3]
    s = 0
    rev = [int(c) for c in reversed(cadena) if c.isdigit()]
    for i, d in enumerate(rev):
        x = d * pesos[i % 2]
        s += x // 10 + x % 10
    return str((10 - s % 10) % 10)


def checksum6(cadena: str) -> str:
    acc = 0
    for i, c in enumerate(cadena):
        if c.isdigit():
            acc += int(c) * ((i % 7) + 1)
    return str(acc % 1_000_000).zfill(6)


def importes_nacion_8(centavos: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    z8 = str(centavos).zfill(8)
    out.append(("zfill8", z8))
    s7 = str(centavos).zfill(7)
    out.append(("7peso13", s7 + digito_mod10_peso13(s7)))
    out.append(("7mod10", s7 + digito_mod10_simple(s7)))
    return out


def digito_verificador_nacion(s7: str, estilo: str = "auto") -> str:
    """DV del bloque importe BNA (7 dígitos + 1 DV = 8 en el código de 22)."""
    algos: list[tuple[str, Callable[[str], str]]] = [
        ("7peso13", digito_mod10_peso13),
        ("7mod10", digito_mod10_simple),
    ]
    if estilo != "auto":
        for name, fn in algos:
            if name == estilo:
                return fn(s7)
    for _, fn in algos:
        return fn(s7)
    return digito_mod10_peso13(s7)


def bloque_importe_nacion(
    centavos: int,
    estilo: str = "7peso13",
    nac_ref: str = "",
) -> str:
    """8 dígitos: centavos(7) + DV. Si nac_ref tiene 22 dígitos, usa su bloque [14:22]."""
    d = solo_digitos(nac_ref)
    if len(d) == 22:
        return d[14:22]
    s7 = str(centavos).zfill(7)
    return s7 + digito_verificador_nacion(s7, estilo)


def nro6_desde_interno(nro_interno: str) -> str:
    """Primeros 6 dígitos del NRO interno (bloque medio NAC/PFS)."""
    d = solo_digitos(nro_interno)
    if len(d) >= 8:
        return d[-8:][:6]
    if len(d) >= 6:
        return d[-6:].zfill(6)
    return d.zfill(6)


def bloque_medio_pfs(nro_interno: str, pfs_ref: str = "") -> str:
    """14 dígitos: 00000020 + nro6."""
    partes = partes_desde_pfs_ref(pfs_ref)
    if partes.get("medio14"):
        return partes["medio14"]
    return f"{PFS_BLOQUE_MEDIO}{nro6_desde_interno(nro_interno)}"


def _stem_archivo(fac: dict[str, Any]) -> str:
    archivo = str(fac.get("archivo") or "")
    return solo_digitos(archivo.split(".")[0].split("-")[0])


def _suffix_stem(fac: dict[str, Any]) -> tuple[str, str]:
    """Parte del stem tras el suministro (ej. 0097000600732603)."""
    stem = _stem_archivo(fac)
    suministro = solo_digitos(fac.get("suministro"))
    if not stem or not suministro:
        return stem, ""
    for pref in (suministro, suministro.zfill(6), "0" + suministro.zfill(5)):
        if pref and stem.startswith(pref):
            return stem[len(pref) :], pref
    return stem, ""


def cesp_desde_archivo(fac: dict[str, Any]) -> str:
    """
    CESP talón (14 dígitos) embebido en el nombre de archivo Fox.
    Ej. 0001010097000600752377-202604.pdf → 97000600752377
    """
    stem = _stem_archivo(fac)
    if len(stem) < 14:
        return ""
    for m in re.finditer(r"97\d{12}", stem):
        cand = m.group(0)
        if cand[2:6] == PV_IMPRESO_DEFAULT:
            return cand
    return ""


def extraer_partes_impresas(fac: dict[str, Any]) -> dict[str, str]:
    """
    PV y número impresos en el talón (ej. N° 0006 00732603).
    Prioridad: nro_comprobante 12 dígitos → suffix del archivo → defaults.
    """
    codigo_afip = fac.get("codigo_afip")
    prefijo = prefijo_afip(codigo_afip)
    pv = PV_IMPRESO_DEFAULT
    nro = ""

    nro_long = solo_digitos(fac.get("nro_comprobante"))
    if len(nro_long) >= 12:
        pv = nro_long[-12:-8].zfill(4)
        nro = nro_long[-8:].zfill(8)
    elif len(nro_long) >= 8:
        nro = nro_long[-8:].zfill(8)

    if not nro:
        suffix, _ = _suffix_stem(fac)
        if len(suffix) >= 16:
            nro = suffix[-8:].zfill(8)
            pv = suffix[-12:-8].zfill(4)
        elif len(suffix) >= 14:
            nro = suffix[-8:].zfill(8)
            pv = suffix[-12:-8].zfill(4)

    if not nro:
        nro, _ = extraer_comprobante_8(fac)

    return {
        "prefijo_afip": prefijo,
        "pv_impreso": pv,
        "nro_impreso": nro.zfill(8) if nro else "",
        "codigo_afip": solo_digitos(codigo_afip),
    }


def extraer_comprobante_8(fac: dict[str, Any]) -> tuple[str, str]:
    partes = extraer_partes_impresas(fac)
    if partes["nro_impreso"]:
        src = "partes_impresas"
        if _stem_archivo(fac):
            src = "archivo_suffix"
        return partes["nro_impreso"], src

    stem = _stem_archivo(fac)
    if len(stem) >= 8:
        return stem[-8:].zfill(8), "archivo_ultimos8"

    nro = solo_digitos(fac.get("nro_comprobante"))
    if len(nro) >= 8:
        return nro[-8:].zfill(8), "nro_comprobante_ultimos8"
    if nro:
        return nro.zfill(8), "nro_comprobante_relleno"
    return "", ""


def build_cesp_talon(fac: dict[str, Any]) -> str:
    """CESP cooperativa: prefijo_AFIP(2) + PV_impreso(4) + nro(8)."""
    p = extraer_partes_impresas(fac)
    if not p["nro_impreso"]:
        return ""
    return f"{p['prefijo_afip']}{p['pv_impreso'].zfill(4)}{p['nro_impreso'].zfill(8)}"


def build_cesp_barra(comprobante_8: str, codigo_afip: Any = None, pv_impreso: str = PV_IMPRESO_DEFAULT) -> str:
    """Compatibilidad: arma CESP si ya tenés el nro de 8 dígitos."""
    pref = prefijo_afip(codigo_afip) if codigo_afip is not None else "97"
    return f"{pref}{str(pv_impreso).zfill(4)}{comprobante_8.zfill(8)}"


def build_link(suministro: Any) -> str:
    return f"{LINK_PREFIJO}{solo_digitos(suministro).zfill(8)}"


def build_nac(
    nro_interno: str,
    centavos: int,
    estilo_imp: str = "7peso13",
    nac_ref: str = "",
) -> str:
    """
    Banco Nación — 22 dígitos en PDF Merlos:
      2699 + 4120 + nro_interno[:6] + importe(7) + DV(1)
    """
    nro6 = nro6_desde_interno(nro_interno)
    imp8 = bloque_importe_nacion(centavos, estilo_imp, nac_ref=nac_ref)
    return f"{ENTE_BANCO_NACION:04d}{PV_FACTURADOR}{nro6}{imp8}"


def vencimiento_pfs(periodo: str | None, fac: dict[str, Any] | None = None) -> str:
    periodo_norm = (periodo or "").strip()
    if periodo_norm in VENC_PFS_POR_PERIODO:
        return VENC_PFS_POR_PERIODO[periodo_norm]
    if fac:
        for fmt, val in parse_fecha_digitos(fac.get("vencimiento")).items():
            if len(val) >= 8:
                return val[-8:].zfill(8)
    return "00000000"


def _slice_pfs_1based(d: str, start: int, end: int) -> str:
    """Subcadena con posiciones 1-based inclusive (estilo layout equipo)."""
    return d[start - 1 : end]


def layout_pfs(d: str) -> str:
    """
    Detecta variante de armado en un PFS de 47 dígitos.
    - legacy: pos. 34-41 (0-based 33-40) repite saldo8 (8 dígitos)
    - equipo: pos. 31-40 (0-based 30-39) bloque de 10 dígitos (ej. 70+saldo8)
    """
    if len(d) < 40:
        return "legacy"
    saldo = d[3:11]
    bloque_rep = d[30:40]
    if len(bloque_rep) == 10 and bloque_rep != saldo:
        return "equipo"
    return "legacy"


def armar_pfs_legacy(saldo8: str, venc8: str, medio14: str, cola6: str) -> str:
    """PFS 47 dígitos — layout clásico (04/2026 y mayoría de cod_bar)."""
    s8 = saldo8.zfill(8)[-8:]
    v8 = venc8.zfill(8)[-8:]
    m14 = medio14.zfill(14)[-14:]
    c6 = cola6.zfill(6)[-6:]
    return f"{ENTE_PAGO_FACIL:03d}{s8}{v8}{m14}{s8}{c6}"


def armar_pfs_equipo(
    saldo8: str,
    venc6: str,
    tipo_cobro: str,
    nro6: str,
    *,
    importe_rep10: str = "",
    control5: str = "",
    dv_final: str = "",
    cola6: str = "",
) -> str:
    """
    PFS 47 dígitos — layout documentado por el equipo (pos. 1-based).

    Pos: 1-3 ente | 4-11 saldo | 12-17 venc6 | 18 tipo | 19-30 interno12 |
         31-40 importe_rep10 | 41-45 control5 | 46 dv_final

    interno12 = 00000020 (8) + primeros 4 dígitos de nro6.
    importe_rep10 por defecto = últimos 2 de nro6 + saldo8 (10 dígitos).
    cola6 alternativa = control5 (5) + dv_final (1).
    """
    s8 = saldo8.zfill(8)[-8:]
    v6 = venc6.zfill(6)[-6:]
    tipo = str(tipo_cobro or "1")[:1]
    n6 = solo_digitos(nro6).zfill(6)[-6:]
    interno12 = (PFS_BLOQUE_MEDIO + n6[:4])[:12]
    rep10 = importe_rep10 or (n6[-2:] + s8)
    rep10 = rep10.zfill(10)[-10:]
    if cola6:
        c5 = cola6.zfill(6)[:5]
        dv = cola6.zfill(6)[5:6] or "0"
    else:
        c5 = control5.zfill(5)[-5:]
        dv = str(dv_final or "0")[:1]
    return f"{ENTE_PAGO_FACIL:03d}{s8}{v6}{tipo}{interno12}{rep10}{c5}{dv}"


def armar_pfs(
    saldo8: str,
    venc8: str,
    medio14: str,
    cola6: str,
    *,
    nro6: str = "",
    periodo: str = "",
    layout: str = "auto",
) -> str:
    """Ensambla PFS; elige layout legacy o equipo según período / detección."""
    layout_eff = layout
    if layout_eff == "auto":
        layout_eff = "equipo" if (periodo or "").strip() in PERIODO_PFS_LAYOUT_EQUIPO else "legacy"
    if layout_eff == "equipo" and nro6:
        v8 = venc8.zfill(8)[-8:]
        return armar_pfs_equipo(
            saldo8,
            v8[:6],
            v8[6:7] if len(v8) > 6 else "1",
            nro6,
            cola6=cola6,
        )
    return armar_pfs_legacy(saldo8, venc8, medio14, cola6)


def build_pfs(
    nro_interno: str,
    centavos: int,
    venc8: str,
    *,
    cola6: str | None = None,
    pfs_ref: str = "",
    periodo: str = "",
) -> str:
    ref = partes_desde_pfs_ref(pfs_ref) if pfs_ref else {}
    if ref.get("saldo8") and ref.get("medio14"):
        cola = cola6 if cola6 is not None else ref.get("cola6", "000000")
        v = ref["venc8"] or (venc8 or "00000000")[-8:].zfill(8)
        ly = ref.get("layout", "auto")
        n6 = ref.get("nro6") or nro6_desde_interno(nro_interno)
        return armar_pfs(
            ref["saldo8"], v, ref["medio14"], cola,
            nro6=n6, periodo=periodo, layout=ly,
        )

    saldo8 = str(centavos).zfill(8)
    v = (venc8 or "00000000")[-8:].zfill(8)
    medio = bloque_medio_pfs(nro_interno, pfs_ref)
    cola = cola6 if cola6 is not None else checksum6(
        f"{ENTE_PAGO_FACIL:03d}{saldo8}{v}{medio}{saldo8}"
    )
    n6 = nro6_desde_interno(nro_interno)
    return armar_pfs(saldo8, v, medio, cola, nro6=n6, periodo=periodo)


def parse_nac_esperado(nac: str) -> dict[str, str]:
    """Despiece de NAC del PDF (22 dígitos Merlos)."""
    d = solo_digitos(nac)
    out = {"nro6": "", "nro_interno": "", "imp8": "", "raw": d}
    if len(d) == 22:
        out["nro6"] = d[8:14]
        out["imp8"] = d[14:22]
        # Los 2 primeros dígitos del importe suelen cerrar el nro interno de 8
        out["nro_interno"] = (out["nro6"] + out["imp8"][:2]).zfill(8)[-8:]
    elif len(d) >= 24:
        out["nro_interno"] = d[8:16]
        out["nro6"] = d[8:14]
        out["imp8"] = d[16:24] if len(d) >= 24 else ""
    return out


def nro_interno_desde_nac(nac: str) -> str:
    """NRO interno de 8 dígitos a partir del NAC del PDF."""
    return parse_nac_esperado(nac).get("nro_interno", "")


def partes_desde_pfs_esperado(pfs: str) -> dict[str, str]:
    """Extrae cola(6) y nro6 del bloque medio de un PFS del PDF."""
    ref = partes_desde_pfs_ref(pfs)
    out: dict[str, str] = {"cola6": ref.get("cola6", ""), "nro6": ""}
    medio = ref.get("medio14", "")
    if len(medio) >= 14:
        out["nro6"] = medio[-6:]
    return out


def ventanas_8(cadena: str) -> list[tuple[str, int]]:
    d = solo_digitos(cadena)
    return [(d[i : i + 8], i) for i in range(max(0, len(d) - 7))]


def candidatos_cola_pfs(cuerpo: str, saldo8: str, venc8: str, centavos: int) -> list[tuple[str, str]]:
    """Variantes de cola de 6 dígitos para Pago Fácil."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(label: str, val: str) -> None:
        c = val.zfill(6)[-6:]
        if c not in seen:
            seen.add(c)
            out.append((label, c))

    add("checksum6", checksum6(cuerpo + saldo8))
    add("checksum6_solo_cuerpo", checksum6(cuerpo))
    add("venc6_cent2", venc8[:6] + str(centavos % 100).zfill(2))
    add("venc6_mod", venc8[:6] + str((centavos // 10000) % 100).zfill(2))
    add("mod_centavos", str(centavos % 1_000_000).zfill(6))
    add("venc8_last6", venc8[-6:])
    return out


def candidatos_nro_interno(fac: dict[str, Any]) -> list[tuple[str, str]]:
    """
    Posibles NRO internos (8 dígitos) para BNA/PFS.
    Excluye el nro impreso en talón salvo que venga con PV 4120 en cod_fac.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    partes = extraer_partes_impresas(fac)
    nro_impreso = partes["nro_impreso"]
    suministro = solo_digitos(fac.get("suministro"))

    def add(nro: str, label: str, priority: int = 50) -> None:
        n = nro.zfill(8)[-8:]
        if not n.isdigit() or n in seen:
            return
        if n == nro_impreso and "pv4120" not in label:
            return
        if suministro and n == suministro.zfill(8)[-8:]:
            return
        seen.add(n)
        out.append((priority, n, label))

    for field in ("cod_fac", "codigo_fac", "Cod_Fac", "COD_FAC"):
        raw = solo_digitos(fac.get(field))
        if len(raw) >= 12:
            pv, nro = raw[-12:-8], raw[-8:]
            if pv == PV_FACTURADOR:
                add(nro, f"db_{field}_pv4120", 0)
            else:
                add(nro, f"db_{field}", 10)

    variantes = variantes_cod_fac(
        fac.get("nro_comprobante"), fac.get("archivo"), fac
    )
    orden = (
        "db_cod_fac",
        "nro_comprobante_seg_10_18",
        "archivo_stem_seg_10_18",
        "nro_comprobante_seg_8_16",
        "archivo_stem_seg_8_16",
    )
    for name in orden:
        if name not in variantes:
            continue
        parts = variantes[name]
        pri = orden.index(name)
        if parts["pv"] == PV_FACTURADOR:
            add(parts["nro"], name + "_pv4120", pri)
        elif parts["pv"] not in ("0102", "0100", "0000"):
            add(parts["nro"], name, pri + 20)

    for name, parts in variantes.items():
        if parts["pv"] == PV_FACTURADOR:
            add(parts["nro"], name + "_pv4120", 15)

    stem = _stem_archivo(fac)
    nro_db = solo_digitos(fac.get("nro_comprobante"))
    for fuente, cadena in (("stem", stem), ("nro_db", nro_db)):
        for win, pos in ventanas_8(cadena):
            add(win, f"{fuente}_win{pos:02d}", 40)

    if not out:
        add("00000000", "default", 99)

    out.sort(key=lambda x: x[0])
    return [(n, lbl) for _, n, lbl in out]


def elegir_nro_interno(fac: dict[str, Any], nac_esperado: str = "") -> tuple[str, str]:
    if nac_esperado:
        n = nro_interno_desde_nac(nac_esperado)
        if n:
            return n, "desde_nac_esperado"
    candidatos = candidatos_nro_interno(fac)
    if candidatos:
        return candidatos[0]
    return "00000000", "default"


def _partes_fox12(digits12: str) -> dict[str, str]:
    d = digits12[-12:].zfill(12)
    return {"pv": d[:4], "nro": d[-8:], "cod_fac_12": d}


def variantes_cod_fac(
    nro_comprobante: Any,
    archivo: Any = None,
    fac: dict[str, Any] | None = None,
) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    fac = fac or {}

    for field in ("cod_fac", "codigo_fac", "Cod_Fac", "COD_FAC"):
        raw = solo_digitos(fac.get(field))
        if len(raw) >= 12:
            out["db_cod_fac"] = _partes_fox12(raw)

    candidatos: list[tuple[str, str]] = []
    nro_d = solo_digitos(nro_comprobante)
    if nro_d:
        candidatos.append(("nro_comprobante", nro_d))

    arch = str(archivo or "")
    stem = arch.split(".")[0].split("-")[0] if arch else ""
    arch_d = solo_digitos(stem)
    if arch_d and arch_d != nro_d:
        candidatos.append(("archivo_stem", arch_d))

    for label, digits in candidatos:
        if len(digits) >= 12:
            out[f"{label}_fox12"] = _partes_fox12(digits)
            d12 = digits[:12].zfill(12)
            out[f"{label}_fox12_cabecera"] = {
                "pv": d12[:4],
                "nro": d12[4:12],
                "cod_fac_12": d12,
            }
        if len(digits) >= 20:
            out[f"{label}_seg_8_16"] = {
                "pv": digits[8:12],
                "nro": digits[12:20],
                "cod_fac_12": digits[8:20],
            }
            out[f"{label}_seg_10_18"] = {
                "pv": digits[10:14],
                "nro": digits[14:22] if len(digits) >= 22 else digits[14:].zfill(8)[-8:],
                "cod_fac_12": (digits[10:22] if len(digits) >= 22 else digits[10:] + "0" * 12)[:12],
            }
        for i in range(0, max(0, len(digits) - 11)):
            chunk = digits[i : i + 12]
            if len(chunk) == 12:
                out[f"{label}_win{i:02d}"] = _partes_fox12(chunk)

    return out


def _importe_para_codigo(fac: dict[str, Any]) -> tuple[int | None, str]:
    """Para códigos de barras usar total de la boleta (saldo en DB puede ser parcial)."""
    for campo in ("total", "Total", "saldo", "Saldo"):
        c = importe_centavos(fac.get(campo))
        if c is not None:
            return c, campo
    return None, ""


def partes_desde_pfs_ref(pfs_ref: str) -> dict[str, str]:
    """Despiece de PFS de 47 dígitos (PDF / cod_bar). Incluye layout legacy y equipo (1-based)."""
    d = solo_digitos(pfs_ref)
    out: dict[str, str] = {
        "saldo8": "",
        "venc8": "",
        "medio14": "",
        "cola6": "",
        "nro6": "",
        "layout": "",
        "len": str(len(d)),
        # Campos layout equipo (pos. 1-based)
        "ente": "",
        "venc6": "",
        "tipo_cobro": "",
        "interno12": "",
        "importe_rep10": "",
        "control5": "",
        "dv_final": "",
    }
    if len(d) < 33:
        return out

    out["saldo8"] = d[3:11]
    out["venc8"] = d[11:19]
    out["layout"] = layout_pfs(d) if len(d) == 47 else "legacy"

    idx = d.find(PFS_BLOQUE_MEDIO, 11)
    if idx < 0:
        idx = PFS_OFFSET_MEDIO if len(d) >= PFS_OFFSET_MEDIO + 14 else -1
    if idx >= 0 and len(d) >= idx + 14:
        out["medio14"] = d[idx : idx + 14]
        out["nro6"] = out["medio14"][8:14]

    if len(d) >= 41:
        out["cola6"] = d[-6:]

    if len(d) == 47:
        out["ente"] = _slice_pfs_1based(d, 1, 3)
        out["venc6"] = _slice_pfs_1based(d, 12, 17)
        out["tipo_cobro"] = _slice_pfs_1based(d, 18, 18)
        out["interno12"] = _slice_pfs_1based(d, 19, 30)
        out["importe_rep10"] = _slice_pfs_1based(d, 31, 40)
        out["control5"] = _slice_pfs_1based(d, 41, 45)
        out["dv_final"] = _slice_pfs_1based(d, 46, 46)
        if not out["nro6"] and out["interno12"]:
            rep = out.get("importe_rep10", "")
            if len(rep) >= 2:
                out["nro6"] = (out["interno12"][-4:] + rep[:2]).zfill(6)[-6:]

    return out


def _codigos_desde_db(fac: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for col, key in _CAMPOS_BARRA_DB:
        val = solo_digitos(fac.get(col))
        if val and key not in out and key != "cod_fac":
            out[key] = val
    return out


def probar_combinaciones(
    fac: dict[str, Any],
    esperado_nac: str = "",
    esperado_pfs: str = "",
) -> dict[str, Any]:
    """
    Fuerza bruta de nro_interno × cola PFS; opcionalmente calibra con códigos del PDF.
    """
    imp, _ = _importe_para_codigo(fac)
    periodo = str(fac.get("periodo") or "").strip()
    venc8 = vencimiento_pfs(periodo, fac)
    resultados: dict[str, Any] = {
        "candidatos_nac": [],
        "candidatos_pfs": [],
        "mejor_nac": None,
        "mejor_pfs": None,
    }
    if imp is None:
        return resultados

    exp_nac = solo_digitos(esperado_nac)
    exp_pfs = solo_digitos(esperado_pfs)
    nac_parsed = parse_nac_esperado(exp_nac) if exp_nac else {}
    nro_objetivo = nac_parsed.get("nro_interno", "")
    pfs_parsed = partes_desde_pfs_esperado(exp_pfs) if exp_pfs else {}

    candidatos: list[tuple[str, str]] = []
    if nro_objetivo:
        candidatos.append((nro_objetivo, "desde_nac_esperado"))
    if pfs_parsed.get("nro6"):
        n8 = (pfs_parsed["nro6"] + (nac_parsed.get("imp8", "")[:2])).zfill(8)[-8:]
        if n8 not in {c[0] for c in candidatos}:
            candidatos.append((n8, "desde_pfs_esperado"))
    for item in candidatos_nro_interno(fac):
        if item not in candidatos:
            candidatos.append(item)

    for nro_int, label in candidatos:
        for estilo in ("7peso13", "7mod10"):
            nac = build_nac(nro_int, imp, estilo, nac_ref=exp_nac)
            cmp = comparar(exp_nac, nac) if exp_nac else ""
            entry = {"nro_interno": nro_int, "variante": label, "estilo_imp": estilo, "valor": nac, "cmp": cmp}
            resultados["candidatos_nac"].append(entry)
            if cmp == "OK" and not resultados["mejor_nac"]:
                resultados["mejor_nac"] = entry
            if nro_objetivo and nro_int == nro_objetivo and not resultados["mejor_nac"]:
                resultados["mejor_nac"] = entry

        saldo8 = str(imp).zfill(8)
        medio = f"{PFS_BLOQUE_MEDIO}{nro6_desde_interno(nro_int)}"
        cuerpo = f"{ENTE_PAGO_FACIL:03d}{saldo8}{venc8}{medio}"
        colas = candidatos_cola_pfs(cuerpo, saldo8, venc8, imp)
        if pfs_parsed.get("cola6"):
            colas.insert(0, ("desde_pdf", pfs_parsed["cola6"]))
        for cola_label, cola6 in colas:
            pfs = build_pfs(
                nro_int, imp, venc8, cola6=cola6, pfs_ref=exp_pfs, periodo=periodo
            )
            cmp = comparar(exp_pfs, pfs) if exp_pfs else ""
            entry = {
                "nro_interno": nro_int,
                "variante": label,
                "cola": cola_label,
                "cola6": cola6,
                "valor": pfs,
                "cmp": cmp,
            }
            resultados["candidatos_pfs"].append(entry)
            if cmp == "OK" and not resultados["mejor_pfs"]:
                resultados["mejor_pfs"] = entry

    if exp_nac and not resultados["mejor_nac"]:
        ranked = sorted(
            resultados["candidatos_nac"],
            key=lambda e: (0 if e["cmp"] == "OK" else 1 if e["cmp"] == "PARCIAL" else 2),
        )
        if ranked:
            resultados["mejor_nac"] = ranked[0]

    if exp_pfs and not resultados["mejor_pfs"]:
        ranked = sorted(
            resultados["candidatos_pfs"],
            key=lambda e: (0 if e["cmp"] == "OK" else 1 if e["cmp"] == "PARCIAL" else 2),
        )
        if ranked:
            resultados["mejor_pfs"] = ranked[0]

    return resultados


# --- Aliases / compatibilidad ---

def build_nac_v2(pv: str, nro: str, centavos: int, estilo_imp: str = "7peso13") -> str:
    imp = bloque_importe_nacion(centavos, estilo_imp)
    return f"{ENTE_BANCO_NACION:04d}{pv}{nro}{imp}"


def build_pfs_v2(pv: str, nro: str, centavos: int, venc8: str) -> str:
    return build_pfs(nro, centavos, venc8, pfs_ref="")


def build_nac_tentativo(centavos: int, nro_interno: str = "00000000") -> str:
    return build_nac(nro_interno, centavos, nac_ref="")


def build_pfs_tentativo(
    centavos: int,
    comprobante_8: str,
    periodo: str | None = None,
    venc8: str | None = None,
) -> str:
    periodo_norm = (periodo or "").strip()
    v = venc8 or vencimiento_pfs(periodo_norm)
    return build_pfs(comprobante_8, centavos, v, pfs_ref="")


def build_cesp_v2(nro: str, suministro: Any = None) -> str:
    return build_cesp_barra(nro[-8:].zfill(8) if nro else "")


def resumen_codigos_pago(
    fac: dict[str, Any],
    nac_esperado: str = "",
    pfs_esperado: str = "",
) -> dict[str, str]:
    out: dict[str, str] = {}
    desde_db = _codigos_desde_db(fac)
    if desde_db.get("nac"):
        out["nac_db"] = desde_db["nac"]
    if desde_db.get("pfs"):
        out["pfs_db"] = desde_db["pfs"]

    partes = extraer_partes_impresas(fac)
    out["_prefijo_afip"] = partes["prefijo_afip"]
    out["_pv_impreso"] = partes["pv_impreso"]
    out["_codigo_afip"] = partes["codigo_afip"]

    comp8, comp_src = extraer_comprobante_8(fac)
    if comp8:
        out["comprobante"] = comp8
        out["_comprobante_desde"] = comp_src

    cesp = build_cesp_talon(fac)
    if cesp:
        out["cesp"] = cesp

    suministro = fac.get("suministro")
    if suministro:
        out["link"] = build_link(suministro)

    imp, imp_src = _importe_para_codigo(fac)
    periodo = str(fac.get("periodo") or "").strip()
    venc_pfs_val = vencimiento_pfs(periodo, fac)

    nro_int, nro_src = elegir_nro_interno(fac, nac_esperado)
    pfs_partes = partes_desde_pfs_esperado(pfs_esperado) if pfs_esperado else {}
    if not nro_int or nro_int == "00000000":
        n6 = pfs_partes.get("nro6", "")
        if len(n6) == 6 and nac_esperado:
            nro_int = nro_interno_desde_nac(nac_esperado) or n6.zfill(8)
            nro_src = "desde_pfs_o_nac"

    out["_nro_interno"] = nro_int
    out["_nro_interno_desde"] = nro_src

    if imp is not None:
        out["saldo8"] = str(imp).zfill(8)
        out["_importe_centavos"] = str(imp)
        out["_importe_desde"] = imp_src

        cola = pfs_partes.get("cola6") or None
        out["nac"] = build_nac(nro_int, imp, nac_ref=nac_esperado)
        out["nac_tentativo"] = out["nac"]

        pr = partes_desde_pfs_ref(pfs_esperado) if pfs_esperado else {}
        if pr.get("saldo8") and pr.get("medio14"):
            out["pfs"] = armar_pfs(
                pr["saldo8"],
                pr["venc8"] or venc_pfs_val,
                pr["medio14"],
                cola or pr.get("cola6", "000000"),
                nro6=pr.get("nro6") or nro6_desde_interno(nro_int),
                periodo=periodo,
                layout=pr.get("layout", "auto"),
            )
            out["_pfs_desde"] = "pdf_partes"
        else:
            out["pfs"] = build_pfs(
                nro_int,
                imp,
                venc_pfs_val,
                cola6=cola,
                pfs_ref=pfs_esperado,
                periodo=periodo,
            )
            out["_pfs_desde"] = "calculado"
        out["pfs_tentativo"] = out["pfs"]

    cesp_db = solo_digitos(fac.get("cesp"))
    if cesp_db:
        out["_cesp_db"] = cesp_db

    out["_periodo"] = periodo
    out["_vencimiento_pfs"] = venc_pfs_val
    out["_archivo"] = str(fac.get("archivo") or "")
    out["_nro_comprobante_db"] = solo_digitos(fac.get("nro_comprobante"))
    out["_nota"] = (
        "cesp/link: patron PDF. nac/pfs: 2699+4120+nro_interno; "
        "nro_interno puede requerir calibracion (probar_patrones.py --nac/--pfs)."
    )
    return out


def generar_numericos(fac: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}

    def put(key: str, value: str) -> None:
        if value and value.isdigit():
            out[key] = value

    resumen = resumen_codigos_pago(fac)
    for k, v in resumen.items():
        if not k.startswith("_"):
            put(k, v)

    put("suministro", solo_digitos(fac.get("suministro")))
    put("nro_comprobante", solo_digitos(fac.get("nro_comprobante")))

    imp, _ = _importe_para_codigo(fac)
    if imp is None:
        return out

    put("importe_centavos", str(imp))
    periodo = str(fac.get("periodo") or "").strip()
    venc8 = vencimiento_pfs(periodo, fac)
    variantes = variantes_cod_fac(fac.get("nro_comprobante"), fac.get("archivo"), fac)

    for var_name, parts in variantes.items():
        pv, nro = parts["pv"], parts["nro"]
        for imp_label, imp8 in importes_nacion_8(imp):
            put(f"tentativo_nac_{var_name}_{imp_label}", f"{ENTE_BANCO_NACION:04d}{pv}{nro}{imp8}")
            put(f"tentativo_pfs_{var_name}_venc", build_pfs(nro, imp, venc8, pfs_ref=""))

    for nro_int, label in candidatos_nro_interno(fac):
        put(f"tentativo_nac_{label}_4120", build_nac(nro_int, imp))
        put(f"tentativo_pfs_{label}", build_pfs(nro_int, imp, venc8, pfs_ref=""))

    return out


def comparar(esperado: str, calculado: str) -> str:
    e, c = solo_digitos(esperado), solo_digitos(calculado)
    if not e:
        return ""
    if e == c:
        return "OK"
    if e in c or c in e:
        return "PARCIAL"
    n = min(len(e), len(c))
    for i in range(n):
        if e[i] != c[i]:
            return f"DIFF@{i+1}"
    if len(e) != len(c):
        return f"LEN{len(e)}vs{len(c)}"
    return "DIFF"
