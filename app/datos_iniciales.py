"""Datos simulados de los 30 medicamentos del Centro Médico El Roble.

Los nombres vienen de la lista original; el consumo, la desviación, el tiempo
de entrega, los costos (en quetzales) y las existencias son inventados para la
demostración. Los medicamentos críticos usan un nivel de servicio del 99 %.

Para que el modelo tenga historial desde el primer día, cada medicamento arranca
con 30 días de salidas simuladas: una normal con su μD y σD, reescalada para que
el promedio y la desviación de esos 30 días queden muy cerca de esos valores.
"""

import random
from datetime import datetime, time, timedelta, timezone
from statistics import mean, stdev

from app.consumo import GUATEMALA, VENTANA_DIAS, dia_local

SEMILLA = 2026

_CAMPOS = (
    "nombre",
    "unidad",
    "existencia",
    "consumo_promedio",
    "desviacion",
    "tiempo_entrega",
    "costo_pedido",
    "costo_mantener",
    "nivel_servicio",
)

_FILAS = [
    ("Ácido tranexámico", "ampollas", 83, 4, 1.5, 7, 150, 6, 95),
    ("Adrenalina (epinefrina)", "ampollas", 27, 3, 1.2, 5, 125, 3, 99),
    ("Albúmina humana", "viales", 14, 0.8, 0.5, 10, 200, 140, 95),
    ("Budena (budesonida)", "ampollas", 161, 6, 2.5, 7, 125, 7, 95),
    ("Ceftriaxona", "viales", 83, 12, 4, 7, 150, 5, 95),
    ("Clonazepam", "tabletas", 408, 10, 3, 14, 100, 0.6, 95),
    ("Clorfeniramina", "ampollas", 194, 5, 2, 7, 100, 1.5, 95),
    ("Dexametasona", "ampollas", 235, 10, 3.5, 7, 100, 1.8, 95),
    ("Dexketoprofeno", "ampollas", 680, 14, 5, 7, 125, 3.5, 95),
    ("Diazepam", "ampollas", 40, 3, 1.2, 14, 100, 2.5, 95),
    ("Dimenhidrinato", "ampollas", 184, 6, 2.2, 7, 100, 1.5, 95),
    ("Dipirona (metamizol)", "ampollas", 182, 20, 6, 5, 100, 1.2, 95),
    ("Fentanilo", "ampollas", 262, 5, 2, 14, 175, 8, 99),
    ("Furosemida", "ampollas", 229, 8, 3, 7, 100, 1.4, 95),
    ("Haloperidol", "ampollas", 50, 1.5, 0.8, 14, 100, 3, 95),
    ("Insulina cristalina", "frascos", 10, 1.2, 0.5, 7, 150, 30, 99),
    ("Laxalán (lactulosa)", "frascos", 74, 2, 0.9, 7, 100, 6, 95),
    ("Meropenema", "viales", 150, 4, 1.8, 10, 200, 35, 99),
    ("Metoclopramida", "ampollas", 329, 12, 4, 7, 100, 1.2, 95),
    ("Metronidazol", "bolsas", 47, 6, 2.3, 7, 125, 3, 95),
    ("Misoprostol", "tabletas", 104, 2, 1.2, 14, 125, 4, 99),
    ("Morfina", "ampollas", 47, 3, 1.4, 14, 175, 6, 99),
    ("Oxitocina", "ampollas", 87, 6, 2.5, 7, 125, 2.5, 99),
    ("Paracetamol", "tabletas", 2702, 90, 25, 5, 100, 0.1, 95),
    ("Salbutamol", "frascos", 37, 2.5, 1, 7, 100, 8, 95),
    ("Solución Hartmann", "bolsas", 504, 25, 8, 5, 150, 3, 95),
    ("Solución salina", "bolsas", 337, 45, 12, 5, 150, 2.5, 95),
    ("Tropium (bromuro de ipratropio)", "frascos", 76, 2, 0.8, 10, 125, 9, 95),
    ("Vancomicina", "viales", 151, 3, 1.4, 10, 175, 18, 95),
    ("Vitamina K", "ampollas", 88, 3, 1, 7, 100, 2, 95),
]

MEDICAMENTOS = [dict(zip(_CAMPOS, fila)) for fila in _FILAS]


def salidas_simuladas(
    medicamento: dict, indice: int, ahora: datetime
) -> tuple[list[tuple[datetime, int]], datetime]:
    """Devuelve las salidas de los últimos 30 días y la fecha de alta del medicamento."""
    azar = random.Random(SEMILLA + indice)
    z = [azar.gauss(0, 1) for _ in range(VENTANA_DIAS)]
    media, desviacion = mean(z), stdev(z)
    mu, sigma = medicamento["consumo_promedio"], medicamento["desviacion"]
    cantidades = [max(0, round(mu + sigma * (x - media) / desviacion)) for x in z]

    hoy = dia_local(ahora)
    primer_dia = hoy - timedelta(days=VENTANA_DIAS - 1)
    salidas = []
    for atras, cantidad in zip(range(VENTANA_DIAS - 1, -1, -1), cantidades):
        dia = hoy - timedelta(days=atras)
        fecha = datetime.combine(dia, time(7, azar.randrange(60)), GUATEMALA)
        if cantidad:
            salidas.append((min(fecha, ahora).astimezone(timezone.utc), cantidad))
    return salidas, datetime.combine(primer_dia, time(), GUATEMALA).astimezone(timezone.utc)
