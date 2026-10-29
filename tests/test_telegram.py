from sqlalchemy import select

from app import inventario, pedidos, telegram
from app.db import AccionTelegram, Mensaje, Pedido, PedidoLinea, ahora
from app.inventario import LineaPedida
from tests.ayudas import medicamento


def _pedido_con_aviso(db):
    m = medicamento(db, existencia=40)
    inventario.vender(db, [LineaPedida(m.id, 25)], "v", ahora())
    _, pedido = pedidos.evaluar_reposicion(db, m, ahora())
    return m, pedido, db.scalar(select(Mensaje).where(Mensaje.pedido_id == pedido.id))


def _boton(mensaje, accion, callback="cb1", chat="555", usuario=77):
    return {
        "callback_query": {
            "id": callback,
            "from": {"id": usuario, "first_name": "Ana", "username": "encargada"},
            "message": {"chat": {"id": int(chat)}, "message_id": mensaje.telegram_message_id},
            "data": f"s:{mensaje.id}:{accion}",
        }
    }


def test_sin_token_el_aviso_queda_con_transporte_simulado(db):
    _, _, mensaje = _pedido_con_aviso(db)
    assert mensaje.estado == "simulado"
    assert "Pulse «Pedido realizado»" in mensaje.texto
    assert "no compra ni paga" in mensaje.texto


def test_envio_con_botones_y_ids(db, telegram_real):
    _, pedido, mensaje = _pedido_con_aviso(db)
    assert mensaje.estado == "pendiente" and mensaje.chat_id == "555"
    via = telegram.TransporteSimulado()
    assert telegram.enviar_pendientes(db, via) == 1
    db.refresh(mensaje)
    assert mensaje.estado == "enviado" and mensaje.telegram_message_id
    metodo, datos = via.llamadas[0]
    assert metodo == "sendMessage"
    textos = [b["text"] for fila in datos["reply_markup"]["inline_keyboard"] for b in fila]
    assert textos == ["✅ Pedido realizado", "✏️ Cambiar cantidad", "🔎 Ver detalle"]


def test_si_telegram_falla_se_reintenta_sin_deshacer_la_venta(db, telegram_real):
    m, pedido, mensaje = _pedido_con_aviso(db)
    via = telegram.TransporteSimulado()
    via.fallar = True
    for _ in range(telegram.MAX_INTENTOS):
        mensaje.proximo_intento = ahora()
        db.commit()
        telegram.enviar_pendientes(db, via)
        db.refresh(mensaje)
    assert mensaje.estado == "fallido" and mensaje.intentos == telegram.MAX_INTENTOS
    assert "Fallo simulado" in mensaje.ultimo_error
    db.refresh(m)
    assert m.existencia == 15
    assert db.get(Pedido, pedido.id).estado == "pendiente"


def test_pedido_realizado_desde_telegram_una_sola_vez(db, telegram_real):
    m, pedido, mensaje = _pedido_con_aviso(db)
    via = telegram.TransporteSimulado()
    telegram.enviar_pendientes(db, via)
    db.refresh(mensaje)
    telegram.manejar_update(db, _boton(mensaje, "ok"), via)
    db.refresh(pedido)
    assert pedido.estado == "realizado" and "Ana" in pedido.confirmado_por
    db.refresh(m)
    assert m.existencia == 15
    telegram.manejar_update(db, _boton(mensaje, "ok"), via)  # el mismo callback reenviado
    telegram.manejar_update(db, _boton(mensaje, "ok", callback="cb2"), via)  # doble clic
    respuestas = [d["text"] for metodo, d in via.llamadas if metodo == "answerCallbackQuery"]
    assert "ya se había procesado" in respuestas[1]
    assert "Ya estaba registrado" in respuestas[2]
    assert len(db.scalars(select(AccionTelegram)).all()) == 2


def test_chat_o_usuario_no_autorizado(db, telegram_real):
    _, pedido, mensaje = _pedido_con_aviso(db)
    via = telegram.TransporteSimulado()
    telegram.enviar_pendientes(db, via)
    db.refresh(mensaje)
    telegram.manejar_update(db, _boton(mensaje, "ok", callback="x1", usuario=999), via)
    telegram.manejar_update(db, _boton(mensaje, "ok", callback="x2", chat="123"), via)
    db.refresh(pedido)
    assert pedido.estado == "pendiente"


def test_cambiar_cantidad_respondiendo_al_bot(db, telegram_real):
    _, pedido, mensaje = _pedido_con_aviso(db)
    via = telegram.TransporteSimulado()
    telegram.enviar_pendientes(db, via)
    db.refresh(mensaje)
    telegram.manejar_update(db, _boton(mensaje, "cant"), via)
    telegram.manejar_update(db, {"message": {"chat": {"id": 555}, "from": {"id": 77, "first_name": "Ana"}, "text": "80"}}, via)
    linea = db.scalar(select(PedidoLinea).where(PedidoLinea.pedido_id == pedido.id))
    assert linea.cantidad_pedida == 80


def test_start_dice_el_id_del_chat(db):
    via = telegram.TransporteSimulado()
    telegram.manejar_update(db, {"message": {"chat": {"id": 4242}, "from": {"id": 1}, "text": "/start"}}, via)
    assert "4242" in via.llamadas[0][1]["text"]


def test_webhook_exige_el_secreto(cliente, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:prueba")
    monkeypatch.setenv("TELEGRAM_MODO", "webhook")
    monkeypatch.setenv("TELEGRAM_SECRETO_WEBHOOK", "s3creto")
    assert cliente.post("/telegram/webhook", json={}).status_code == 403
    r = cliente.post("/telegram/webhook", json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "s3creto"})
    assert r.status_code == 200
    monkeypatch.setenv("TELEGRAM_MODO", "polling")
    assert cliente.post("/telegram/webhook", json={}, headers={"X-Telegram-Bot-Api-Secret-Token": "s3creto"}).status_code == 404
