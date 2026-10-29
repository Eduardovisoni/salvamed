"""Pedidos de reposición y evaluación del punto de reorden.

Estados: pendiente de gestionar → realizado (en camino) → recibido, o cancelado.
Marcar "realizado" registra que el encargado hizo la compra por su canal; no
aumenta la existencia. Solo la recepción física lo hace.

La reposición se evalúa con la posición de inventario: existencia más lo que ya
se pidió y falta recibir. Un aviso pendiente de gestionar no cuenta como
mercancía comprada.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import avisos
from app.db import (
    ABIERTOS,
    CANCELADO,
    PENDIENTE,
    REALIZADO,
    Medicamento,
    Pedido,
    PedidoLinea,
    Proveedor,
    ahora,
)
from app.inventario import ErrorDeOperacion
from app.planificacion import Evaluacion, configuracion, evaluar, evaluar_varios, registrar_plan_del_dia

DIAS_ENTRE_AVISOS_DE_AUMENTO = 7


def lineas_de(db: Session, pedido: Pedido) -> list[tuple[PedidoLinea, Medicamento]]:
    return list(
        db.execute(
            select(PedidoLinea, Medicamento)
            .join(Medicamento, Medicamento.id == PedidoLinea.medicamento_id)
            .where(PedidoLinea.pedido_id == pedido.id)
            .order_by(PedidoLinea.id)
        ).tuples()
    )


def _linea_desde(e: Evaluacion, cantidad: int | None = None) -> PedidoLinea:
    return PedidoLinea(
        medicamento_id=e.m.id,
        cantidad_sugerida=e.sugerida,
        cantidad_eoq=e.plan.Q if e.plan else None,
        cantidad_pedida=cantidad if cantidad is not None else e.sugerida,
        costo_unitario=e.m.costo_compra,
        existencia_aviso=e.existencia,
        posicion_aviso=e.posicion,
        punto_reorden=e.plan.R if e.plan else None,
        inventario_seguridad=e.plan.IS if e.plan else None,
        mu_d=e.consumo.mu_d,
        sigma_d=e.consumo.sigma_d,
        tiempo_entrega=e.parametros["L"].valor,
    )


def crear_pedido(db: Session, e: Evaluacion, momento: datetime, origen="automatico", cantidad: int | None = None) -> Pedido:
    m = e.m
    proveedor = db.get(Proveedor, m.proveedor_id) if m.proveedor_id else None
    envio = proveedor.costo_envio if proveedor and proveedor.costo_envio else configuracion(db).costo_pedido
    pedido = Pedido(
        corrida_id=m.corrida_id,
        proveedor_id=m.proveedor_id,
        estado=PENDIENTE,
        origen=origen,
        creado=momento,
        costo_envio=envio,
    )
    db.add(pedido)
    db.flush()
    linea = _linea_desde(e, cantidad)
    if linea.cantidad_pedida < 1:
        raise ErrorDeOperacion("La cantidad del pedido debe ser 1 o más.")
    linea.pedido_id = pedido.id
    db.add(linea)
    db.flush()
    return pedido


def agregar_linea(db: Session, pedido: Pedido, e: Evaluacion, cantidad: int) -> PedidoLinea:
    """Compra conjunta: otro medicamento del mismo proveedor en el mismo pedido (un solo envío)."""
    if pedido.estado != PENDIENTE:
        raise ErrorDeOperacion("Solo se agregan líneas a un pedido pendiente de gestionar.")
    if e.m.proveedor_id != pedido.proveedor_id:
        raise ErrorDeOperacion("Ese medicamento es de otro proveedor.")
    if any(l.medicamento_id == e.m.id for l, _ in lineas_de(db, pedido)):
        raise ErrorDeOperacion("Ese medicamento ya está en el pedido.")
    if cantidad < 1:
        raise ErrorDeOperacion("La cantidad debe ser 1 o más.")
    linea = _linea_desde(e, cantidad)
    linea.pedido_id = pedido.id
    db.add(linea)
    db.commit()
    return linea


def plazo_de(db: Session, pedido: Pedido) -> float:
    return max((l.tiempo_entrega or 0) for l, _ in lineas_de(db, pedido)) or configuracion(db).tiempo_entrega


def marcar_realizado(
    db: Session, pedido: Pedido, usuario: str, momento: datetime, fecha_estimada: datetime | None = None
) -> tuple[bool, str]:
    """Devuelve (cambió, explicación). Repetirlo no duplica nada."""
    if pedido.estado == REALIZADO:
        return False, f"Ya estaba registrado como realizado por {pedido.confirmado_por or 'otra persona'}."
    if pedido.estado != PENDIENTE:
        return False, f"El pedido #{pedido.id} está {pedido.estado}; ya no se puede marcar como realizado."
    pedido.estado = REALIZADO
    pedido.realizado_en = momento
    pedido.confirmado_por = usuario[:120]
    pedido.fecha_estimada = fecha_estimada or momento + timedelta(days=plazo_de(db, pedido))
    db.commit()
    return True, f"Pedido #{pedido.id} registrado como realizado por {usuario}."


def confirmar(db: Session, pedido: Pedido, usuario: str) -> tuple[bool, str]:
    """Punto de entrada común (web y Telegram). En simulación usa el reloj virtual."""
    if pedido.corrida_id is None:
        return marcar_realizado(db, pedido, usuario, ahora())
    from app import simulacion

    return simulacion.confirmar_pedido(db, pedido, usuario)


def cancelar(db: Session, pedido: Pedido, momento: datetime, motivo: str = "") -> None:
    if pedido.estado not in ABIERTOS:
        raise ErrorDeOperacion("Ese pedido ya está cerrado.")
    if any(l.cantidad_recibida for l, _ in lineas_de(db, pedido)):
        raise ErrorDeOperacion("Ya se recibió parte del pedido: ciérralo desde la recepción.")
    pedido.estado = CANCELADO
    pedido.cancelado_en = momento
    pedido.nota = motivo[:200]
    db.commit()
    if pedido.corrida_id is not None:
        from app import simulacion

        simulacion.quitar_causa(db, pedido)


def cambiar_cantidad(db: Session, linea: PedidoLinea, cantidad: int) -> None:
    pedido = db.get(Pedido, linea.pedido_id)
    if pedido.estado not in ABIERTOS:
        raise ErrorDeOperacion("Ese pedido ya está cerrado.")
    if cantidad < max(linea.cantidad_recibida, 1):
        raise ErrorDeOperacion(f"La cantidad debe ser al menos {max(linea.cantidad_recibida, 1)}.")
    linea.cantidad_pedida = cantidad
    db.commit()


def cambiar_fecha(db: Session, pedido: Pedido, fecha: datetime) -> None:
    if pedido.estado != REALIZADO:
        raise ErrorDeOperacion("La fecha estimada se corrige cuando el pedido ya fue realizado.")
    pedido.fecha_estimada = fecha
    pedido.aviso_atraso = False
    db.commit()


def evaluar_reposicion(db: Session, m: Medicamento, momento: datetime) -> tuple[Evaluacion, Pedido | None]:
    evaluaciones, nuevos = evaluar_grupo(db, [m], momento)
    return evaluaciones[m.id], (nuevos[0] if nuevos else None)


def evaluar_grupo(db: Session, medicamentos: list[Medicamento], momento: datetime) -> tuple[dict[int, Evaluacion], list[Pedido]]:
    """Evalúa medicamentos y, si alguno llegó al punto de reorden, crea su pedido y el aviso.

    No duplica: si ya hay un pedido abierto para el medicamento no crea otro. Sí
    avisa una vez si el pedido en camino ya no alcanza o si está atrasado. Si
    varios medicamentos del mismo proveedor llegan a R en la misma revisión, van
    en un solo pedido (un solo envío y un solo mensaje).
    """
    evaluaciones = evaluar_varios(db, medicamentos, momento)
    por_proveedor: dict[int | None, list[Evaluacion]] = {}
    for e in evaluaciones.values():
        m = e.m
        registrar_plan_del_dia(db, e)
        abierto = e.pedido_abierto
        if e.plan and e.plan.D > 0 and e.estado.clave == "pedir" and abierto is None and e.sugerida > 0:
            por_proveedor.setdefault(m.proveedor_id, []).append(e)
        elif abierto is not None and abierto.estado == REALIZADO and e.plan:
            if e.posicion <= e.plan.R_pedido and not abierto.aviso_insuficiente:
                abierto.aviso_insuficiente = True
                avisos.encolar(db, "insuficiente", avisos.texto_insuficiente(e, abierto), m.corrida_id, abierto.id, m.id)
            if abierto.fecha_estimada and momento > abierto.fecha_estimada and not abierto.aviso_atraso:
                abierto.aviso_atraso = True
                avisos.encolar(db, "atraso", avisos.texto_atraso(e, abierto), m.corrida_id, abierto.id, m.id)
        if e.aumento and (
            m.ultimo_aviso_aumento is None or (e.hoy - m.ultimo_aviso_aumento).days >= DIAS_ENTRE_AVISOS_DE_AUMENTO
        ):
            m.ultimo_aviso_aumento = e.hoy
            avisos.encolar(db, "aumento", avisos.texto_aumento(e), m.corrida_id, None, m.id)
    nuevos = []
    for lista in por_proveedor.values():
        pedido = crear_pedido(db, lista[0], momento)
        for e in lista[1:]:
            linea = _linea_desde(e)
            linea.pedido_id = pedido.id
            db.add(linea)
        db.flush()
        avisos.encolar(
            db, "reposicion", avisos.texto_reposicion(db, pedido), pedido.corrida_id, pedido.id, lista[0].m.id, con_botones=True
        )
        nuevos.append(pedido)
    db.commit()
    return evaluaciones, nuevos


def pedidos(db: Session, corrida_id=None, estados=None, limite=100) -> list[Pedido]:
    from app.db import en_ambito

    consulta = select(Pedido).where(en_ambito(Pedido.corrida_id, corrida_id)).order_by(Pedido.id.desc()).limit(limite)
    if estados:
        consulta = consulta.where(Pedido.estado.in_(estados))
    return list(db.scalars(consulta))
