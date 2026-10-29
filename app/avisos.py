"""Textos de los avisos y bandeja de salida.

Un aviso se guarda primero en la tabla `mensajes` y después se envía (ver
app/telegram.py). Si Telegram falla, el despacho o el pedido no se deshacen: el
mensaje queda con su estado, sus intentos y el error para reintentarlo.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ajustes import ajustes
from app.consumo import hora_local
from app.db import Medicamento, Mensaje, Pedido, PedidoLinea, Proveedor, ahora
from app.modelo import punto_de_pedido


def _n(valor: float | None, decimales: int = 1) -> str:
    if valor is None:
        return "—"
    return f"{valor:,.{decimales}f}"


def _encabezado(corrida_id) -> str:
    if corrida_id is None:
        return "🏥 SALVAMED · Sanatorio El Roble (farmacia interna)"
    return f"🧪 SALVAMED · SIMULACIÓN del sanatorio (corrida #{corrida_id}) — no es inventario real"


def texto_reposicion(db: Session, pedido: Pedido, evaluaciones: dict | None = None) -> str:
    lineas = list(
        db.execute(
            select(PedidoLinea, Medicamento)
            .join(Medicamento, Medicamento.id == PedidoLinea.medicamento_id)
            .where(PedidoLinea.pedido_id == pedido.id)
            .order_by(PedidoLinea.id)
        ).tuples()
    )
    proveedor = db.get(Proveedor, pedido.proveedor_id) if pedido.proveedor_id else None
    partes = [_encabezado(pedido.corrida_id), f"⚠️ Reposición necesaria · Pedido #{pedido.id}", ""]
    for linea, m in lineas:
        partes += [
            f"💊 {m.nombre_completo} ({m.unidad})",
            f"Disponible: {linea.existencia_aviso} · En camino: {(linea.posicion_aviso or 0) - (linea.existencia_aviso or 0)}",
            f"Pedir al llegar a: {punto_de_pedido(linea.punto_reorden) if linea.punto_reorden is not None else '—'} (R = {_n(linea.punto_reorden)} hacia arriba; inventario de seguridad {_n(linea.inventario_seguridad)})",
            f"Consumo: {_n(linea.mu_d, 2)}/día, σ {_n(linea.sigma_d, 2)}",
            f"Plazo de entrega L: {_n(linea.tiempo_entrega)} días → consumo previsto en L: {_n((linea.mu_d or 0) * (linea.tiempo_entrega or 0))}",
            f"Cantidad sugerida: {linea.cantidad_pedida} (EOQ {_n(linea.cantidad_eoq, 2)}, ajustada a paquetes de {m.unidades_paquete})",
        ]
        if linea.mu_d and linea.tiempo_entrega and (linea.existencia_aviso or 0) / linea.mu_d < linea.tiempo_entrega:
            partes.append(
                f"⏱️ Alcanza para {(linea.existencia_aviso or 0) / linea.mu_d:.1f} días y el pedido tarda {_n(linea.tiempo_entrega)}: riesgo de agotarse antes."
            )
        partes.append("")
    partes.append(f"Proveedor: {proveedor.nombre if proveedor else 'sin proveedor asignado'}")
    partes.append(f"Aviso generado: {hora_local(pedido.creado):%d/%m/%Y %H:%M}")
    partes.append("")
    partes.append("Pulse «Pedido realizado» cuando haya hecho la compra por su canal habitual. Este botón no compra ni paga nada.")
    return "\n".join(partes)


def texto_aumento(e) -> str:
    a, p, m = e.aumento, e.plan, e.m
    partes = [
        _encabezado(m.corrida_id),
        f"📈 Aumento de consumo · {m.nombre_completo}",
        f"Últimos {a.dias_recientes} días: {_n(a.reciente, 2)}/día vs {_n(a.anterior, 2)}/día antes (×{a.razon:.2f}, z = {a.z:.1f}).",
    ]
    if p:
        partes += [
            f"Consumo previsto en L ({_n(e.parametros['L'].valor)} días): {_n(e.consumo_en_L)}",
            f"Proyección anual D: {_n(p.D, 0)}",
            f"Nuevo R: {_n(p.R)} → pedir al llegar a {p.R_pedido} · IS: {_n(p.IS)} · EOQ: {_n(p.Q, 2)} → sugerido {e.sugerida}",
        ]
    partes.append("Son estimaciones con los despachos registrados; no indican la causa del aumento.")
    return "\n".join(partes)


def texto_insuficiente(e, pedido: Pedido) -> str:
    return "\n".join(
        [
            _encabezado(e.m.corrida_id),
            f"⚠️ El pedido #{pedido.id} ya no cubre la necesidad · {e.m.nombre_completo}",
            f"Posición (existencia + en camino): {e.posicion} · Nuevo R: {_n(e.plan.R)} → pedir al llegar a {e.plan.R_pedido}",
            f"Sugerido adicional: {e.sugerida}",
        ]
    )


def texto_atraso(e, pedido: Pedido) -> str:
    return "\n".join(
        [
            _encabezado(e.m.corrida_id),
            f"⏰ Pedido #{pedido.id} atrasado · {e.m.nombre_completo}",
            f"Se esperaba el {hora_local(pedido.fecha_estimada):%d/%m/%Y %H:%M}. Existencia: {e.existencia}.",
        ]
    )


def encolar(db: Session, tipo: str, texto: str, corrida_id=None, pedido_id=None, medicamento_id=None, con_botones=False) -> list[Mensaje]:
    a = ajustes()
    chats = a.chats_de(corrida_id)
    if a.telegram_modo == "simulado":
        estados = [("simulado", "")]
    elif not chats:
        estados = [("sin_destino", "")]
    else:
        estados = [("pendiente", chat) for chat in chats]
    mensajes = []
    for estado, chat in estados:
        mensaje = Mensaje(
            corrida_id=corrida_id,
            pedido_id=pedido_id,
            medicamento_id=medicamento_id,
            tipo=tipo,
            chat_id=chat,
            texto=texto[:4000],
            con_botones=con_botones,
            estado=estado,
            creado=ahora(),
            proximo_intento=ahora(),
            ultimo_error="Falta TELEGRAM_CHATS" if estado == "sin_destino" else "",
        )
        db.add(mensaje)
        mensajes.append(mensaje)
    return mensajes
