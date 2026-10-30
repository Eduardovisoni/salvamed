"""Pantalla de simulación y su API (el navegador consulta el estado cada medio segundo)."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import pedidos, simulacion, tareas
from app.db import Mensaje, Pedido, sesion
from app.web import pagina

rutas = APIRouter()


@rutas.get("/simulacion")
def pantalla(request: Request, db: Session = Depends(sesion)):
    return pagina(
        request,
        "simulacion.html",
        {
            "pestana": "simulacion",
            "corrida": simulacion.corrida_activa(db),
            "escenarios": simulacion.ESCENARIOS,
            "velocidades": simulacion.VELOCIDADES,
            "aviso_roble": simulacion.AVISO_ROBLE,
            "recargo": simulacion.RECARGO_EMERGENCIA,
            "anteriores": simulacion.corridas_anteriores(db),
            "minutos_por_segundo": simulacion.MINUTOS_POR_SEGUNDO,
        },
    )


def _activa(db: Session):
    corrida = simulacion.corrida_activa(db)
    if corrida is None:
        raise HTTPException(status_code=409, detail="No hay una corrida activa.")
    return corrida


def _enviar_si_hace_falta(db: Session, corrida, tareas_fondo: BackgroundTasks) -> None:
    if db.scalar(select(Mensaje.id).where(Mensaje.corrida_id == corrida.id, Mensaje.estado == "pendiente")):
        tareas_fondo.add_task(tareas.enviar_ahora)


@rutas.get("/simulacion/estado")
def estado(tareas_fondo: BackgroundTasks, desde: int = 0, med: int | None = None, db: Session = Depends(sesion)):
    corrida = _activa(db)
    simulacion.paso(db, corrida)
    _enviar_si_hace_falta(db, corrida, tareas_fondo)
    return JSONResponse(simulacion.estado(db, corrida, desde, med))


@rutas.post("/simulacion/control")
async def control(request: Request, tareas_fondo: BackgroundTasks, db: Session = Depends(sesion)):
    corrida = _activa(db)
    formulario = await request.form()
    simulacion.controlar(db, corrida, str(formulario.get("accion", "")), str(formulario.get("valor", "")))
    _enviar_si_hace_falta(db, corrida, tareas_fondo)
    desde = formulario.get("desde", "0")
    med = formulario.get("med", "")
    return JSONResponse(
        simulacion.estado(db, corrida, int(desde) if str(desde).isdigit() else 0, int(med) if str(med).isdigit() else None)
    )


@rutas.post("/simulacion/nueva")
async def nueva(request: Request, db: Session = Depends(sesion)):
    formulario = await request.form()
    semilla = str(formulario.get("semilla", "")).strip()
    try:
        demora = float(str(formulario.get("demora", "0")).replace(",", ".") or 0)
    except ValueError:
        demora = 0
    simulacion.crear_corrida(
        db,
        str(formulario.get("escenario", "base")),
        int(semilla) if semilla.isdigit() else None,
        min(max(demora, 0), 72),
    )
    return RedirectResponse("/simulacion", status_code=303)


@rutas.post("/simulacion/confirmar/{pedido_id}")
def confirmar(pedido_id: int, db: Session = Depends(sesion)):
    """Respuesta del encargado desde la web, para cuando no hay Telegram real."""
    pedido = db.get(Pedido, pedido_id)
    if pedido is None or pedido.corrida_id is None:
        raise HTTPException(status_code=404, detail="Ese pedido no existe en la simulación.")
    cambio, texto = pedidos.confirmar(db, pedido, "Expositor (desde la pantalla)")
    return JSONResponse({"ok": cambio, "texto": texto})
