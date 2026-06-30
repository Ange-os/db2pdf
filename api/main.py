"""
API HTTP para generar PDF de facturas.

Prueba local (desde la raíz del proyecto):
    pip install -r requirements.txt
    python -m api.main

    curl http://127.0.0.1:8000/health
    curl -H "X-API-Key: TU_CLAVE" "http://127.0.0.1:8000/v1/factura/pdf?suministro=346201" -o prueba.pdf
"""
from __future__ import annotations

import re
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse, Response

import config
from db import FacturaNotFoundError, test_connection
from generar import generar_pdf_suministro

app = FastAPI(title="db2pdf API", version="0.1.0")


def _pdf_filename(suministro: str, periodo: str | None) -> str:
    per = periodo.strip().replace("/", "-") if periodo else "ultima"
    return f"factura_{suministro}_{per}.pdf"


def _validate_periodo(periodo: str | None) -> str | None:
    if periodo is None:
        return None
    p = periodo.strip()
    if not p:
        return None
    if not re.fullmatch(r"\d{2}/\d{4}", p):
        raise HTTPException(
            status_code=400,
            detail="periodo debe tener formato MM/YYYY (ej: 04/2026)",
        )
    return p


async def verify_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    expected = config.API_KEY
    if not expected:
        return
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="API key inválida o ausente")


@app.get("/health")
def health() -> JSONResponse:
    db_ok = False
    db_error: str | None = None
    try:
        db_ok = test_connection()
    except Exception as exc:
        db_error = str(exc)

    body: dict = {
        "status": "ok" if db_ok else "degraded",
        "database": "up" if db_ok else "down",
    }
    if db_error:
        body["database_error"] = db_error
    return JSONResponse(content=body, status_code=200 if db_ok else 503)


@app.get("/v1/factura/pdf", dependencies=[Depends(verify_api_key)])
def factura_pdf(
    suministro: Annotated[str, Query(min_length=1, description="Número de suministro")],
    periodo: Annotated[
        str | None,
        Query(description="Período MM/YYYY (opcional; sin él, la más reciente)"),
    ] = None,
) -> Response:
    periodo_norm = _validate_periodo(periodo)
    try:
        pdf_bytes = generar_pdf_suministro(suministro, periodo_norm)
    except FacturaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        msg = str(exc)
        if "credenciales" in msg.lower() or "Faltan" in msg:
            raise HTTPException(status_code=503, detail=msg) from exc
        raise HTTPException(status_code=500, detail=msg) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Error al generar PDF") from exc

    digits = re.sub(r"\D", "", suministro) or suministro.strip()
    filename = _pdf_filename(digits, periodo_norm)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host=config.API_HOST,
        port=config.API_PORT,
        reload=True,
    )
