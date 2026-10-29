"""Pantallas de operación: venta, inventario, medicamento, pedidos y recepción."""

import secrets
from datetime import datetime, time, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import despacho, dinero, graficas, inventario, pedidos
from app.consumo import GUATEMALA, dia_local
from app.db import (
    PENDIENTE,
    Medicamento,
    Mensaje,
    Movimiento,
    Pedido,
    PedidoLinea,
    PlanDiario,
    Proveedor,
    Venta,
    ahora,
    en_ambito,
    sesion,
)
from app.formulario import GRUPOS_MEDICAMENTO, campos, leer, valores_de
from app.inventario import ErrorDeOperacion, LineaPedida
from app.planificacion import configuracion, evaluar, evaluar_varios
from app.web import medicamento_de, entero, pagina

rutas = APIRouter()
ORDEN = {"pedir": 0, "pronto": 1, "pendiente": 2, "bien": 3}


def _sin_tildes(texto: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if not unicodedata.combining(c))


def _activos(db: Session, corrida_id=None) -> list[Medicamento]:
    return list(
        db.scalars(
            select(Medicamento).where(en_ambito(Medicamento.corrida_id, corrida_id), Medicamento.activo)
        )
    )


def _evaluaciones(db: Session, corrida_id=None, momento=None):
    medicamentos = _activos(db, corrida_id)
    evaluaciones = list(evaluar_varios(db, medicamentos, momento or ahora()).values())
    evaluaciones.sort(
        key=lambda e: (
            ORDEN.get(e.estado.clave, 9),
            e.estado.dias_para_reorden if e.estado.dias_para_reorden is not None else 1e9,
            _sin_tildes(e.m.nombre_completo),
        )
    )
    return evaluaciones


def _evaluar_despues(db: Session, ids: set[int], momento: datetime) -> list[Pedido]:
    medicamentos = [db.get(Medicamento, i) for i in ids]
    _, nuevos = pedidos.evaluar_grupo(db, [m for m in medicamentos if m is not None], momento)
    return nuevos


# ---------------------------------------------------------------- Venta


def _catalogo_para_venta(evaluaciones) -> list[dict]:
    return [
        {
            "id": e.m.id,
            "nombre": e.m.nombre,
            "presentacion": e.m.presentacion,
            "unidad": e.m.unidad,
            "sku": e.m.sku,
            "existencia": e.m.existencia,
            "precio": e.m.precio_venta,
            "estado": e.estado.clave,
        }
        for e in sorted(evaluaciones, key=lambda e: _sin_tildes(e.m.nombre_completo))
    ]


def _pagina_venta(request, db: Session, carrito=None, error="", venta=None, estado=200, agregar=None):
    momento = ahora()
    evaluaciones = _evaluaciones(db, momento=momento)
    conteo = {clave: 0 for clave in ORDEN}
    for e in evaluaciones:
        conteo[e.estado.clave] = conteo.get(e.estado.clave, 0) + 1
    inicio_hoy = datetime.combine(dia_local(momento), time(), GUATEMALA)
    recientes = list(
        db.scalars(select(Venta).where(Venta.corrida_id.is_(None)).order_by(Venta.id.desc()).limit(8))
    )
    nuevos = []
    if venta is not None:
        nuevos = [
            (p, pedidos.lineas_de(db, p))
            for p in db.scalars(
                select(Pedido).where(Pedido.corrida_id.is_(None), Pedido.creado >= venta.fecha, Pedido.origen == "automatico")
            )
        ]
    return pagina(
        request,
        "venta.html",
        {
            "pestana": "venta",
            "catalogo": _catalogo_para_venta(evaluaciones),
            "carrito": carrito or [],
            "agregar": agregar,
            "token": secrets.token_urlsafe(16),
            "error": error,
            "venta": venta,
            "lineas_venta": inventario.lineas_de(db, venta) if venta else [],
            "nuevos_pedidos": nuevos,
            "conteo": conteo,
            "por_gestionar": len(pedidos.pedidos(db, None, (PENDIENTE,))),
            "hoy": dinero.resumen(db, None, inicio_hoy),
            "recientes": recientes,
        },
        estado,
    )


@rutas.get("/")
def venta(request: Request, venta: int | None = None, agregar: int | None = None, db: Session = Depends(sesion)):
    hecha = db.get(Venta, venta) if venta else None
    if hecha is not None and hecha.corrida_id is not None:
        hecha = None
    return _pagina_venta(request, db, venta=hecha, agregar=agregar)


@rutas.post("/ventas")
async def cobrar(request: Request, tareas: BackgroundTasks, db: Session = Depends(sesion)):
    formulario = await request.form()
    ids = formulario.getlist("medicamento_id")
    cantidades = formulario.getlist("cantidad")
    lineas, carrito = [], []
    error = ""
    for medicamento_id, cantidad in zip(ids, cantidades):
        mid, n = entero(medicamento_id), entero(cantidad)
        carrito.append({"id": mid, "cantidad": cantidad})
        if mid is None or n is None:
            error = "Revisa las cantidades: deben ser números enteros."
            continue
        lineas.append(LineaPedida(mid, n))
    if not error:
        try:
            momento = ahora()
            hecha = inventario.vender(db, lineas, str(formulario.get("token", "")), momento)
        except ErrorDeOperacion as problema:
            error = str(problema)
    if error:
        return _pagina_venta(request, db, carrito=carrito, error=error, estado=400)
    if hecha.fecha == momento:
        nuevos = _evaluar_despues(db, {m.id for _, m in inventario.lineas_de(db, hecha)}, momento)
        if nuevos:
            despacho.programar(tareas)
    return RedirectResponse(f"/?venta={hecha.id}", status_code=303)


@rutas.get("/ventas")
def ventas(request: Request, db: Session = Depends(sesion)):
    momento = ahora()
    inicio_hoy = datetime.combine(dia_local(momento), time(), GUATEMALA)
    return pagina(
        request,
        "ventas.html",
        {
            "pestana": "ventas",
            "hoy": dinero.resumen(db, None, inicio_hoy),
            "mes": dinero.resumen(db, None, inicio_hoy - timedelta(days=29)),
            "total": dinero.resumen(db, None),
            "ventas": list(db.scalars(select(Venta).where(Venta.corrida_id.is_(None)).order_by(Venta.id.desc()).limit(60))),
        },
    )


def _venta_de(db: Session, venta_id: int) -> Venta:
    hecha = db.get(Venta, venta_id)
    if hecha is None or hecha.corrida_id is not None:
        raise HTTPException(status_code=404, detail="Esa venta no existe.")
    return hecha


@rutas.get("/ventas/{venta_id}")
def detalle_venta(request: Request, venta_id: int, error: str = "", db: Session = Depends(sesion)):
    hecha = _venta_de(db, venta_id)
    return pagina(
        request,
        "venta_detalle.html",
        {"pestana": "ventas", "venta": hecha, "lineas": inventario.lineas_de(db, hecha), "error": error},
    )


@rutas.post("/ventas/{venta_id}/anular")
async def anular(request: Request, venta_id: int, db: Session = Depends(sesion)):
    hecha = _venta_de(db, venta_id)
    motivo = str((await request.form()).get("motivo", ""))
    try:
        inventario.anular_venta(db, hecha, motivo, ahora())
    except ErrorDeOperacion as problema:
        return pagina(
            request,
            "venta_detalle.html",
            {"pestana": "ventas", "venta": hecha, "lineas": inventario.lineas_de(db, hecha), "error": str(problema)},
            400,
        )
    _evaluar_despues(db, {m.id for _, m in inventario.lineas_de(db, hecha)}, ahora())
    return RedirectResponse(f"/ventas/{venta_id}", status_code=303)


# ---------------------------------------------------------------- Inventario


@rutas.get("/medicamentos")
def inventario_pantalla(request: Request, db: Session = Depends(sesion)):
    inactivos = list(
        db.scalars(select(Medicamento).where(Medicamento.corrida_id.is_(None), Medicamento.activo.is_(False)))
    )
    return pagina(
        request,
        "inventario.html",
        {"pestana": "inventario", "filas": _evaluaciones(db), "inactivos": inactivos, "cfg": configuracion(db)},
    )


def _proveedores(db: Session, corrida_id=None) -> list[Proveedor]:
    return list(
        db.scalars(select(Proveedor).where(en_ambito(Proveedor.corrida_id, corrida_id)).order_by(Proveedor.nombre))
    )


def _formulario(request, db, valores, errores, m=None, estado=200):
    grupos = [g for g in GRUPOS_MEDICAMENTO if not (m and g.solo_alta)]
    return pagina(
        request,
        "formulario.html",
        {
            "pestana": "editar" if m else "nuevo",
            "grupos": grupos,
            "valores": valores,
            "errores": errores,
            "medicamento": m,
            "proveedores": _proveedores(db),
            "cfg": configuracion(db),
        },
        estado,
    )


@rutas.get("/medicamentos/nuevo")
def nuevo(request: Request, db: Session = Depends(sesion)):
    return _formulario(request, db, {"unidades_paquete": "1", "existencia_inicial": "0"}, {})


def _leer_medicamento(db, formulario, m=None):
    lista = campos([g for g in GRUPOS_MEDICAMENTO if not (m and g.solo_alta)])
    validos = {p.id for p in _proveedores(db)}
    valores, datos, errores = leer(formulario, lista, validos)
    if "nombre" in datos and "presentacion" in datos and inventario.nombre_repetido(
        db, datos["nombre"], datos["presentacion"], None, m.id if m else None
    ):
        errores["nombre"] = "Ya existe un medicamento con ese nombre y presentación."
    if datos.get("unidades_paquete") is None:
        datos["unidades_paquete"] = 1
    return valores, datos, errores


@rutas.post("/medicamentos")
async def crear(request: Request, db: Session = Depends(sesion)):
    valores, datos, errores = _leer_medicamento(db, await request.form())
    if errores:
        return _formulario(request, db, valores, errores, estado=400)
    existencia = datos.pop("existencia_inicial") or 0
    m = inventario.dar_de_alta(db, datos, existencia, ahora())
    db.commit()
    pedidos.evaluar_reposicion(db, m, ahora())
    return RedirectResponse(f"/medicamentos/{m.id}?guardado=1", status_code=303)


def _grafica_existencias(db: Session, m: Medicamento, momento: datetime, dias: int = 30):
    desde = datetime.combine(dia_local(momento) - timedelta(days=dias - 1), time(), GUATEMALA)
    serie = [(f, e) for f, e, _, _ in inventario.serie_de_existencias(db, m)]
    planes = [
        (p.dia, p.punto_reorden, p.inventario_seguridad)
        for p in db.scalars(select(PlanDiario).where(PlanDiario.medicamento_id == m.id).order_by(PlanDiario.dia))
    ]
    marcas = []
    for pedido in db.scalars(
        select(Pedido).join(PedidoLinea, PedidoLinea.pedido_id == Pedido.id).where(PedidoLinea.medicamento_id == m.id)
    ):
        if pedido.realizado_en:
            linea = db.scalar(select(PedidoLinea).where(PedidoLinea.pedido_id == pedido.id, PedidoLinea.medicamento_id == m.id))
            marcas.append((pedido.realizado_en, "pedido", f"Pedido #{pedido.id}: {linea.cantidad_pedida}"))
    for mv in db.scalars(select(Movimiento).where(Movimiento.medicamento_id == m.id, Movimiento.tipo == "recepcion")):
        marcas.append((mv.fecha, "recepcion", f"Recepción +{mv.cantidad}"))
    return graficas.existencias(serie, planes, marcas, desde, momento)


@rutas.get("/medicamentos/{medicamento_id}")
def detalle(request: Request, medicamento_id: int, guardado: int = 0, db: Session = Depends(sesion)):
    m = medicamento_de(db, medicamento_id)
    momento = ahora()
    e = evaluar(db, m, momento)
    lineas = list(
        db.execute(
            select(PedidoLinea, Pedido)
            .join(Pedido, Pedido.id == PedidoLinea.pedido_id)
            .where(PedidoLinea.medicamento_id == m.id)
            .order_by(Pedido.id.desc())
            .limit(10)
        ).tuples()
    )
    return pagina(
        request,
        "detalle.html",
        {
            "pestana": "",
            "e": e,
            "m": m,
            "proveedor": db.get(Proveedor, m.proveedor_id) if m.proveedor_id else None,
            "grafica": _grafica_existencias(db, m, momento),
            "consumo_grafica": graficas.consumo_diario(e.consumo, e.hoy),
            "historial": inventario.historial(db, m),
            "pedidos_med": lineas,
            "guardado": guardado == 1,
            "cfg": configuracion(db),
        },
    )


@rutas.get("/medicamentos/{medicamento_id}/editar")
def editar(request: Request, medicamento_id: int, db: Session = Depends(sesion)):
    m = medicamento_de(db, medicamento_id)
    lista = campos([g for g in GRUPOS_MEDICAMENTO if not g.solo_alta])
    return _formulario(request, db, valores_de(m, lista), {}, m)


@rutas.post("/medicamentos/{medicamento_id}")
async def actualizar(request: Request, medicamento_id: int, db: Session = Depends(sesion)):
    m = medicamento_de(db, medicamento_id)
    valores, datos, errores = _leer_medicamento(db, await request.form(), m)
    if errores:
        return _formulario(request, db, valores, errores, m, 400)
    for campo, valor in datos.items():
        setattr(m, campo, valor)
    if m.costo_promedio is None:
        m.costo_promedio = m.costo_compra
    m.actualizado = ahora()
    db.commit()
    pedidos.evaluar_reposicion(db, m, ahora())
    return RedirectResponse(f"/medicamentos/{m.id}?guardado=1", status_code=303)


@rutas.post("/medicamentos/{medicamento_id}/activo")
async def cambiar_activo(request: Request, medicamento_id: int, db: Session = Depends(sesion)):
    m = medicamento_de(db, medicamento_id)
    m.activo = (await request.form()).get("activo") == "1"
    db.commit()
    return RedirectResponse(f"/medicamentos/{m.id}", status_code=303)


@rutas.get("/medicamentos/{medicamento_id}/ajuste")
def ajuste(request: Request, medicamento_id: int, db: Session = Depends(sesion)):
    m = medicamento_de(db, medicamento_id)
    return pagina(request, "ajuste.html", {"pestana": "", "m": m, "valores": {}, "error": ""})


@rutas.post("/medicamentos/{medicamento_id}/ajuste")
async def guardar_ajuste(request: Request, medicamento_id: int, db: Session = Depends(sesion)):
    m = medicamento_de(db, medicamento_id)
    formulario = await request.form()
    valores = {"existencia": str(formulario.get("existencia", "")), "motivo": str(formulario.get("motivo", ""))}
    nueva = entero(valores["existencia"])
    try:
        if nueva is None:
            raise ErrorDeOperacion("Escribe la existencia contada como número entero.")
        inventario.ajustar(db, m, nueva, valores["motivo"], ahora())
    except ErrorDeOperacion as problema:
        return pagina(request, "ajuste.html", {"pestana": "", "m": m, "valores": valores, "error": str(problema)}, 400)
    pedidos.evaluar_reposicion(db, m, ahora())
    return RedirectResponse(f"/medicamentos/{m.id}", status_code=303)


@rutas.post("/medicamentos/{medicamento_id}/pedido")
async def pedido_manual(request: Request, medicamento_id: int, tareas: BackgroundTasks, db: Session = Depends(sesion)):
    m = medicamento_de(db, medicamento_id)
    e = evaluar(db, m, ahora())
    if e.pedido_abierto is not None:
        return RedirectResponse(f"/pedidos/{e.pedido_abierto.id}", status_code=303)
    cantidad = entero((await request.form()).get("cantidad")) or e.sugerida
    if not cantidad or cantidad < 1:
        raise HTTPException(status_code=400, detail="Escribe una cantidad.")
    from app import avisos

    pedido = pedidos.crear_pedido(db, e, ahora(), origen="manual", cantidad=cantidad)
    avisos.encolar(db, "reposicion", avisos.texto_reposicion(db, pedido), None, pedido.id, m.id, con_botones=True)
    db.commit()
    despacho.programar(tareas)
    return RedirectResponse(f"/pedidos/{pedido.id}", status_code=303)


# ---------------------------------------------------------------- Pedidos


def _pedido_de(db: Session, pedido_id: int) -> Pedido:
    pedido = db.get(Pedido, pedido_id)
    if pedido is None or pedido.corrida_id is not None:
        raise HTTPException(status_code=404, detail="Ese pedido no existe.")
    return pedido


@rutas.get("/pedidos")
def lista_pedidos(request: Request, db: Session = Depends(sesion)):
    todos = pedidos.pedidos(db, None, limite=200)
    lineas = {p.id: pedidos.lineas_de(db, p) for p in todos}
    proveedores = {p.id: p for p in _proveedores(db)}
    return pagina(
        request,
        "pedidos.html",
        {"pestana": "pedidos", "pedidos": todos, "lineas": lineas, "proveedores": proveedores, "momento": ahora()},
    )


def _pagina_pedido(request, db: Session, pedido: Pedido, error="", estado=200):
    lineas = pedidos.lineas_de(db, pedido)
    proveedor = db.get(Proveedor, pedido.proveedor_id) if pedido.proveedor_id else None
    mismos = []
    if pedido.estado == PENDIENTE and pedido.proveedor_id:
        en_pedido = {m.id for _, m in lineas}
        mismos = [
            m
            for m in _activos(db)
            if m.proveedor_id == pedido.proveedor_id and m.id not in en_pedido
        ]
    valor = sum(l.cantidad_pedida * (l.costo_unitario or 0) for l, _ in lineas)
    return pagina(
        request,
        "pedido.html",
        {
            "pestana": "pedidos",
            "pedido": pedido,
            "lineas": lineas,
            "proveedor": proveedor,
            "mismos": mismos,
            "valor": valor,
            "mensajes": list(db.scalars(select(Mensaje).where(Mensaje.pedido_id == pedido.id).order_by(Mensaje.id))),
            "error": error,
            "momento": ahora(),
        },
        estado,
    )


@rutas.get("/pedidos/{pedido_id}")
def pedido(request: Request, pedido_id: int, db: Session = Depends(sesion)):
    return _pagina_pedido(request, db, _pedido_de(db, pedido_id))


async def _accion_pedido(request: Request, db: Session, pedido_id: int, accion):
    pedido = _pedido_de(db, pedido_id)
    formulario = await request.form()
    try:
        accion(pedido, formulario)
    except ErrorDeOperacion as problema:
        db.rollback()
        return _pagina_pedido(request, db, pedido, str(problema), 400)
    return RedirectResponse(f"/pedidos/{pedido_id}", status_code=303)


@rutas.post("/pedidos/{pedido_id}/realizado")
async def realizado(request: Request, pedido_id: int, db: Session = Depends(sesion)):
    def accion(pedido, formulario):
        cambio, mensaje = pedidos.confirmar(db, pedido, "Web (pantalla de pedidos)")
        if not cambio:
            raise ErrorDeOperacion(mensaje)

    return await _accion_pedido(request, db, pedido_id, accion)


@rutas.post("/pedidos/{pedido_id}/cancelar")
async def cancelar(request: Request, pedido_id: int, db: Session = Depends(sesion)):
    return await _accion_pedido(
        request, db, pedido_id, lambda p, f: pedidos.cancelar(db, p, ahora(), str(f.get("motivo", "")))
    )


@rutas.post("/pedidos/{pedido_id}/cantidades")
async def cantidades(request: Request, pedido_id: int, db: Session = Depends(sesion)):
    def accion(pedido, formulario):
        for linea, _ in pedidos.lineas_de(db, pedido):
            n = entero(formulario.get(f"linea_{linea.id}"))
            if n is None:
                raise ErrorDeOperacion("Las cantidades deben ser números enteros.")
            if n != linea.cantidad_pedida:
                pedidos.cambiar_cantidad(db, linea, n)

    return await _accion_pedido(request, db, pedido_id, accion)


@rutas.post("/pedidos/{pedido_id}/fecha")
async def fecha(request: Request, pedido_id: int, db: Session = Depends(sesion)):
    def accion(pedido, formulario):
        try:
            dia = datetime.fromisoformat(str(formulario.get("fecha", ""))).date()
        except ValueError:
            raise ErrorDeOperacion("Escribe una fecha válida.")
        pedidos.cambiar_fecha(db, pedido, datetime.combine(dia, time(12), GUATEMALA))

    return await _accion_pedido(request, db, pedido_id, accion)


@rutas.post("/pedidos/{pedido_id}/lineas")
async def agregar_linea(request: Request, pedido_id: int, db: Session = Depends(sesion)):
    def accion(pedido, formulario):
        mid, n = entero(formulario.get("medicamento_id")), entero(formulario.get("cantidad"))
        if mid is None or n is None:
            raise ErrorDeOperacion("Elige un medicamento y escribe la cantidad.")
        m = medicamento_de(db, mid)
        pedidos.agregar_linea(db, pedido, evaluar(db, m, ahora()), n)

    return await _accion_pedido(request, db, pedido_id, accion)


@rutas.post("/pedidos/{pedido_id}/recibir")
async def recibir(request: Request, pedido_id: int, db: Session = Depends(sesion)):
    ids = set()

    def accion(pedido, formulario):
        recibido = {}
        for linea, m in pedidos.lineas_de(db, pedido):
            n = entero(formulario.get(f"linea_{linea.id}") or "0")
            if n is None:
                raise ErrorDeOperacion("Las cantidades recibidas deben ser números enteros.")
            recibido[linea.id] = n
            ids.add(m.id)
        inventario.recibir(db, pedido, recibido, ahora(), "Web", cerrar=formulario.get("cerrar") == "1")

    respuesta = await _accion_pedido(request, db, pedido_id, accion)
    if ids and respuesta.status_code == 303:
        _evaluar_despues(db, ids, ahora())
    return respuesta


@rutas.post("/mensajes/{mensaje_id}/reintentar")
def reintentar(mensaje_id: int, tareas: BackgroundTasks, db: Session = Depends(sesion)):
    mensaje = db.get(Mensaje, mensaje_id)
    if mensaje is None:
        raise HTTPException(status_code=404, detail="Ese mensaje no existe.")
    if mensaje.estado == "fallido":
        mensaje.estado = "pendiente"
        mensaje.intentos = 0
        mensaje.proximo_intento = ahora()
        db.commit()
        despacho.programar(tareas)
    destino = f"/pedidos/{mensaje.pedido_id}" if mensaje.pedido_id and mensaje.corrida_id is None else "/configuracion#telegram"
    return RedirectResponse(destino, status_code=303)

