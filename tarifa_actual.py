"""
tarifa_actual.py - Tarifa vigente segun asamblea.
Editar este archivo cuando haya nueva resolucion de asamblea.
"""

# Asamblea del 30 de agosto de 2025
FECHA_ASAMBLEA = "30 DE AGOSTO DE 2025"

CARGO_FIJO = 12795  # $

# Rangos de consumo en m3 -> $/m3
RANGOS_M3 = [
    ("1 a 10 m³",        497),
    ("11 a 20 m³",       528),
    ("21 a 30 m³",      1010),
    ("31 a 40 m³",      1087),
    ("41 a 60 m³",      1212),
    ("61 a 80 m³",      1320),
    ("81 a 200 m³",     1397),
    ("Más de 201 m³",   1552),
]
