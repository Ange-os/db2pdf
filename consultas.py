"""
consultas.py - Lee la base ERP (MySQL/MariaDB) y arma el dict para template.html.

Uso principal (suministro + período, como generar.py):
    from consultas import cargar_datos_completos
    datos = cargar_datos_completos("596575", "05/2026")

Por id_fac (generar2.py / pipeline ERP):
    from consultas import cargar_datos
    datos, fac_row = cargar_datos(2072656)
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import config
from db import FacturaNotFoundError, _sanitize_identifier, fetch_factura, get_connection
from tarifa_actual import CARGO_FIJO, FECHA_ASAMBLEA, RANGOS_M3


def grupo_de_item(des_pro: str, tipo: int) -> str:
    d = (des_pro or "").upper()
    if any(k in d for k in ("CARGO FIJO", "CONSUMO AGUA", "BONIFICACION", "REDONDEO")):
        return "CONSUMO AGUA"
    if any(k in d for k in ("BOMBEO", "COSTO OPERATIVO")):
        return "OTROS CONCEPTOS"
    if any(k in d for k in ("CANCELACION", "ANTICIPO")):
        return "OTROS"
    if any(k in d for k in ("INTERES", "MORA")):
        return "OTROS"
    if any(k in d for k in ("ALICUOTA", "IVA")):
        return "IVA E IMPUESTOS"
    return {1: "CONSUMO AGUA", 2: "CONSUMO AGUA",
            4: "IVA E IMPUESTOS", 5: "CONSUMO AGUA"}.get(tipo, f"GRUPO {tipo}")


def _sql_cabecera() -> str:
    tbl = _sanitize_identifier(config.TABLE_FACTURAS)
    return f"""
SELECT id_fac, archivo, nro_comprobante, codigo_afip, fecha_emision, periodo,
       prox_vencimiento, vencimiento, total, cesp, comp_relacionado,
       nombre_socio, nro_socio, cuit_cliente, condicion_iva,
       suministro, categoria, zona, sector, ruta,
       direccion_postal, ubicacion, nro_medidor,
       fecha_lect_ant, lectura_anterior, fecha_lect_act, lectura_actual,
       consumo_m3, dias_periodo,
       saldo, deuda_total_anterior, comprobantes_adeudados,
       codbarPF, codbarNac, codbarCoop, res_com, des_com
  FROM `{tbl}`
 WHERE id_fac = %s
"""


def _sql_items() -> str:
    tbl = _sanitize_identifier(config.TABLE_ITEMS_ERP)
    return f"""
SELECT item, tipo, cod_pro, des_pro, can_ite, pre_vsi, importe, periodo,
       ali_iva1, imp_iva1, ali_iva2, imp_iva2, ali_iva3, imp_iva3
  FROM `{tbl}`
 WHERE id_fac = %s
 ORDER BY item
"""


def _sql_consumos_grafico(n_periodos: int) -> str:
    tbl = _sanitize_identifier(config.TABLE_CONSUMOS)
    ph = ",".join(["%s"] * n_periodos)
    return f"""
SELECT periodo, consumo
  FROM `{tbl}`
 WHERE cod_sum = %s AND periodo IN ({ph})
 ORDER BY SUBSTR(periodo,4,4), SUBSTR(periodo,1,2)
"""


def _sql_cesp() -> str:
    tbl = _sanitize_identifier(config.TABLE_CESPAFIP)
    return f"""
SELECT vto_cesp, fec_ini, fec_fin
  FROM `{tbl}`
 WHERE cesp = %s
 LIMIT 1
"""


def _sql_deudas() -> str:
    tbl = _sanitize_identifier(config.TABLE_FACTURAS)
    return f"""
SELECT fecha_emision, res_com, nro_comprobante, saldo, suministro
  FROM `{tbl}`
 WHERE suministro = %s AND saldo > 0 AND id_fac != %s
 ORDER BY fecha_emision
"""


def _periodos_anteriores(periodo_str: str, n: int = 13) -> list[str]:
    try:
        mes, anio = periodo_str.split("/")
        m, a = int(mes), int(anio)
    except Exception:
        return []
    out: list[str] = []
    for _ in range(n):
        out.append(f"{m:02d}/{a}")
        m -= 1
        if m == 0:
            m = 12
            a -= 1
    return list(reversed(out))


def _fmt_periodo_corto(p: str) -> str:
    try:
        mes, anio = p.split("/")
        return f"{mes}/{anio[-2:]}"
    except Exception:
        return p


def _fmt_nrocomp(nro_12: str, suc_len: int = 4, num_len: int = 8) -> tuple[str, str]:
    s = (nro_12 or "").strip()
    if len(s) >= (suc_len + num_len):
        return s[:suc_len], s[suc_len:suc_len + num_len]
    return s[:suc_len], s[suc_len:]


def _fmt_fecha(v: Any) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, (datetime, date)):
        return v.strftime("%d/%m/%Y")
    return str(v)


def _fmt_comp(res: Any, nro_comp: Any) -> str:
    if not res or not nro_comp:
        return (res or "") + "-" + (nro_comp or "")
    suc, num = _fmt_nrocomp(str(nro_comp))
    return f"{res}-{suc}-{num}"


def _label_iva(des_pro: str, consumidor: str) -> str:
    m = re.search(r"(\d+(?:[.,]\d+)?)", des_pro or "")
    rate = float(m.group(1).replace(",", ".")) if m else 0.0
    return f"IVA {rate:.1f} % (*) {consumidor or 'CONSUMIDOR FINAL'}"


def _orden_linea(descripcion: str) -> int:
    d = (descripcion or "").upper()
    if "CARGO FIJO" in d:
        return 0
    if "BONIFICACION" in d:
        return 1
    if "REDONDEO" in d:
        return 3
    return 2


def normalizar_para_template(datos: dict[str, Any]) -> dict[str, Any]:
    """Alias de campos que espera template.html (vía mapear_factura / datos_muestra)."""
    fac = datos.get("fac") or {}
    venc_pago = fac.pop("vencimiento_pago", None)
    if venc_pago:
        fac["vencimiento"] = venc_pago
    elif not fac.get("vencimiento"):
        fac["vencimiento"] = fac.get("proximo_vto") or ""
    datos["fac"] = fac
    return datos


def _armar_datos(fac_row: dict[str, Any], cur: Any, id_fac: int) -> dict[str, Any]:
    suc, num = _fmt_nrocomp(fac_row.get("nro_comprobante") or "")
    des_com = (fac_row.get("des_com") or "").strip() or "LIQUIDACION"

    fac: dict[str, Any] = {
        "tipo_titulo": des_com,
        "suc_com_str": suc,
        "num_com_str": num,
        "fecha_str": _fmt_fecha(fac_row.get("fecha_emision")),
        "cod_afip": fac_row.get("codigo_afip") or "",
        "periodo": fac_row.get("periodo") or "",
        "proximo_vto": _fmt_fecha(fac_row.get("prox_vencimiento") or fac_row.get("vencimiento")),
        "vencimiento_pago": _fmt_fecha(fac_row.get("vencimiento") or fac_row.get("prox_vencimiento")),
        "cesp": fac_row.get("cesp") or "",
        "cesp_vto": "",
        "total": float(fac_row.get("total") or 0),
        "iva_total": 0.0,
        "otros_imp": 0.0,
        "cod_barras_pf": fac_row.get("codbarPF") or fac_row.get("codbarCoop") or "",
        "comp_relacionado": None,
    }
    if fac_row.get("comp_relacionado"):
        fac["comp_relacionado"] = {
            "descripcion": fac_row["comp_relacionado"],
            "importe": 0.00,
        }

    if fac["cesp"]:
        try:
            cur.execute(_sql_cesp(), (fac["cesp"],))
            cesp_row = cur.fetchone()
            if cesp_row:
                fac["cesp_vto"] = _fmt_fecha(cesp_row.get("vto_cesp"))
        except Exception:
            pass
    if not fac["cesp_vto"]:
        fac["cesp_vto"] = fac["vencimiento_pago"]

    socio = {
        "nombre": fac_row.get("nombre_socio") or "",
        "codigo": fac_row.get("nro_socio") or "",
        "direccion": fac_row.get("direccion_postal") or "",
        "suministro": fac_row.get("suministro") or "",
        "zona": fac_row.get("zona") or "",
        "sector": fac_row.get("sector") or "",
        "ruta": fac_row.get("ruta") or "",
        "tipo_consumidor": (fac_row.get("condicion_iva") or "").upper() or "CONSUMIDOR FINAL",
        "categoria": fac_row.get("categoria") or "PARTICULAR",
        "ubicacion": fac_row.get("ubicacion") or "",
    }

    medidos = [{
        "servicio": "Agua",
        "cat": 1,
        "medidor": fac_row.get("nro_medidor") or "",
        "fec_ant": _fmt_fecha(fac_row.get("fecha_lect_ant")),
        "est_ant": float(fac_row.get("lectura_anterior") or 0),
        "fec_act": _fmt_fecha(fac_row.get("fecha_lect_act")),
        "est_act": float(fac_row.get("lectura_actual") or 0),
        "consumo": float(fac_row.get("consumo_m3") or 0),
        "dias": int(fac_row.get("dias_periodo") or 0),
        "periodo": fac_row.get("periodo") or "",
    }]

    cur.execute(_sql_items(), (id_fac,))
    items = [dict(r) for r in cur.fetchall()]
    grupos: dict[str, dict[str, Any]] = {}
    orden: list[str] = []
    iva_total = 0.0
    mora_por_grupo: dict[str, float] = {}

    for it in items:
        tipo = it["tipo"] or 0
        nombre_grupo = grupo_de_item(it.get("des_pro"), tipo)
        key = nombre_grupo
        if key not in grupos:
            grupos[key] = {"nombre": nombre_grupo, "subtotal": 0.0, "lineas": []}
            orden.append(key)
        importe = float(it["importe"] or 0)

        if it.get("cod_pro") == 47:
            can = float(it["can_ite"]) if it["can_ite"] not in (None, "") else 1.0
            preciso = (float(it["pre_vsi"]) if it["pre_vsi"] not in (None, "") else 0.0) * can
            mora_por_grupo[key] = mora_por_grupo.get(key, 0.0) + preciso
            continue

        descripcion = it["des_pro"] or ""
        if tipo == 2 and socio["categoria"]:
            descripcion = f"{descripcion} {socio['categoria']}".strip()
        elif tipo == 4:
            descripcion = _label_iva(it.get("des_pro"), socio["tipo_consumidor"])

        es_consumo = tipo == 2
        grupos[key]["lineas"].append({
            "descripcion": descripcion,
            "cantidad": (float(it["can_ite"]) if it["can_ite"] not in (None, 0) else None) if es_consumo else None,
            "periodo": (it["periodo"] or None) if es_consumo else None,
            "precio": (float(it["pre_vsi"]) if it["pre_vsi"] not in (None, 0) else None) if es_consumo else None,
            "importe": importe,
        })
        grupos[key]["subtotal"] += importe
        if tipo == 4:
            iva_total += importe

    for key, total_mora in mora_por_grupo.items():
        total_mora = round(total_mora, 2)
        grupos[key]["lineas"].append({
            "descripcion": "INTERES POR MORA",
            "cantidad": None,
            "periodo": None,
            "precio": None,
            "importe": total_mora,
        })
        grupos[key]["subtotal"] += total_mora

    for g in grupos.values():
        g["lineas"].sort(key=lambda linea: _orden_linea(linea["descripcion"]))

    grupos_conceptos = [grupos[k] for k in orden]
    fac["iva_total"] = iva_total

    periodos = _periodos_anteriores(fac["periodo"], config.CONSUMO_CHART_MESES)
    consumos_grafico: list[dict[str, Any]] = []
    if periodos and socio["suministro"]:
        try:
            cur.execute(_sql_consumos_grafico(len(periodos)), [socio["suministro"], *periodos])
            rows = cur.fetchall()
            mp = {r["periodo"]: float(r["consumo"] or 0) for r in rows}
            for p in periodos:
                consumos_grafico.append({
                    "periodo": _fmt_periodo_corto(p),
                    "valor": abs(mp.get(p, 0)),
                })
        except Exception:
            pass

    cur.execute(_sql_deudas(), (socio["suministro"], id_fac))
    deudas_rows = cur.fetchall()
    deudas_all = [{
        "fecha": _fmt_fecha(r["fecha_emision"]),
        "comprobante": _fmt_comp(r["res_com"], r["nro_comprobante"]),
        "importe": float(r["saldo"] or 0),
    } for r in deudas_rows]
    deudas_visibles = deudas_all[:4]

    n_adeudados = fac_row.get("comprobantes_adeudados")
    if n_adeudados is not None:
        deudas_mas = max(int(n_adeudados) - len(deudas_visibles), 0)
    else:
        deudas_mas = len(deudas_all) - len(deudas_visibles)

    deuda_total_ant = fac_row.get("deuda_total_anterior")
    if deuda_total_ant is not None:
        deuda_total = float(deuda_total_ant)
    else:
        deuda_total = sum(d["importe"] for d in deudas_all)

    return {
        "fac": fac,
        "socio": socio,
        "consumos_grafico": consumos_grafico,
        "medidos": medidos,
        "grupos_conceptos": grupos_conceptos,
        "deudas": deudas_visibles,
        "deudas_mas": deudas_mas if deudas_mas > 0 else 0,
        "deuda_total": deuda_total,
        "tarifa": {
            "fecha_asamblea": FECHA_ASAMBLEA,
            "cargo_fijo": CARGO_FIJO,
            "rangos": RANGOS_M3,
        },
    }


def cargar_datos(id_fac: int, fac_row: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Arma el dict del template a partir de id_fac.
    Devuelve (datos, fac_row_db) para enriquecer_presentacion().
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            if fac_row is None:
                cur.execute(_sql_cabecera(), (id_fac,))
                fac_row = cur.fetchone()
            if not fac_row:
                raise ValueError(f"id_fac={id_fac} no encontrado en {config.DB_NAME}")

            datos = _armar_datos(fac_row, cur, int(id_fac))
            return datos, dict(fac_row)


def cargar_datos_suministro(
    suministro: str,
    periodo: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resuelve id_fac por suministro/período y carga datos ERP."""
    fac_row = fetch_factura(suministro, periodo)
    id_fac = fac_row.get("id_fac")
    if id_fac is None:
        raise FacturaNotFoundError(
            f"La factura no tiene id_fac (suministro={suministro}"
            + (f", periodo={periodo}" if periodo else "")
            + ")"
        )
    return cargar_datos(int(id_fac), fac_row=fac_row)


def cargar_datos_completos(
    suministro: str,
    periodo: str | None = None,
    *,
    cod_nac: str | None = None,
    cod_pfs: str | None = None,
    cod_cesp: str | None = None,
) -> dict[str, Any]:
    """Pipeline completo: consultas ERP + normalización + logo/códigos/gráfico."""
    from mapear_factura import enriquecer_presentacion

    datos, fac_row = cargar_datos_suministro(suministro, periodo)
    datos = normalizar_para_template(datos)
    return enriquecer_presentacion(
        datos,
        fac_db=fac_row,
        cod_nac=cod_nac,
        cod_pfs=cod_pfs,
        cod_cesp=cod_cesp,
    )
