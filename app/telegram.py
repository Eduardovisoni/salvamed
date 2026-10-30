"""Avisos por Telegram y respuestas del encargado.

Envío: la bandeja `mensajes` se procesa en segundo plano. Un fallo no deshace
la venta ni pierde el pedido: el mensaje queda con su error y se reintenta con
espera creciente (hasta 5 intentos; después se puede reintentar a mano).

Respuestas: los botones llevan el id del mensaje. Se valida el chat, el
usuario, el pedido y, en simulación, que la corrida siga activa. Cada pulsación
(callback) se registra una sola vez, así que un doble clic, un botón viejo o un
reintento de Telegram no duplican efectos.

Transportes:
- TransporteHttp habla con la API real de Telegram (necesita TELEGRAM_TOKEN).
- TransporteSimulado no sale a internet: guarda lo que se "envió". Se usa en las
  pruebas y cuando no hay credenciales (TELEGRAM_MODO=simulado). En ese modo la
  pantalla muestra el mensaje y permite responder desde la web.

No uses el mismo bot en tu computadora (polling) y en Railway (webhook): cada
uno le quitaría las actualizaciones al otro. Usa un bot de pruebas para local.
"""

import logging
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ajustes import ajustes
from app.consumo import hora_local
from app.db import (
    ABIERTOS,
    PENDIENTE,
    AccionTelegram,
    AjusteInterno,
    Corrida,
    Mensaje,
    Pedido,
    PedidoLinea,
    ahora,
)

registro = logging.getLogger("salvamed.telegram")
MAX_INTENTOS = 5


class ErrorTelegram(Exception):
    pass


class TransporteHttp:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}/"

    def llamar(self, metodo: str, datos: dict, espera: float = 15) -> dict:
        try:
            respuesta = httpx.post(self.base + metodo, json=datos, timeout=espera)
            cuerpo = respuesta.json()
        except (httpx.HTTPError, ValueError) as problema:
            raise ErrorTelegram(f"Sin respuesta de Telegram: {type(problema).__name__}") from None
        if not cuerpo.get("ok"):
            raise ErrorTelegram(str(cuerpo.get("description", "Error de Telegram"))[:300])
        return cuerpo.get("result")


class TransporteSimulado:
    """Imita la API: guarda cada llamada y devuelve ids crecientes."""

    def __init__(self):
        self.llamadas: list[tuple[str, dict]] = []
        self.fallar = False
        self._siguiente = 1000

    def llamar(self, metodo: str, datos: dict, espera: float = 15):
        if self.fallar:
            raise ErrorTelegram("Fallo simulado de red")
        self.llamadas.append((metodo, datos))
        if metodo == "sendMessage":
            self._siguiente += 1
            return {"message_id": self._siguiente}
        return True


_simulado = TransporteSimulado()


def transporte():
    a = ajustes()
    if a.telegram_modo == "simulado":
        return _simulado
    return TransporteHttp(a.telegram_token)


# ---------------------------------------------------------------- Envío


def botones(db: Session, mensaje: Mensaje) -> dict | None:
    if not mensaje.con_botones or not mensaje.pedido_id:
        return None
    pedido = db.get(Pedido, mensaje.pedido_id)
    filas = []
    if pedido and pedido.estado == PENDIENTE:
        filas.append([{"text": "✅ Pedido realizado", "callback_data": f"s:{mensaje.id}:ok"}])
    fila = []
    if pedido and pedido.estado in ABIERTOS:
        fila.append({"text": "✏️ Cambiar cantidad", "callback_data": f"s:{mensaje.id}:cant"})
    url = ajustes().url_publica
    if url.startswith("https://"):
        destino = f"{url}/simulacion" if mensaje.corrida_id else f"{url}/pedidos/{mensaje.pedido_id}"
        fila.append({"text": "🔎 Ver detalle", "url": destino})
    else:
        fila.append({"text": "🔎 Ver detalle", "callback_data": f"s:{mensaje.id}:ver"})
    filas.append(fila)
    return {"inline_keyboard": filas}


def enviar_pendientes(db: Session, via=None) -> int:
    via = via or transporte()
    momento = ahora()
    enviados = 0
    pendientes = db.scalars(
        select(Mensaje)
        .where(Mensaje.estado == "pendiente", Mensaje.proximo_intento <= momento)
        .order_by(Mensaje.id)
        .limit(20)
    ).all()
    for mensaje in pendientes:
        datos = {"chat_id": mensaje.chat_id, "text": mensaje.texto}
        teclado = botones(db, mensaje)
        if teclado:
            datos["reply_markup"] = teclado
        try:
            resultado = via.llamar("sendMessage", datos)
        except ErrorTelegram as problema:
            mensaje.intentos += 1
            mensaje.ultimo_error = str(problema)[:400]
            if mensaje.intentos >= MAX_INTENTOS:
                mensaje.estado = "fallido"
            else:
                mensaje.proximo_intento = momento + timedelta(seconds=15 * 2 ** (mensaje.intentos - 1))
        else:
            mensaje.estado = "enviado"
            mensaje.intentos += 1
            mensaje.telegram_message_id = (resultado or {}).get("message_id")
            mensaje.enviado_en = momento
            mensaje.ultimo_error = ""
            enviados += 1
        db.commit()
    return enviados


# ---------------------------------------------------------------- Respuestas


def nombre_de(usuario: dict) -> str:
    nombre = " ".join(x for x in (usuario.get("first_name"), usuario.get("last_name")) if x)
    if usuario.get("username"):
        nombre = f"{nombre} (@{usuario['username']})".strip()
    return (nombre or f"usuario {usuario.get('id')}")[:120]


def _permitido(mensaje: Mensaje, chat_id: str, usuario_id: str) -> str | None:
    a = ajustes()
    if mensaje.chat_id and mensaje.chat_id != chat_id:
        return "Este botón no corresponde a este chat."
    if chat_id not in a.chats_de(mensaje.corrida_id):
        return "Este chat no está autorizado para gestionar pedidos."
    if a.usuarios and usuario_id not in a.usuarios:
        return "Tu usuario no está autorizado para gestionar pedidos."
    return None


def _corrida_vigente(db: Session, mensaje: Mensaje) -> bool:
    if mensaje.corrida_id is None:
        return True
    corrida = db.get(Corrida, mensaje.corrida_id)
    return corrida is not None and corrida.estado == "activa"


def _primera_linea(db: Session, pedido: Pedido) -> PedidoLinea | None:
    return db.scalar(select(PedidoLinea).where(PedidoLinea.pedido_id == pedido.id).order_by(PedidoLinea.id))


def _clave_espera(chat_id: str, usuario_id: str) -> str:
    return f"espera:{chat_id}:{usuario_id}"


def _actualizar_mensaje(db: Session, mensaje: Mensaje, via, agregado: str) -> None:
    mensaje.texto = (mensaje.texto + "\n\n" + agregado)[:4000]
    db.commit()
    if mensaje.telegram_message_id and mensaje.chat_id:
        datos = {"chat_id": mensaje.chat_id, "message_id": mensaje.telegram_message_id, "text": mensaje.texto}
        teclado = botones(db, mensaje)
        datos["reply_markup"] = teclado or {"inline_keyboard": []}
        try:
            via.llamar("editMessageText", datos)
        except ErrorTelegram as problema:
            registro.warning("No se pudo editar el mensaje %s: %s", mensaje.id, problema)


def responder_boton(db: Session, consulta: dict, via) -> str:
    """Procesa la pulsación de un botón. Devuelve el texto que ve quien pulsó."""
    from app import pedidos as servicio

    callback_id = str(consulta.get("id", ""))
    usuario = consulta.get("from", {}) or {}
    chat_id = str(((consulta.get("message") or {}).get("chat") or {}).get("id", ""))
    datos = str(consulta.get("data", ""))
    accion = AccionTelegram(
        callback_id=callback_id, chat_id=chat_id, usuario=nombre_de(usuario), datos=datos[:80], fecha=ahora()
    )
    db.add(accion)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return "Esta acción ya se había procesado."

    def terminar(texto: str) -> str:
        accion.resultado = texto[:200]
        db.commit()
        return texto

    partes = datos.split(":")
    if len(partes) != 3 or partes[0] != "s" or not partes[1].isdigit():
        return terminar("Botón no reconocido.")
    mensaje = db.get(Mensaje, int(partes[1]))
    if mensaje is None or not mensaje.pedido_id:
        return terminar("Este aviso ya no existe.")
    accion.mensaje_id = mensaje.id
    problema = _permitido(mensaje, chat_id, str(usuario.get("id", "")))
    if problema:
        return terminar(problema)
    if not _corrida_vigente(db, mensaje):
        return terminar("Esa corrida de simulación ya terminó o se reinició: el botón ya no tiene efecto.")
    pedido = db.get(Pedido, mensaje.pedido_id)
    if pedido is None:
        return terminar("Ese pedido ya no existe.")

    if partes[2] == "ok":
        cambio, texto = servicio.confirmar(db, pedido, nombre_de(usuario))
        if cambio:
            _actualizar_mensaje(
                db, mensaje, via, f"✅ Pedido realizado registrado por {nombre_de(usuario)} · {hora_local(ahora()):%d/%m %H:%M}"
            )
        return terminar(texto)
    if partes[2] == "cant":
        if pedido.estado not in ABIERTOS:
            return terminar(f"El pedido #{pedido.id} ya está {pedido.estado}.")
        linea = _primera_linea(db, pedido)
        clave = _clave_espera(chat_id, str(usuario.get("id", "")))
        espera = db.get(AjusteInterno, clave) or AjusteInterno(clave=clave)
        espera.valor = f"{pedido.id}:{linea.id}"
        db.merge(espera)
        db.commit()
        try:
            via.llamar(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": f"Pedido #{pedido.id}: escribe la cantidad efectivamente pedida (ahora: {linea.cantidad_pedida}).",
                    "reply_markup": {"force_reply": True, "selective": True},
                },
            )
        except ErrorTelegram as problema:
            return terminar(f"No se pudo pedir la cantidad: {problema}")
        return terminar("Escribe la nueva cantidad como respuesta.")
    if partes[2] == "ver":
        linea = _primera_linea(db, pedido)
        return terminar(
            f"Pedido #{pedido.id} · {pedido.estado}. Cantidad: {linea.cantidad_pedida}. "
            f"R {linea.punto_reorden or 0:.1f}, existencia al aviso {linea.existencia_aviso}."
        )
    return terminar("Botón no reconocido.")


def responder_texto(db: Session, mensaje: dict, via) -> None:
    from app import pedidos as servicio
    from app.inventario import ErrorDeOperacion

    chat_id = str((mensaje.get("chat") or {}).get("id", ""))
    usuario = mensaje.get("from", {}) or {}
    texto = str(mensaje.get("text", "")).strip()

    def decir(contenido: str) -> None:
        try:
            via.llamar("sendMessage", {"chat_id": chat_id, "text": contenido})
        except ErrorTelegram as problema:
            registro.warning("No se pudo responder: %s", problema)

    if texto.startswith("/start") or texto.startswith("/id"):
        decir(
            f"Hola, soy el bot de SALVAMED. Este chat tiene el id {chat_id}. "
            "Para recibir los avisos, agrégalo a TELEGRAM_CHATS (o TELEGRAM_CHATS_SIMULACION)."
        )
        return
    clave = _clave_espera(chat_id, str(usuario.get("id", "")))
    espera = db.get(AjusteInterno, clave)
    if espera is None or not espera.valor:
        return
    if not texto.isdigit():
        decir("Escribe solo la cantidad, por ejemplo 60.")
        return
    pedido_id, linea_id = (int(x) for x in espera.valor.split(":"))
    linea, pedido = db.get(PedidoLinea, linea_id), db.get(Pedido, pedido_id)
    db.delete(espera)
    db.commit()
    if linea is None or pedido is None:
        decir("Ese pedido ya no existe.")
        return
    if chat_id not in ajustes().chats_de(pedido.corrida_id):
        decir("Este chat no está autorizado.")
        return
    try:
        servicio.cambiar_cantidad(db, linea, int(texto))
    except ErrorDeOperacion as problema:
        decir(str(problema))
        return
    decir(f"Listo: pedido #{pedido.id} con {texto} unidades, cambiado por {nombre_de(usuario)}.")


def manejar_update(db: Session, update: dict, via=None) -> None:
    via = via or transporte()
    if "callback_query" in update:
        consulta = update["callback_query"]
        texto = responder_boton(db, consulta, via)
        try:
            via.llamar("answerCallbackQuery", {"callback_query_id": consulta.get("id"), "text": texto[:190], "show_alert": len(texto) > 60})
        except ErrorTelegram as problema:
            registro.warning("No se pudo contestar el botón: %s", problema)
    elif "message" in update:
        responder_texto(db, update["message"], via)


# ---------------------------------------------------------------- Recepción de actualizaciones


def _offset(db: Session) -> int:
    fila = db.get(AjusteInterno, "telegram_offset")
    return int(fila.valor) if fila and fila.valor.isdigit() else 0


def _guardar_offset(db: Session, valor: int) -> None:
    db.merge(AjusteInterno(clave="telegram_offset", valor=str(valor)))
    db.commit()


def tiene_webhook(via) -> str:
    info = via.llamar("getWebhookInfo", {})
    return (info or {}).get("url", "")


def leer_actualizaciones(db: Session, via, espera: int = 20) -> int:
    """Un ciclo de long polling (modo local). Devuelve cuántas actualizaciones procesó."""
    updates = via.llamar(
        "getUpdates",
        {"offset": _offset(db), "timeout": espera, "allowed_updates": ["message", "callback_query"]},
        espera=espera + 10,
    ) or []
    for update in updates:
        try:
            manejar_update(db, update, via)
        except Exception:  # una actualización rara no debe detener el bot
            db.rollback()
            registro.exception("Error procesando la actualización %s", update.get("update_id"))
        _guardar_offset(db, int(update["update_id"]) + 1)
    return len(updates)


def configurar_webhook(via) -> None:
    a = ajustes()
    via.llamar(
        "setWebhook",
        {
            "url": f"{a.url_publica}/telegram/webhook",
            "secret_token": a.secreto_webhook,
            "allowed_updates": ["message", "callback_query"],
        },
    )
