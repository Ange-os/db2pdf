"""
generar.py - Genera el PDF de una factura.

Uso CLI (datos hardcodeados):
    python generar.py --muestra
    python generar.py --muestra --pdf out.pdf

Uso CLI (MariaDB):
    python generar.py --suministro 346201 --periodo 04/2026 --pdf out.pdf
    python generar.py 346201 04/2026 --pdf out.pdf

Uso programático:
    from generar import generar_pdf_suministro
    pdf_bytes = generar_pdf_suministro("346201", "04/2026")
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from io import BytesIO
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).parent


def _fmt_money(value) -> str:
    if value is None or value == "":
        return "0,00"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def render_html(datos: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(str(HERE)),
        autoescape=select_autoescape(['html']),
    )
    env.filters["money"] = _fmt_money
    tmpl = env.get_template("template.html")
    return tmpl.render(**datos)


def render_pdf(html: str, out_pdf: Path | None = None) -> bytes:
    """Renderiza HTML a PDF usando Playwright (Chromium headless)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright no instalado.")
        print("  pip install -r requirements.txt")
        print("  playwright install chromium")
        sys.exit(1)

    html_tmp = HERE / "_preview.html"
    html_tmp.write_text(html, encoding="utf-8")

    # A4 @ 96 dpi: 210 mm × 297 mm
    _A4_VIEWPORT = {"width": 794, "height": 1123}

    pdf_buf = BytesIO()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=_A4_VIEWPORT)
        page.goto(f"file:///{html_tmp.resolve().as_posix()}")
        page.wait_for_load_state("networkidle")
        pdf_bytes = page.pdf(
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )
        browser.close()

    if out_pdf:
        out_pdf = Path(out_pdf).resolve()
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        out_pdf.write_bytes(pdf_bytes)

    return pdf_bytes


def cargar_datos(
    *,
    muestra: bool = False,
    suministro: str | None = None,
    periodo: str | None = None,
    cod_nac: str | None = None,
    cod_pfs: str | None = None,
    cod_cesp: str | None = None,
) -> dict:
    if muestra:
        from datos_muestra import datos_factura_muestra
        from mapear_factura import enriquecer_presentacion
        datos = datos_factura_muestra()
        return enriquecer_presentacion(
            datos,
            cod_nac=cod_nac,
            cod_pfs=cod_pfs,
            cod_cesp=cod_cesp,
        )
    if not suministro:
        raise ValueError("Indicá --suministro o --muestra")
    from consultas import cargar_datos_completos
    return cargar_datos_completos(
        suministro,
        periodo,
        cod_nac=cod_nac,
        cod_pfs=cod_pfs,
        cod_cesp=cod_cesp,
    )


def generar_pdf_suministro(
    suministro: str,
    periodo: str | None = None,
    output_path: Path | str | None = None,
    *,
    cod_nac: str | None = None,
    cod_pfs: str | None = None,
    cod_cesp: str | None = None,
) -> bytes:
    """Genera PDF desde MariaDB por suministro y período opcional."""
    datos = cargar_datos(
        suministro=suministro,
        periodo=periodo,
        cod_nac=cod_nac,
        cod_pfs=cod_pfs,
        cod_cesp=cod_cesp,
    )
    html = render_html(datos)
    return render_pdf(html, Path(output_path) if output_path else None)


def _default_pdf_name(datos: dict) -> str:
    socio = datos.get("socio") or {}
    fac = datos.get("fac") or {}
    sumi = socio.get("suministro") or "factura"
    per = fac.get("periodo") or "ultima"
    return f"factura_{sumi}_{per.replace('/', '-')}.pdf"


def main():
    ap = argparse.ArgumentParser(
        description="Genera factura HTML/PDF (muestra hardcodeada o MariaDB)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python generar.py --muestra --pdf prueba.pdf\n"
            "  python generar.py --suministro 346201 --periodo 04/2026 --pdf out.pdf\n"
            "  python generar.py 346201 04/2026 --pdf out.pdf"
        ),
    )
    ap.add_argument(
        "posicional",
        nargs="*",
        metavar=("SUMINISTRO", "PERIODO"),
        help="Suministro y período MM/YYYY (alternativa a -s / -p)",
    )
    ap.add_argument("--muestra", action="store_true",
                    help="Usa datos hardcodeados (factura Moyano 04/2026)")
    ap.add_argument("--suministro", "-s", help="Número de suministro (MariaDB)")
    ap.add_argument("--periodo", "-p", help="Período MM/YYYY (opcional)")
    ap.add_argument("--pdf", help="Generar PDF en esta ruta (requiere playwright)")
    ap.add_argument("--abrir", action="store_true",
                    help="Abre el HTML preview en el browser por defecto")
    ap.add_argument("--cod-nac", default=None, help="Código Banco Nación (22 dígitos)")
    ap.add_argument("--cod-pfs", default=None, help="Código Pago Fácil (47 dígitos)")
    ap.add_argument("--cod-cesp", default=None, help="Código CESP talón (14 dígitos)")
    args = ap.parse_args()

    suministro = args.suministro
    periodo = args.periodo
    if args.posicional:
        if not suministro:
            suministro = args.posicional[0]
        if len(args.posicional) > 1 and not periodo:
            periodo = args.posicional[1]

    if not args.muestra and not suministro:
        ap.print_help()
        print("\nIndicá --muestra o --suministro (ej: generar.py 346201 04/2026)")
        sys.exit(0)

    try:
        datos = cargar_datos(
            muestra=args.muestra,
            suministro=suministro,
            periodo=periodo,
            cod_nac=args.cod_nac,
            cod_pfs=args.cod_pfs,
            cod_cesp=args.cod_cesp,
        )
    except Exception as e:
        from db import FacturaNotFoundError
        if isinstance(e, FacturaNotFoundError):
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        if isinstance(e, ValueError) and "id_fac=" in str(e):
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        if "credenciales" in str(e).lower() or "Faltan" in str(e):
            print(f"ERROR DB: {e}", file=sys.stderr)
            print("  Copiá .env.example a .env y completá DB_USER, DB_PASSWORD, DB_NAME.")
            sys.exit(2)
        raise

    html = render_html(datos)
    preview = HERE / "_preview.html"
    preview.write_text(html, encoding="utf-8")
    print(f"HTML preview: {preview}")

    if args.abrir:
        webbrowser.open(preview.as_uri())

    if args.pdf:
        out = Path(args.pdf).resolve()
        render_pdf(html, out)
        print(f"PDF generado: {out}")
    elif not args.muestra and suministro:
        out = HERE / _default_pdf_name(datos)
        render_pdf(html, out)
        print(f"PDF generado: {out}")


if __name__ == "__main__":
    main()
