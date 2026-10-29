"""Catálogo de demostración para el modo operación (solo con una acción explícita).

Usa `datos/SALVAMED en orden.csv`, el archivo del grupo. Solo los nombres de los
medicamentos están contextualizados en el Sanatorio El Roble; existencias, plazos,
totales semanales y costos son simulados. El archivo trae `precio_unitario_q` sin
decir qué es: aquí se toma como costo de compra simulado, porque el archivo lo usa
para valorar el inventario y presupuestar pedidos. Es un supuesto de demostración.
"""

from pathlib import Path

from sqlalchemy.orm import Session

from app.importacion import Opciones, aplicar, preparar

ARCHIVO = Path(__file__).resolve().parent.parent / "datos" / "SALVAMED en orden.csv"


def cargar_demo(db: Session) -> dict:
    opciones = Opciones(columna_ambigua={"precio_unitario_q": "costo_compra"})
    importacion = preparar(db, "SALVAMED en orden.csv (demostración)", ARCHIVO.read_bytes(), opciones)
    return aplicar(db, importacion)
