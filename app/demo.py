"""Catálogo de demostración para el modo operación (solo con una acción explícita).

Usa `datos/SALVAMED en orden.csv`, el archivo del grupo. Solo los nombres de los
medicamentos están contextualizados en el Sanatorio El Roble; existencias, plazos,
totales semanales y costos son simulados. El archivo trae `precio_unitario_q` sin
decir qué es: aquí se toma como costo de compra simulado, porque el archivo lo usa
para valorar el inventario y presupuestar pedidos. Es un supuesto de demostración.
"""

from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db import (
    AccionTelegram, ConsumoHistorico, Despacho, DespachoLinea, Importacion, Medicamento, Mensaje, Movimiento,
    Pedido, PedidoLinea, PlanDiario, Proveedor,
)
from app.importacion import Opciones, aplicar, preparar

ARCHIVO = Path(__file__).resolve().parent.parent / "datos" / "SALVAMED en orden.csv"


def cargar_demo(db: Session) -> dict:
    opciones = Opciones(columna_ambigua={"precio_unitario_q": "costo_compra"})
    importacion = preparar(db, "SALVAMED en orden.csv (demostración)", ARCHIVO.read_bytes(), opciones)
    return aplicar(db, importacion)


def borrar_datos(db: Session) -> dict:
    """Deja la operación vacía para volver a cargar o importar desde cero.

    Borra medicamentos, existencias, despachos, pedidos, proveedores, historia importada y
    avisos de la operación. No toca la configuración general ni las simulaciones (corrida_id).
    Los hijos se borran a mano antes que los padres porque SQLite no aplica los ON DELETE.
    """
    medicamentos = select(Medicamento.id).where(Medicamento.corrida_id.is_(None))
    pedidos = select(Pedido.id).where(Pedido.corrida_id.is_(None))
    despachos = select(Despacho.id).where(Despacho.corrida_id.is_(None))
    mensajes = select(Mensaje.id).where(Mensaje.corrida_id.is_(None))
    cuenta = {
        "medicamentos": db.scalar(select(func.count()).select_from(medicamentos.subquery())),
        "despachos": db.scalar(select(func.count()).select_from(despachos.subquery())),
        "pedidos": db.scalar(select(func.count()).select_from(pedidos.subquery())),
    }
    db.execute(delete(AccionTelegram).where(AccionTelegram.mensaje_id.in_(mensajes)))
    db.execute(delete(Mensaje).where(Mensaje.corrida_id.is_(None)))
    db.execute(delete(PedidoLinea).where(PedidoLinea.pedido_id.in_(pedidos)))
    db.execute(delete(Pedido).where(Pedido.corrida_id.is_(None)))
    db.execute(delete(DespachoLinea).where(DespachoLinea.despacho_id.in_(despachos)))
    db.execute(delete(Despacho).where(Despacho.corrida_id.is_(None)))
    db.execute(delete(Movimiento).where(Movimiento.corrida_id.is_(None)))
    db.execute(delete(PlanDiario).where(PlanDiario.corrida_id.is_(None)))
    db.execute(delete(ConsumoHistorico).where(ConsumoHistorico.medicamento_id.in_(medicamentos)))
    db.execute(delete(Medicamento).where(Medicamento.corrida_id.is_(None)))
    db.execute(delete(Proveedor).where(Proveedor.corrida_id.is_(None)))
    db.execute(delete(Importacion))
    db.commit()
    return cuenta
