"""Plantillas, filtros de formato y utilidades comunes de las pantallas."""

from datetime import date, datetime
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.ajustes import ajustes
from app.consumo import hora_local
from app.db import Medicamento

CARPETA = Path(__file__).parent
plantillas = Jinja2Templates(directory=CARPETA / "templates")


def _num(valor, decimales: int = 2) -> str:
    if valor is None:
        return "—"
    return f"{valor:,.{decimales}f}"


def _corto(valor) -> str:
    if valor is None:
        return "—"
    return f"{valor:.4f}".rstrip("0").rstrip(".")


def _entero(valor) -> str:
    if valor is None:
        return "—"
    return f"{round(valor):,}"


def _unidad(unidad: str, cantidad: float) -> str:
    if cantidad != 1 or not unidad.endswith("s"):
        return unidad
    if unidad.endswith("es") and unidad[-3:-2] not in "aeiou":
        return unidad[:-2]
    return unidad[:-1]


def _dinero(valor) -> str:
    if valor is None:
        return "—"
    signo = "−" if valor < 0 else ""
    return f"{signo}Q {abs(valor):,.2f}"


def _fecha(valor) -> str:
    if valor is None:
        return "—"
    if isinstance(valor, datetime):
        return hora_local(valor).strftime("%d/%m/%Y %H:%M")
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    return str(valor)


def _dia(valor) -> str:
    if valor is None:
        return "—"
    if isinstance(valor, datetime):
        valor = hora_local(valor)
    return valor.strftime("%d/%m/%Y")


def _hora(valor) -> str:
    return hora_local(valor).strftime("%H:%M") if valor else "—"


def _iso_dia(valor) -> str:
    return hora_local(valor).date().isoformat() if valor else ""


plantillas.env.filters.update(
    num=_num, corto=_corto, entero=_entero, dinero=_dinero, fecha=_fecha, dia=_dia, hora=_hora, iso_dia=_iso_dia, unidad=_unidad
)
plantillas.env.globals["ajustes"] = ajustes

ESTADOS_PEDIDO = {
    "pendiente": "Pendiente de gestionar",
    "realizado": "Pedido realizado · en camino",
    "recibido": "Recibido",
    "cancelado": "Cancelado",
}
plantillas.env.globals["estados_pedido"] = ESTADOS_PEDIDO


def pagina(request: Request, nombre: str, contexto: dict, estado: int = 200):
    return plantillas.TemplateResponse(request, nombre, contexto, status_code=estado)


def medicamento_de(db: Session, medicamento_id: int, corrida_id=None) -> Medicamento:
    m = db.get(Medicamento, medicamento_id)
    if m is None or m.corrida_id != corrida_id:
        raise HTTPException(status_code=404, detail="Ese medicamento no existe.")
    return m


def entero(texto: str | None) -> int | None:
    try:
        valor = float(str(texto or "").strip().replace(",", ""))
    except ValueError:
        return None
    if not valor.is_integer():
        return None
    return int(valor)
