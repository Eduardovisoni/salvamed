"""Catálogo de demostración para el modo operación (solo con una acción explícita).

Usa `datos/SALVAMED en orden.csv`, el archivo del grupo. Solo los nombres de los
medicamentos están contextualizados en El Roble; existencias, plazos, totales
semanales y precios son simulados. El archivo trae `precio_unitario_q` sin decir
si es costo o precio: aquí se toma como costo de compra simulado (el archivo lo
usa para valorar el inventario y presupuestar pedidos) y se genera un precio de
venta simulado con un margen del 35 %. Las dos cosas son supuestos de demostración.
"""

from pathlib import Path

from sqlalchemy.orm import Session

from app.importacion import Opciones, aplicar, preparar

ARCHIVO = Path(__file__).resolve().parent.parent / "datos" / "SALVAMED en orden.csv"
MARGEN_SIMULADO = 0.35


def cargar_demo(db: Session) -> dict:
    opciones = Opciones(columna_ambigua={"precio_unitario_q": "costo_compra"}, margen_simulado=MARGEN_SIMULADO)
    importacion = preparar(db, "SALVAMED en orden.csv (demostración)", ARCHIVO.read_bytes(), opciones)
    return aplicar(db, importacion)
