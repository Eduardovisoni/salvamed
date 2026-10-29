import unicodedata
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.consumo import Consumo, consumo_de, dia_local, hora_local, inicio_de_ventana
from app.db import ENTRADA, SALIDA, Medicamento, Movimiento, ahora, preparar_base, sesion
from app.formulario import (
    GRUPOS,
    SALAS,
    VALORES_NUEVO,
    leer_formulario,
    leer_movimiento,
    valores_de,
)
from app.modelo import calcular_plan, estado_de

CARPETA = Path(__file__).parent
ORDEN_DE_ESTADO = {"pedir": 0, "pronto": 1, "bien": 2}
RECIENTES = 10
PAGINAS = {
    SALIDA: {"ruta": "/", "pestana": "salidas"},
    ENTRADA: {"ruta": "/ingresos", "pestana": "ingresos"},
}


@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    preparar_base()
    yield


app = FastAPI(title="SALVAMED", lifespan=ciclo_de_vida, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=CARPETA / "static"), name="static")
plantillas = Jinja2Templates(directory=CARPETA / "templates")


def _num(valor: float, decimales: int = 2) -> str:
    return f"{valor:,.{decimales}f}"


def _corto(valor: float) -> str:
    return f"{valor:.4f}".rstrip("0").rstrip(".")


def _entero(valor: float) -> str:
    return f"{round(valor):,}"


def _unidad(unidad: str, cantidad: float) -> str:
    if cantidad != 1 or not unidad.endswith("s"):
        return unidad
    if unidad.endswith("es") and unidad[-3:-2] not in "aeiou":
        return unidad[:-2]
    return unidad[:-1]


def _dinero(valor: float) -> str:
    return f"Q {valor:,.2f}"


def _fecha(valor: datetime) -> str:
    return hora_local(valor).strftime("%d/%m/%Y %H:%M")


plantillas.env.filters.update(
    num=_num, corto=_corto, entero=_entero, dinero=_dinero, fecha=_fecha, unidad=_unidad
)
plantillas.env.globals["salas"] = SALAS


def _consumos(db: Session, medicamentos: list[Medicamento], momento: datetime) -> dict[int, Consumo]:
    ids = [m.id for m in medicamentos]
    consulta = select(Movimiento.medicamento_id, Movimiento.fecha, Movimiento.cantidad).where(
        Movimiento.tipo == SALIDA,
        Movimiento.fecha >= inicio_de_ventana(momento),
        Movimiento.medicamento_id.in_(ids),
    )
    salidas = {i: [] for i in ids}
    for medicamento_id, fecha, cantidad in db.execute(consulta):
        salidas[medicamento_id].append((fecha, cantidad))
    return {
        m.id: consumo_de(salidas[m.id], m.creado, momento, m.consumo_promedio, m.desviacion)
        for m in medicamentos
    }


def _fila(medicamento: Medicamento, consumo: Consumo, existencia: int | None = None) -> dict:
    if existencia is None:
        existencia = medicamento.existencia
    plan = calcular_plan(medicamento.datos(consumo))
    estado = estado_de(existencia, consumo.mu_d, plan)
    return {
        "m": medicamento,
        "consumo": consumo,
        "plan": plan,
        "estado": estado,
        "existencia": existencia,
    }


def _fila_actual(db: Session, medicamento: Medicamento) -> dict:
    return _fila(medicamento, _consumos(db, [medicamento], ahora())[medicamento.id])


def _grafica(consumo: Consumo, momento: datetime) -> dict:
    ancho, alto = 600, 150
    tope = max([*consumo.por_dia, consumo.mu_d + consumo.sigma_d, 1])
    paso = ancho / max(len(consumo.por_dia), 1)
    hoy = dia_local(momento)
    barras = []
    for i, cantidad in enumerate(consumo.por_dia):
        dia = hoy - timedelta(days=len(consumo.por_dia) - 1 - i)
        h = cantidad / tope * alto
        barras.append(
            {
                "x": round(i * paso + paso * 0.15, 2),
                "y": round(alto - h, 2),
                "ancho": round(paso * 0.7, 2),
                "alto": round(h, 2),
                "etiqueta": f"{dia:%d/%m}: {cantidad}",
            }
        )

    def y_de(valor: float) -> float:
        return round(alto - min(valor, tope) / tope * alto, 2)

    return {
        "ancho": ancho,
        "alto": alto,
        "barras": barras,
        "y_mu": y_de(consumo.mu_d),
        "y_arriba": y_de(consumo.mu_d + consumo.sigma_d),
        "y_abajo": y_de(max(consumo.mu_d - consumo.sigma_d, 0)),
        "primer_dia": hoy - timedelta(days=len(consumo.por_dia) - 1),
    }


def _buscar(db: Session, medicamento_id: int) -> Medicamento:
    medicamento = db.get(Medicamento, medicamento_id)
    if medicamento is None:
        raise HTTPException(status_code=404, detail="Ese medicamento no existe.")
    return medicamento


def _nombre_repetido(db: Session, nombre: str, excepto_id: int | None = None) -> bool:
    consulta = select(Medicamento.id).where(func.lower(Medicamento.nombre) == nombre.lower())
    if excepto_id is not None:
        consulta = consulta.where(Medicamento.id != excepto_id)
    return db.scalar(consulta) is not None


def _ultimo_movimiento_id(db: Session, medicamento_id: int) -> int | None:
    return db.scalar(
        select(func.max(Movimiento.id)).where(Movimiento.medicamento_id == medicamento_id)
    )


def _cambio(db: Session, movimiento_id: int | None, medicamento_id: int | None = None):
    """El movimiento recién hecho y si todavía se puede deshacer."""
    movimiento = db.get(Movimiento, movimiento_id) if movimiento_id else None
    if movimiento is None or (medicamento_id and movimiento.medicamento_id != medicamento_id):
        return None
    medicamento = db.get(Medicamento, movimiento.medicamento_id)
    deshacer = _ultimo_movimiento_id(db, medicamento.id) == movimiento.id and (
        movimiento.tipo == SALIDA or medicamento.existencia >= movimiento.cantidad
    )
    return {"movimiento": movimiento, "medicamento": medicamento, "deshacer": deshacer}


def _registrar(db: Session, medicamento: Medicamento, tipo: str, datos: dict) -> Movimiento | None:
    """Guarda el movimiento y ajusta la existencia. None si la salida es mayor que lo que hay."""
    cantidad = datos["cantidad"]
    consulta = update(Medicamento).where(Medicamento.id == medicamento.id)
    if tipo == SALIDA:
        consulta = consulta.where(Medicamento.existencia >= cantidad).values(
            existencia=Medicamento.existencia - cantidad
        )
    else:
        consulta = consulta.values(existencia=Medicamento.existencia + cantidad)
    if db.execute(consulta).rowcount == 0:
        db.rollback()
        return None
    movimiento = Movimiento(
        medicamento_id=medicamento.id, tipo=tipo, cantidad=cantidad, nota=datos["nota"], fecha=ahora()
    )
    db.add(movimiento)
    db.commit()
    db.refresh(medicamento)
    return movimiento


def _error_de_existencia(medicamento: Medicamento) -> str:
    return f"Solo hay {medicamento.existencia:,} {medicamento.unidad} en bodega."


def _recientes(
    db: Session, medicamento_id: int | None = None, tipo: str | None = None, limite: int = RECIENTES
):
    consulta = (
        select(Movimiento, Medicamento.nombre, Medicamento.unidad)
        .join(Medicamento, Medicamento.id == Movimiento.medicamento_id)
        .order_by(Movimiento.fecha.desc(), Movimiento.id.desc())
        .limit(limite)
    )
    if medicamento_id is not None:
        consulta = consulta.where(Movimiento.medicamento_id == medicamento_id)
    if tipo is not None:
        consulta = consulta.where(Movimiento.tipo == tipo)
    return db.execute(consulta).all()


def _formulario(request: Request, valores, errores, medicamento=None, estado=200):
    return plantillas.TemplateResponse(
        request,
        "formulario.html",
        {
            "grupos": GRUPOS,
            "valores": valores,
            "errores": errores,
            "medicamento": medicamento,
            "pestana": "editar" if medicamento else "nuevo",
        },
        status_code=estado,
    )


def _sin_tildes(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def _filas(db: Session) -> list[dict]:
    medicamentos = db.scalars(select(Medicamento)).all()
    consumos = _consumos(db, medicamentos, ahora())
    filas = [_fila(m, consumos[m.id]) for m in medicamentos]
    filas.sort(
        key=lambda f: (
            ORDEN_DE_ESTADO[f["estado"].clave],
            f["estado"].dias_para_reorden,
            _sin_tildes(f["m"].nombre),
        )
    )
    return filas


def _pagina_movimiento(request: Request, db: Session, tipo: str, valores, errores, hecho=None, estado=200):
    filas = _filas(db)
    conteo = {clave: 0 for clave in ORDEN_DE_ESTADO}
    for f in filas:
        conteo[f["estado"].clave] += 1
    return plantillas.TemplateResponse(
        request,
        "movimiento.html",
        {
            "tipo": tipo,
            "conteo": conteo,
            "costo_total": sum(f["plan"].CT for f in filas),
            "total": len(filas),
            "opciones": sorted(filas, key=lambda f: _sin_tildes(f["m"].nombre)),
            "valores": valores,
            "errores": errores,
            "cambio": _cambio(db, hecho),
            "recientes": _recientes(db, tipo=tipo),
            "volver": tipo,
            "pestana": PAGINAS[tipo]["pestana"],
        },
        status_code=estado,
    )


def _valores_vacios(medicamento: int | None) -> dict[str, str]:
    return {"medicamento_id": str(medicamento or ""), "cantidad": "", "nota": ""}


async def _registrar_desde_formulario(request: Request, db: Session, tipo: str):
    valores, datos, errores = leer_movimiento(await request.form(), con_sala=tipo == SALIDA)
    medicamento = None
    if valores["medicamento_id"].isdigit():
        medicamento = db.get(Medicamento, int(valores["medicamento_id"]))
    if medicamento is None:
        errores["medicamento_id"] = "Elige un medicamento."
    elif tipo == SALIDA and "cantidad" in datos and datos["cantidad"] > medicamento.existencia:
        errores["cantidad"] = _error_de_existencia(medicamento)
    movimiento = None if errores else _registrar(db, medicamento, tipo, datos)
    if movimiento is None:
        if not errores:
            errores["cantidad"] = _error_de_existencia(medicamento)
        return _pagina_movimiento(request, db, tipo, valores, errores, estado=400)
    return RedirectResponse(f"{PAGINAS[tipo]['ruta']}?hecho={movimiento.id}", status_code=303)


@app.get("/salud")
def salud():
    return {"ok": True}


@app.get("/")
def inicio(
    request: Request,
    medicamento: int | None = None,
    hecho: int | None = None,
    db: Session = Depends(sesion),
):
    return _pagina_movimiento(request, db, SALIDA, _valores_vacios(medicamento), {}, hecho)


@app.get("/salidas")
def salidas(request: Request):
    consulta = request.url.query
    return RedirectResponse("/" + (f"?{consulta}" if consulta else ""), status_code=307)


@app.post("/salidas")
async def registrar_salida(request: Request, db: Session = Depends(sesion)):
    return await _registrar_desde_formulario(request, db, SALIDA)


@app.get("/ingresos")
def ingresos(
    request: Request,
    medicamento: int | None = None,
    hecho: int | None = None,
    db: Session = Depends(sesion),
):
    return _pagina_movimiento(request, db, ENTRADA, _valores_vacios(medicamento), {}, hecho)


@app.post("/ingresos")
async def registrar_ingreso(request: Request, db: Session = Depends(sesion)):
    return await _registrar_desde_formulario(request, db, ENTRADA)


@app.get("/medicamentos")
def inventario(request: Request, db: Session = Depends(sesion)):
    return plantillas.TemplateResponse(
        request, "inventario.html", {"filas": _filas(db), "pestana": "inventario"}
    )


@app.post("/movimientos/{movimiento_id}/deshacer")
async def deshacer(request: Request, movimiento_id: int, db: Session = Depends(sesion)):
    movimiento = db.get(Movimiento, movimiento_id)
    if movimiento is None:
        raise HTTPException(status_code=404, detail="Ese movimiento ya no existe.")
    medicamento_id = movimiento.medicamento_id
    if _ultimo_movimiento_id(db, medicamento_id) != movimiento.id:
        raise HTTPException(status_code=409, detail="Solo se puede deshacer el último movimiento.")
    cambio = movimiento.cantidad if movimiento.tipo == SALIDA else -movimiento.cantidad
    consulta = (
        update(Medicamento)
        .where(Medicamento.id == medicamento_id, Medicamento.existencia + cambio >= 0)
        .values(existencia=Medicamento.existencia + cambio)
    )
    if db.execute(consulta).rowcount == 0:
        db.rollback()
        raise HTTPException(status_code=409, detail="La existencia quedaría negativa.")
    db.delete(movimiento)
    db.commit()
    pagina = PAGINAS.get((await request.form()).get("volver"))
    if pagina:
        return RedirectResponse(f"{pagina['ruta']}?medicamento={medicamento_id}", status_code=303)
    return RedirectResponse(f"/medicamentos/{medicamento_id}", status_code=303)


@app.get("/medicamentos/nuevo")
def nuevo(request: Request):
    return _formulario(request, dict(VALORES_NUEVO), {})


@app.post("/medicamentos")
async def crear(request: Request, db: Session = Depends(sesion)):
    valores, datos, errores = leer_formulario(await request.form())
    if "nombre" in datos and _nombre_repetido(db, datos["nombre"]):
        errores["nombre"] = "Ya existe un medicamento con ese nombre."
    if errores:
        return _formulario(request, valores, errores, estado=400)
    medicamento = Medicamento(**datos)
    db.add(medicamento)
    db.commit()
    return RedirectResponse(f"/medicamentos/{medicamento.id}?guardado=1", status_code=303)


@app.get("/medicamentos/{medicamento_id}")
def detalle(
    request: Request,
    medicamento_id: int,
    guardado: int = 0,
    db: Session = Depends(sesion),
):
    fila = _fila_actual(db, _buscar(db, medicamento_id))
    return plantillas.TemplateResponse(
        request,
        "detalle.html",
        {
            **fila,
            "grafica": _grafica(fila["consumo"], ahora()),
            "historial": _recientes(db, medicamento_id, limite=20),
            "guardado": guardado == 1,
            "pestana": "",
        },
    )


@app.get("/medicamentos/{medicamento_id}/editar")
def editar(request: Request, medicamento_id: int, db: Session = Depends(sesion)):
    medicamento = _buscar(db, medicamento_id)
    return _formulario(request, valores_de(medicamento), {}, medicamento)


@app.post("/medicamentos/{medicamento_id}")
async def actualizar(request: Request, medicamento_id: int, db: Session = Depends(sesion)):
    medicamento = _buscar(db, medicamento_id)
    valores, datos, errores = leer_formulario(await request.form())
    if "nombre" in datos and _nombre_repetido(db, datos["nombre"], medicamento_id):
        errores["nombre"] = "Ya existe un medicamento con ese nombre."
    if errores:
        return _formulario(request, valores, errores, medicamento, estado=400)
    for campo, valor in datos.items():
        setattr(medicamento, campo, valor)
    medicamento.actualizado = ahora()
    db.commit()
    return RedirectResponse(f"/medicamentos/{medicamento.id}?guardado=1", status_code=303)


@app.post("/medicamentos/{medicamento_id}/eliminar")
def eliminar(medicamento_id: int, db: Session = Depends(sesion)):
    medicamento = _buscar(db, medicamento_id)
    db.execute(delete(Movimiento).where(Movimiento.medicamento_id == medicamento_id))
    db.delete(medicamento)
    db.commit()
    return RedirectResponse("/medicamentos", status_code=303)
