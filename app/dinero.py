"""Panel de costos: lo que costó lo consumido por los servicios y las compras.

- Costo de lo consumido = unidades despachadas × costo promedio ponderado guardado
  en cada despacho. Se muestra en total y por servicio. No es una factura ni un
  cargo al paciente: SALVAMED no maneja cobros ni datos de pacientes.
- Compras recibidas = unidades recibidas × costo del pedido + un envío por
  pedido. Comprar no es consumir: el costo de consumo aparece al despachar.
- Comprometido = pedidos realizados que todavía no llegan. SALVAMED no registra
  pagos al proveedor.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import PENDIENTE, RECEPCION, REALIZADO, Despacho, DespachoLinea, Movimiento, Pedido, PedidoLinea, en_ambito


@dataclass(frozen=True)
class Resumen:
    consumido: float
    numero_despachos: int
    unidades: int
    por_servicio: tuple[tuple[str, float, int], ...]
    compras_recibidas: float
    compromisos: float
    por_gestionar: float


def resumen(db: Session, corrida_id=None, desde: datetime | None = None, hasta: datetime | None = None) -> Resumen:
    filtro = [en_ambito(Despacho.corrida_id, corrida_id), Despacho.estado == "confirmado"]
    if desde:
        filtro.append(Despacho.fecha >= desde)
    if hasta:
        filtro.append(Despacho.fecha <= hasta)
    por_servicio = tuple(
        (servicio, round(float(costo), 2), int(numero))
        for servicio, costo, numero in db.execute(
            select(Despacho.servicio, func.coalesce(func.sum(Despacho.costo_total), 0), func.count(Despacho.id))
            .where(*filtro)
            .group_by(Despacho.servicio)
            .order_by(func.sum(Despacho.costo_total).desc())
        )
    )
    unidades = db.scalar(
        select(func.coalesce(func.sum(DespachoLinea.cantidad), 0))
        .join(Despacho, Despacho.id == DespachoLinea.despacho_id)
        .where(*filtro)
    )

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
    return Resumen(
        round(sum(c for _, c, _ in por_servicio), 2),
        sum(n for _, _, n in por_servicio),
        int(unidades or 0),
        por_servicio,
        round(recibido, 2),
        round(compromisos, 2),
        round(por_gestionar, 2),
    )
