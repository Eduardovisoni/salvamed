"""Panel monetario pequeño: ventas, costo de lo vendido, margen bruto y compras.

- Margen bruto = ventas − costo de las unidades vendidas (costo promedio
  ponderado guardado en cada venta). No es utilidad ni ganancia neta: no
  descuenta salarios, alquiler ni otros gastos.
- Compras recibidas = unidades recibidas × costo del pedido + un envío por
  pedido. Comprar no es costo de ventas: el costo aparece cuando se vende.
- Compromisos = pedidos realizados que todavía no llegan. SALVAMED no registra
  pagos al proveedor.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import PENDIENTE, RECEPCION, REALIZADO, Movimiento, Pedido, PedidoLinea, Venta, en_ambito


@dataclass(frozen=True)
class Resumen:
    ventas: float
    costo_ventas: float
    numero_ventas: int
    compras_recibidas: float
    compromisos: float
    por_gestionar: float

    @property
    def margen_bruto(self) -> float:
        return self.ventas - self.costo_ventas

    @property
    def margen_porcentaje(self) -> float | None:
        return self.margen_bruto / self.ventas * 100 if self.ventas else None


def resumen(db: Session, corrida_id=None, desde: datetime | None = None, hasta: datetime | None = None) -> Resumen:
    filtro = [en_ambito(Venta.corrida_id, corrida_id), Venta.estado == "confirmada"]
    if desde:
        filtro.append(Venta.fecha >= desde)
    if hasta:
        filtro.append(Venta.fecha <= hasta)
    ventas, costo, numero = db.execute(
        select(func.coalesce(func.sum(Venta.total), 0), func.coalesce(func.sum(Venta.costo_total), 0), func.count(Venta.id)).where(*filtro)
    ).one()

    filtro_rec = [en_ambito(Movimiento.corrida_id, corrida_id), Movimiento.tipo == RECEPCION]
    if desde:
        filtro_rec.append(Movimiento.fecha >= desde)
    if hasta:
        filtro_rec.append(Movimiento.fecha <= hasta)
    recibido = 0.0
    pedidos_con_recepcion = set()
    for cantidad, costo_unitario, pedido_id in db.execute(
        select(Movimiento.cantidad, Movimiento.costo_unitario, Movimiento.pedido_id).where(*filtro_rec)
    ):
        recibido += cantidad * (costo_unitario or 0)
        pedidos_con_recepcion.add(pedido_id)
    if pedidos_con_recepcion:
        recibido += db.scalar(
            select(func.coalesce(func.sum(Pedido.costo_envio), 0)).where(Pedido.id.in_(pedidos_con_recepcion))
        )

    compromisos = por_gestionar = 0.0
    for pedido in db.scalars(select(Pedido).where(en_ambito(Pedido.corrida_id, corrida_id), Pedido.estado.in_((PENDIENTE, REALIZADO)))):
        lineas = list(db.scalars(select(PedidoLinea).where(PedidoLinea.pedido_id == pedido.id)))
        if pedido.estado == REALIZADO:
            compromisos += sum(l.por_recibir * (l.costo_unitario or 0) for l in lineas)
            if not any(l.cantidad_recibida for l in lineas):
                compromisos += pedido.costo_envio
        else:
            por_gestionar += sum(l.cantidad_pedida * (l.costo_unitario or 0) for l in lineas) + pedido.costo_envio
    return Resumen(float(ventas), float(costo), int(numero), round(recibido, 2), round(compromisos, 2), round(por_gestionar, 2))
