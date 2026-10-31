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
from app.planificacion import es_controlado


def _n(valor: float | None, decimales: int = 1) -> str:
    if valor is None:
        return "—"
    return f"{valor:,.{decimales}f}"


def _encabezado(corrida_id) -> str:
    if corrida_id is None:
        return "🏥 SALVAMED · Sanatorio El Roble"
    return f"🧪 SALVAMED · SIMULACIÓN del sanatorio (corrida #{corrida_id}) — no es inventario real"


def _plural(unidad: str, cantidad: int) -> str:
    """«vial» → «viales», «frasco IV» → «frascos IV», «monodosis» igual."""
    if cantidad == 1 or not unidad:
        return unidad
    primera, _, resto = unidad.partition(" ")
    if primera[-1] in "sx":
        plural = primera
    elif primera[-1] in "aeiouáéó":
        plural = primera + "s"
    elif primera[-1] == "z":
        plural = primera[:-1] + "ces"
    else:
        plural = primera + "es"
    return f"{plural} {resto}".strip()


def _dias(valor: float) -> str:
    return f"{valor:g}" if float(valor).is_integer() else f"{valor:.1f}"


def _lista_y(partes: list[str]) -> str:
    return partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " y " + partes[-1]


def texto_reposicion(db: Session, pedido: Pedido, evaluaciones: dict | None = None) -> str:
    """Aviso corto para compras: qué pedir, cuánto y por qué. El cálculo completo está en «Ver detalle»."""
    lineas = list(
        db.execute(
            select(PedidoLinea, Medicamento)
            .join(Medicamento, Medicamento.id == PedidoLinea.medicamento_id)
            .where(PedidoLinea.pedido_id == pedido.id)
            .order_by(PedidoLinea.id)
        ).tuples()
    )
    proveedor = db.get(Proveedor, pedido.proveedor_id) if pedido.proveedor_id else None
    nombre_proveedor = proveedor.nombre if proveedor else "sin proveedor asignado"

    def llego(linea) -> bool:
        return linea.punto_reorden is not None and (linea.posicion_aviso or 0) <= punto_de_pedido(linea.punto_reorden)

    llegaron = [(l, m) for l, m in lineas if llego(l)]
    acompanan = [(l, m) for l, m in lineas if not llego(l)]
    if pedido.origen == "manual":
        llegaron, acompanan = lineas, []

    if pedido.corrida_id is None:
        partes = [f"🏥 SALVAMED · Pedido #{pedido.id} · {nombre_proveedor}"]
    else:
        partes = [f"🧪 SIMULACIÓN (corrida #{pedido.corrida_id}) · Pedido #{pedido.id} · {nombre_proveedor} · no es inventario real"]

    def situacion(linea) -> str:
        camino = (linea.posicion_aviso or 0) - (linea.existencia_aviso or 0)
        texto = f"quedan {linea.existencia_aviso}" + (f" y vienen {camino} en camino" if camino > 0 else "")
        return f"{texto}, se pide al llegar a {punto_de_pedido(linea.punto_reorden)}"

    if pedido.origen == "manual":
        partes.append("📝 Pedido hecho a mano desde SALVAMED.")
    elif len(llegaron) == 1:
        linea, m = llegaron[0]
        partes.append(f"⚠️ {m.nombre_completo} llegó a su punto de reorden: {situacion(linea)}.")
    elif llegaron:
        partes.append("⚠️ Llegaron a su punto de reorden: " + _lista_y([f"{m.nombre_completo} ({situacion(l)})" for l, m in llegaron]) + ".")
    else:
        partes.append("⚠️ Reposición necesaria.")

    partes += ["", "Pedir:"]
    for linea, m in llegaron + acompanan:
        partes.append(f"• {linea.cantidad_pedida} {_plural(m.unidad, linea.cantidad_pedida)} · {m.nombre_completo}")
    if len(acompanan) == 1:
        partes.append("El último va en el mismo envío porque está por llegar a su punto de reorden.")
    elif acompanan:
        partes.append(f"Los últimos {len(acompanan)} van en el mismo envío porque están por llegar a su punto de reorden.")

    notas = []
    controlados = [m.nombre_completo for _, m in lineas if es_controlado(m)]
    if controlados:
        notas.append(f"🔒 Controlado: {_lista_y(controlados)}. Pedir con el formulario F-SI-f-13 del Ministerio; el plazo incluye el trámite.")
    for linea, m in lineas:
        if linea.mu_d and linea.tiempo_entrega and (linea.existencia_aviso or 0) / linea.mu_d < linea.tiempo_entrega:
            alcanza = (linea.existencia_aviso or 0) / linea.mu_d
            notas.append(f"⏱️ {m.nombre_completo}: alcanza para {alcanza:.1f} días y el pedido tarda {_dias(linea.tiempo_entrega)}.")
    if notas:
        partes += [""] + notas

    plazos = [linea.tiempo_entrega for linea, _ in lineas if linea.tiempo_entrega]
    partes.append("")
    if plazos:
        partes.append(f"Llega en unos {_dias(max(plazos))} días.")
    partes.append("Pulse «Pedido realizado» cuando haya hecho la compra. El botón no compra ni paga nada.")
    return "\n".join(partes)


def texto_emergencia(m, pedido: Pedido, servicio: str, falta: int, cantidad: int, llegada, recargo: float) -> str:
    """Aviso informativo: ya no había y un servicio crítico lo necesitaba; la emergencia ya se registró."""
    return "\n".join(
        [
            _encabezado(m.corrida_id),
            f"🚨 Emergencia · {m.nombre_completo} · Pedido #{pedido.id}",
            f"{servicio} lo necesitó y ya no había: faltaron {falta} {m.unidad}.",
            f"Se compró de emergencia {cantidad} {m.unidad}: lo que faltó más lo que se usa mientras llegaría un pedido normal.",
            f"Llega a las {hora_local(llegada):%H:%M}. Recargo: Q {recargo:,.2f}.",
            "Mensaje informativo: no hay que responder.",
        ]
    )


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


def texto_recibido(db: Session, pedido: Pedido) -> str:
    lineas = list(
        db.execute(
            select(PedidoLinea, Medicamento)
            .join(Medicamento, Medicamento.id == PedidoLinea.medicamento_id)
            .where(PedidoLinea.pedido_id == pedido.id)
            .order_by(PedidoLinea.id)
        ).tuples()
    )
    partes = [_encabezado(pedido.corrida_id), f"📦 Pedido #{pedido.id} recibido · existencia actualizada"]
    for linea, m in lineas:
        partes.append(f"• {m.nombre_completo}: +{linea.cantidad_recibida} → existencia {m.existencia}")
    return "\n".join(partes)


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
