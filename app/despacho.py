"""Tareas de fondo: enviar avisos, leer respuestas de Telegram y reevaluar al cambiar de día."""

import asyncio
import logging

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import telegram
from app.ajustes import ajustes
from app.db import Medicamento, ahora, motor

registro = logging.getLogger("salvamed.despacho")


def enviar_ahora() -> None:
    if ajustes().telegram_modo == "simulado":
        return
    with Session(motor) as db:
        telegram.enviar_pendientes(db)


def programar(tareas: BackgroundTasks) -> None:
    tareas.add_task(enviar_ahora)


def reevaluar_operacion() -> None:
    """Las ventas de ayer cambian μD y R hoy aunque nadie venda: se revisa cada cierto tiempo."""
    from app import pedidos

    with Session(motor) as db:
        medicamentos = db.scalars(select(Medicamento).where(Medicamento.corrida_id.is_(None), Medicamento.activo)).all()
        pedidos.evaluar_grupo(db, list(medicamentos), ahora())


async def _bucle_envio() -> None:
    while True:
        try:
            await asyncio.to_thread(enviar_ahora)
        except Exception:
            registro.exception("Falló el envío de avisos")
        await asyncio.sleep(3)


async def _bucle_reevaluacion() -> None:
    while True:
        try:
            await asyncio.to_thread(reevaluar_operacion)
        except Exception:
            registro.exception("Falló la reevaluación")
        await asyncio.sleep(300)


def _leer_una_vez(via) -> None:
    with Session(motor) as db:
        telegram.leer_actualizaciones(db, via)


async def _bucle_polling() -> None:
    via = telegram.transporte()
    try:
        url = await asyncio.to_thread(telegram.tiene_webhook, via)
    except telegram.ErrorTelegram as problema:
        registro.error("No se pudo consultar el bot: %s", problema)
        url = ""
    if url:
        registro.error(
            "Este bot tiene un webhook activo (%s), probablemente producción. "
            "No se hará polling para no quitarle las actualizaciones: usa un bot de pruebas.",
            url,
        )
        return
    while True:
        try:
            await asyncio.to_thread(_leer_una_vez, via)
        except Exception:
            registro.exception("Falló la lectura de Telegram")
            await asyncio.sleep(5)


async def _configurar_webhook() -> None:
    try:
        await asyncio.to_thread(telegram.configurar_webhook, telegram.transporte())
    except telegram.ErrorTelegram as problema:
        registro.error("No se pudo registrar el webhook: %s", problema)


def arrancar() -> list[asyncio.Task]:
    a = ajustes()
    if not a.tareas:
        return []
    tareas = [asyncio.create_task(_bucle_envio()), asyncio.create_task(_bucle_reevaluacion())]
    if a.telegram_modo == "polling":
        tareas.append(asyncio.create_task(_bucle_polling()))
    elif a.telegram_modo == "webhook":
        tareas.append(asyncio.create_task(_configurar_webhook()))
    return tareas
