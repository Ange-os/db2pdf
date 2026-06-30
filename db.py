"""Conexión y consultas MySQL/MariaDB — tabla facturas (cabecera completa del PDF)."""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

import config


class FacturaNotFoundError(Exception):
    pass


def _sanitize_identifier(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", name or ""):
        raise ValueError(f"Identificador SQL no válido: {name!r}")
    return name


@contextmanager
def get_connection():
    if not config.DB_USER or not config.DB_NAME:
        raise RuntimeError(
            "Faltan credenciales. Copiá .env.example a .env y completá DB_USER, DB_PASSWORD, DB_NAME."
        )
    conn = pymysql.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME,
        charset="utf8mb4",
        collation="utf8mb4_unicode_ci",
        cursorclass=DictCursor,
        autocommit=True,
    )
    try:
        yield conn
    finally:
        conn.close()


def _normalize_suministro(suministro: str) -> str:
    digits = re.sub(r"\D", "", str(suministro or ""))
    if not digits:
        raise ValueError("Número de suministro inválido")
    return digits


def _where_suministro_sql() -> str:
    return "TRIM(suministro) = TRIM(CAST(CAST(%s AS UNSIGNED) AS CHAR))"


def fetch_factura_by_id(
    id_fac: int,
    suministro: str | None = None,
) -> dict[str, Any]:
    """Devuelve la fila de facturas por id_fac (sin filtro de saldo)."""
    tbl = _sanitize_identifier(config.TABLE_FACTURAS)
    sql = f"SELECT * FROM `{tbl}` WHERE id_fac = %s"
    params: list[Any] = [int(id_fac)]
    if suministro:
        sql += f" AND {_where_suministro_sql()}"
        params.append(_normalize_suministro(suministro))

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            factura = cur.fetchone()

    if not factura:
        msg = f"No hay factura para id_fac={id_fac}"
        if suministro:
            msg += f", suministro={_normalize_suministro(suministro)}"
        raise FacturaNotFoundError(msg)
    return factura


def fetch_factura(
    suministro: str,
    periodo: str | None = None,
) -> dict[str, Any]:
    """
    Devuelve la fila de facturas (TABLE_FACTURAS).

    Sin id_fac explícito: entre comprobantes con saldo > 0, elige el de mayor
    fecha_emision; desempate por id_fac DESC. Si periodo es None, aplica sobre
    todos los períodos del suministro.
    """
    suministro_norm = _normalize_suministro(suministro)
    tbl = _sanitize_identifier(config.TABLE_FACTURAS)

    sql = f"""
        SELECT *
        FROM `{tbl}`
        WHERE {_where_suministro_sql()}
          AND saldo > 0
    """
    params: list[Any] = [suministro_norm]

    if periodo:
        sql += " AND periodo = %s"
        params.append(periodo.strip())

    sql += " ORDER BY fecha_emision DESC, id_fac DESC LIMIT 1"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            factura = cur.fetchone()

    if not factura:
        raise FacturaNotFoundError(
            f"No hay factura para suministro={suministro_norm}"
            + (f", periodo={periodo}" if periodo else "")
        )

    return factura


def _periodo_sort_key(periodo: str) -> int:
    s = str(periodo or "").strip()
    if "/" not in s:
        return 0
    mm, yyyy = s.split("/", 1)
    try:
        return int(yyyy) * 100 + int(mm)
    except ValueError:
        return 0


def fetch_historial_consumo(
    suministro: str,
    periodo_hasta: str | None = None,
    *,
    meses: int | None = None,
) -> list[dict[str, Any]]:
    """Últimos N meses de consumo (m³) para el gráfico."""
    suministro_norm = _normalize_suministro(suministro)
    tbl = _sanitize_identifier(config.TABLE_CONSUMOS)
    n_meses = meses if meses is not None else config.CONSUMO_CHART_MESES
    hasta_key = _periodo_sort_key(periodo_hasta) if periodo_hasta else None

    sql = f"""
        SELECT periodo, consumo
        FROM `{tbl}`
        WHERE cod_sum = CAST(%s AS UNSIGNED)
          AND consumo IS NOT NULL
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, [suministro_norm])
            rows = cur.fetchall() or []

    by_periodo: dict[str, float] = {}
    for row in rows:
        per = str(row.get("periodo") or "").strip()
        if not per:
            continue
        key = _periodo_sort_key(per)
        if hasta_key is not None and key > hasta_key:
            continue
        try:
            consumo = float(row.get("consumo") or 0)
        except (TypeError, ValueError):
            consumo = 0.0
        by_periodo[per] = consumo

    ordenados = sorted(by_periodo.items(), key=lambda x: _periodo_sort_key(x[0]))
    ultimos = ordenados[-n_meses:] if n_meses > 0 else ordenados
    return [{"periodo": p, "consumo": c} for p, c in ultimos]


def _where_factura_hija(factura: dict[str, Any]) -> tuple[str, list[Any]]:
    fac_id = factura.get("id")
    nro = factura.get("nro_comprobante")
    if fac_id is not None:
        return "factura_id = %s", [fac_id]
    if nro:
        return "nro_comprobante = %s", [str(nro).strip()]
    return "", []


def fetch_factura_items(factura: dict[str, Any]) -> list[dict[str, Any]]:
    """Líneas de detalle (concepto / importe) vinculadas a la factura."""
    where, params = _where_factura_hija(factura)
    if not where:
        return []

    tbl = _sanitize_identifier(config.TABLE_ITEMS)
    sql = f"""
        SELECT concepto, importe
        FROM `{tbl}`
        WHERE {where}
        ORDER BY id
    """

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall() or [])
    except pymysql.Error:
        return []


def fetch_factura_deudas(factura: dict[str, Any]) -> list[dict[str, Any]]:
    """Comprobantes adeudados anteriores."""
    where, params = _where_factura_hija(factura)
    if not where:
        return []

    tbl = _sanitize_identifier(config.TABLE_DEUDAS)
    sql = f"""
        SELECT fecha, comprobante_ref, importe
        FROM `{tbl}`
        WHERE {where}
        ORDER BY fecha, id
    """

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall() or [])
    except pymysql.Error:
        return []


def fetch_factura_completa(
    suministro: str,
    periodo: str | None = None,
) -> dict[str, Any]:
    """Factura + histórico de consumo + ítems + deudas."""
    fac = fetch_factura(suministro, periodo)
    periodo_fac = str(fac.get("periodo") or periodo or "").strip() or None
    historial = fetch_historial_consumo(suministro, periodo_hasta=periodo_fac)
    items = fetch_factura_items(fac)
    deudas = fetch_factura_deudas(fac)
    return {
        "factura": fac,
        "historial_consumo": historial,
        "items": items,
        "deudas_anteriores": deudas,
    }


def test_connection() -> bool:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            row = cur.fetchone()
    return bool(row and row.get("ok") == 1)
