"""Reglas de existencias: alta, despacho a servicios, anulación, ajuste y recepción.

La existencia solo cambia con un movimiento. Nunca se edita directo ni se borra
historia: un despacho equivocado se anula con un movimiento de anulación y un
conteo físico se registra como ajuste con su motivo. Operación y simulación
usan exactamente estas funciones; la diferencia es `corrida_id`.

Costo de lo consumido: costo promedio ponderado. Cada recepción recalcula el
promedio con su costo; cada despacho guarda el costo vigente en ese momento, así
que editar el catálogo después no cambia despachos pasados.
"""

from dataclasses import dataclass
from datetime import datetime, time

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.consumo import GUATEMALA, dia_local, hora_local
from app.db import (
    AJUSTE,
    ANULACION,
    APERTURA,
    RECEPCION,
    RECIBIDO,
    REALIZADO,
    DESPACHO,
    Despacho,
    DespachoLinea,
    Medicamento,
    Movimiento,
    Pedido,
    PedidoLinea,
    en_ambito,
)
from app.planificacion import configuracion, manana

CANTIDAD_MAXIMA = 100_000


class ErrorDeOperacion(Exception):
    pass


def observado_desde(db: Session, momento: datetime):
    """Un día cuenta desde que empieza: si el alta es a media jornada, se cuenta desde mañana."""
    cfg = configuracion(db)
    local = hora_local(momento)
    if local.time() <= time(cfg.hora_apertura):
        return local.date()
    return manana(local.date())


def _mover(db: Session, m: Medicamento, tipo: str, cantidad: int, momento: datetime, **extra) -> Movimiento:
    """Cambia la existencia con un UPDATE condicional (nunca queda negativa) y deja el movimiento."""
    resultado = db.execute(
        update(Medicamento)
        .where(Medicamento.id == m.id, Medicamento.existencia + cantidad >= 0)
        .values(existencia=Medicamento.existencia + cantidad)
    )
    if resultado.rowcount == 0:
        db.refresh(m)
        raise ErrorDeOperacion(f"Solo hay {m.existencia:,} {m.unidad} de {m.nombre_completo}.")
    db.refresh(m)
    movimiento = Movimiento(
        corrida_id=m.corrida_id,
        medicamento_id=m.id,
        tipo=tipo,
        cantidad=cantidad,
        existencia_despues=m.existencia,
        fecha=momento,
        **extra,
    )
    db.add(movimiento)
    return movimiento


def dar_de_alta(
    db: Session, datos: dict, existencia_inicial: int, momento: datetime, corrida_id=None, usuario=""
) -> Medicamento:
    m = Medicamento(corrida_id=corrida_id, existencia=0, creado=momento, actualizado=momento, **datos)
    m.observado_desde = observado_desde(db, momento)
    m.costo_promedio = m.costo_compra
    db.add(m)
    db.flush()
    if existencia_inicial > 0:
        _mover(
            db, m, APERTURA, existencia_inicial, momento,
            costo_unitario=m.costo_compra, nota="Apertura de existencias", usuario=usuario,
        )
    return m


def nombre_repetido(db: Session, nombre: str, presentacion: str, corrida_id=None, excepto_id=None) -> bool:
    consulta = select(Medicamento.id).where(
        func.lower(Medicamento.nombre) == nombre.strip().lower(),
        func.lower(Medicamento.presentacion) == presentacion.strip().lower(),
        en_ambito(Medicamento.corrida_id, corrida_id),
    )
    if excepto_id is not None:
        consulta = consulta.where(Medicamento.id != excepto_id)
    return db.scalar(consulta) is not None


@dataclass
class LineaPedida:
    medicamento_id: int
    cantidad: int


def despachar(
    db: Session, servicio: str, lineas: list[LineaPedida], token: str, momento: datetime, corrida_id=None, usuario="", nota=""
) -> Despacho:
    """Confirma un despacho completo o ninguno. El token evita descontar dos veces la misma lista."""
    if not token or len(token) > 64:
        raise ErrorDeOperacion("Falta el identificador del despacho. Vuelve a cargar la página.")
    previo = db.scalar(select(Despacho).where(Despacho.token == token))
    if previo is not None:
        return previo
    servicio = (servicio or "").strip()
    if not servicio:
        raise ErrorDeOperacion("Elige el servicio que recibe los medicamentos.")
    juntas: dict[int, int] = {}
    for linea in lineas:
        if linea.cantidad < 1 or linea.cantidad > CANTIDAD_MAXIMA:
            raise ErrorDeOperacion("Cada cantidad debe ser un entero entre 1 y 100,000.")
        juntas[linea.medicamento_id] = juntas.get(linea.medicamento_id, 0) + linea.cantidad
    if not juntas:
        raise ErrorDeOperacion("La lista de despacho está vacía.")

    despacho = Despacho(corrida_id=corrida_id, token=token, servicio=servicio[:60], fecha=momento, nota=nota[:200])
    db.add(despacho)
    try:
        db.flush()
        costo = 0.0
        for medicamento_id, cantidad in juntas.items():
            m = db.get(Medicamento, medicamento_id)
            if m is None or m.corrida_id != corrida_id or not m.activo:
                raise ErrorDeOperacion("Uno de los medicamentos ya no está disponible.")
            costo_unitario = m.costo_promedio if m.costo_promedio is not None else m.costo_compra
            subtotal = round((costo_unitario or 0) * cantidad, 2)
            db.add(
                DespachoLinea(
                    despacho_id=despacho.id,
                    medicamento_id=m.id,
                    cantidad=cantidad,
                    costo_unitario=costo_unitario,
                    costo=subtotal,
                )
            )
            _mover(
                db, m, DESPACHO, -cantidad, momento,
                despacho_id=despacho.id, costo_unitario=costo_unitario, nota=servicio[:200], usuario=usuario,
            )
            costo += subtotal
        despacho.costo_total = round(costo, 2)
        db.commit()
    except ErrorDeOperacion:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        previo = db.scalar(select(Despacho).where(Despacho.token == token))
        if previo is None:
            raise
        return previo
    return despacho


def lineas_de(db: Session, despacho: Despacho) -> list[tuple[DespachoLinea, Medicamento]]:
    return list(
        db.execute(
            select(DespachoLinea, Medicamento)
            .join(Medicamento, Medicamento.id == DespachoLinea.medicamento_id)
            .where(DespachoLinea.despacho_id == despacho.id)
            .order_by(DespachoLinea.id)
        ).tuples()
    )


def anular_despacho(db: Session, despacho: Despacho, motivo: str, momento: datetime, usuario="") -> None:
    """Las unidades regresan a la farmacia con un movimiento de anulación; el despacho queda marcado."""
    if despacho.estado != "confirmado":
        raise ErrorDeOperacion("Ese despacho ya estaba anulado.")
    motivo = motivo.strip()
    if not motivo:
        raise ErrorDeOperacion("Escribe el motivo de la anulación.")
    for linea, m in lineas_de(db, despacho):
        _mover(
            db, m, ANULACION, linea.cantidad, momento,
            despacho_id=despacho.id, costo_unitario=linea.costo_unitario, nota=f"Anulación: {motivo}"[:200], usuario=usuario,
        )
    despacho.estado = "anulado"
    despacho.anulado_en = momento
    despacho.motivo_anulacion = motivo[:200]
    db.commit()


def ajustar(db: Session, m: Medicamento, nueva_existencia: int, motivo: str, momento: datetime, usuario="") -> Movimiento:
    """Conteo físico: deja la existencia en el valor contado y explica la diferencia."""
    motivo = motivo.strip()
    if not motivo:
        raise ErrorDeOperacion("Escribe el motivo del ajuste.")
    if nueva_existencia < 0:
        raise ErrorDeOperacion("La existencia contada no puede ser negativa.")
    diferencia = nueva_existencia - m.existencia
    if diferencia == 0:
        raise ErrorDeOperacion("La existencia contada es igual a la registrada: no hay nada que ajustar.")
    movimiento = _mover(db, m, AJUSTE, diferencia, momento, nota=motivo[:200], usuario=usuario)
    db.commit()
    return movimiento


def recibir(
    db: Session, pedido: Pedido, cantidades: dict[int, int], momento: datetime, usuario="", cerrar=False
) -> list[Movimiento]:
    """Registra lo que llegó físicamente. Solo esto aumenta la existencia."""
    if pedido.estado != REALIZADO:
        raise ErrorDeOperacion("Solo se recibe un pedido marcado como realizado.")
    lineas = {l.id: l for l in db.scalars(select(PedidoLinea).where(PedidoLinea.pedido_id == pedido.id))}
    movimientos = []
    for linea_id, cantidad in cantidades.items():
        linea = lineas.get(linea_id)
        if linea is None:
            raise ErrorDeOperacion("Esa línea no pertenece al pedido.")
        if cantidad < 0 or cantidad > linea.por_recibir:
            raise ErrorDeOperacion(f"Puedes recibir de 0 a {linea.por_recibir} unidades en esa línea.")
        if cantidad == 0:
            continue
        m = db.get(Medicamento, linea.medicamento_id)
        costo = linea.costo_unitario if linea.costo_unitario is not None else m.costo_compra
        if costo is not None:
            if m.costo_promedio is None or m.existencia <= 0:
                m.costo_promedio = costo
            else:
                m.costo_promedio = (m.existencia * m.costo_promedio + cantidad * costo) / (m.existencia + cantidad)
        movimientos.append(
            _mover(
                db, m, RECEPCION, cantidad, momento,
                pedido_id=pedido.id, costo_unitario=costo, nota=f"Pedido #{pedido.id}", usuario=usuario,
            )
        )
        linea.cantidad_recibida += cantidad
    if not movimientos and not cerrar:
        raise ErrorDeOperacion("Escribe al menos una cantidad recibida.")
    if cerrar or all(l.por_recibir == 0 for l in lineas.values()):
        pedido.estado = RECIBIDO
        pedido.recibido_en = momento
    db.commit()
    return movimientos


def historial(db: Session, m: Medicamento, limite: int = 30) -> list[Movimiento]:
    return list(
        db.scalars(
            select(Movimiento)
            .where(Movimiento.medicamento_id == m.id)
            .order_by(Movimiento.fecha.desc(), Movimiento.id.desc())
            .limit(limite)
        )
    )


def serie_de_existencias(db: Session, m: Medicamento) -> list[tuple[datetime, int, str, int]]:
    return [
        (mv.fecha, mv.existencia_despues, mv.tipo, mv.cantidad)
        for mv in db.scalars(
            select(Movimiento).where(Movimiento.medicamento_id == m.id).order_by(Movimiento.fecha, Movimiento.id)
        )
    ]


def inicio_del_dia_local(momento: datetime) -> datetime:
    return datetime.combine(dia_local(momento), time(), GUATEMALA)
