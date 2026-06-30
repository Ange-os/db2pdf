import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.resolve()
load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "")

TABLE_FACTURAS = os.getenv("TABLE_FACTURAS", "facturas")
TABLE_CONSUMOS = os.getenv("TABLE_CONSUMOS", "factura_consumo")
TABLE_ITEMS = os.getenv("TABLE_ITEMS", "factura_items")
TABLE_ITEMS_ERP = os.getenv("TABLE_ITEMS_ERP", "factura_items_erp")
TABLE_DEUDAS = os.getenv("TABLE_DEUDAS", "factura_deudas_anteriores")
TABLE_CESPAFIP = os.getenv("TABLE_CESPAFIP", "cespafip")
CONSUMO_CHART_MESES = int(os.getenv("CONSUMO_CHART_MESES", "13"))

STATIC_DIR = BASE_DIR / "static"
OUTPUT_DIR = BASE_DIR / "output"

# API HTTP (pruebas locales / VPS)
API_KEY = os.getenv("API_KEY", "")
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))
