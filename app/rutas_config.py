"""Configuración, proveedores, demostración, importación y webhook de Telegram."""

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import importacion, telegram
from app.ajustes import ajustes
from app.db import Importacion, Medicamento, Proveedor, lista_de_servicios, motor, sesion
from app.formulario import CAMPOS_PROVEEDOR, GRUPOS_CONFIGURACION, campos, leer, valores_de
from app.inventario import ErrorDeOperacion
from app.planificacion import configuracion
from app.web import pagina

rutas = APIRouter()
DIAS = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")


def _pagina_config(request, db: Session, valores=None, errores=None, mensaje="", estado=200):
    cfg = configuracion(db)
    proveedores = list(db.scalars(select(Proveedor).where(Proveedor.corrida_id.is_(None)).order_by(Proveedor.nombre)))
    return pagina(
        request,
        "configuracion.html",
        {
            "pestana": "configuracion",
            "cfg": cfg,
            "grupos": GRUPOS_CONFIGURACION,
            "valores": valores or {**valores_de(cfg, campos(GRUPOS_CONFIGURACION)), "servicios": cfg.servicios},
            "errores": errores or {},
            "dias": DIAS,
            "proveedores": proveedores,
            "campos_proveedor": CAMPOS_PROVEEDOR,
            "valores_proveedor": {p.id: valores_de(p, CAMPOS_PROVEEDOR) for p in proveedores},
            "catalogo_vacio": db.scalar(select(Medicamento.id).where(Medicamento.corrida_id.is_(None))) is None,
            "mensaje": mensaje,
        },
        estado,
    )


@rutas.get("/configuracion")
def ver(request: Request, mensaje: str = "", db: Session = Depends(sesion)):
    return _pagina_config(request, db, mensaje=mensaje)


@rutas.post("/configuracion")
async def guardar(request: Request, db: Session = Depends(sesion)):
    formulario = await request.form()
    valores, datos, errores = leer(formulario, campos(GRUPOS_CONFIGURACION))
    dias_semana = "".join("1" if formulario.get(f"dia_{i}") else "0" for i in range(7))
    if dias_semana == "0000000":
        errores["dias_semana"] = "Debe operar al menos un día."
    servicios = lista_de_servicios(str(formulario.get("servicios", "")))
    if not servicios:
        errores["servicios"] = "Escribe al menos un servicio."
    elif any(len(s) > 60 for s in servicios) or len("\n".join(servicios)) > 600:
        errores["servicios"] = "Cada servicio puede tener hasta 60 caracteres."
    valores["servicios"] = str(formulario.get("servicios", ""))
    if not errores and datos["hora_cierre"] <= datos["hora_apertura"]:
        errores["hora_cierre"] = "Debe ser después de la hora de apertura."
    if errores:
        return _pagina_config(request, db, valores, errores, estado=400)
    cfg = configuracion(db)
    for campo, valor in datos.items():
        setattr(cfg, campo, valor)
    cfg.dias_semana = dias_semana
    cfg.servicios = "\n".join(dict.fromkeys(servicios))
    db.commit()
    return RedirectResponse("/configuracion?mensaje=Configuración guardada.", status_code=303)


@rutas.post("/proveedores")
async def crear_proveedor(request: Request, db: Session = Depends(sesion)):
    valores, datos, errores = leer(await request.form(), CAMPOS_PROVEEDOR)
    if errores:
        return _pagina_config(request, db, mensaje="Revisa los datos del proveedor: " + "; ".join(errores.values()), estado=400)
    datos["articulos_por_envio"] = datos.get("articulos_por_envio") or 1
    db.add(Proveedor(**datos))
    db.commit()
    return RedirectResponse("/configuracion?mensaje=Proveedor agregado.#proveedores", status_code=303)


@rutas.post("/proveedores/{proveedor_id}")
async def editar_proveedor(request: Request, proveedor_id: int, db: Session = Depends(sesion)):
    proveedor = db.get(Proveedor, proveedor_id)
    if proveedor is None or proveedor.corrida_id is not None:
        raise HTTPException(status_code=404, detail="Ese proveedor no existe.")
    valores, datos, errores = leer(await request.form(), CAMPOS_PROVEEDOR)
    if errores:
        return _pagina_config(request, db, mensaje="Revisa los datos del proveedor: " + "; ".join(errores.values()), estado=400)
    datos["articulos_por_envio"] = datos.get("articulos_por_envio") or 1
    for campo, valor in datos.items():
        setattr(proveedor, campo, valor)
    db.commit()
    return RedirectResponse("/configuracion?mensaje=Proveedor guardado.#proveedores", status_code=303)


def _reevaluar(tareas_fondo: BackgroundTasks) -> None:
    from app import tareas

    tareas.reevaluar_operacion()
    tareas.programar(tareas_fondo)


@rutas.post("/configuracion/demo")
def cargar_demo(tareas_fondo: BackgroundTasks, db: Session = Depends(sesion)):
    from app.demo import cargar_demo as cargar

    resumen = cargar(db)
    _reevaluar(tareas_fondo)
    texto = f"Demostración cargada: {resumen['creados']} medicamentos nuevos, {resumen['actualizados']} ya existían."
    return RedirectResponse(f"/medicamentos?mensaje={texto}", status_code=303)


@rutas.post("/configuracion/borrar")
async def borrar_datos(request: Request, db: Session = Depends(sesion)):
    from app.demo import PALABRA_PARA_BORRAR, borrar_datos as borrar

    if str((await request.form()).get("palabra", "")).strip().upper() != PALABRA_PARA_BORRAR:
        return RedirectResponse("/configuracion?borrado=no#borrar", status_code=303)
    cuenta = borrar(db)
    texto = (
        f"Datos borrados: {cuenta['medicamentos']} medicamentos, {cuenta['despachos']} despachos y "
        f"{cuenta['pedidos']} pedidos. La configuración se conservó."
    )
    return RedirectResponse(f"/medicamentos?mensaje={texto}", status_code=303)


# ---------------------------------------------------------------- Importación


def _opciones(formulario) -> importacion.Opciones:
    ambigua = str(formulario.get("precio_unitario_q", "ignorar"))
    return importacion.Opciones(
        columna_ambigua={"precio_unitario_q": ambigua} if ambigua == "costo_compra" else {},
        ajustar_existencias=formulario.get("ajustar_existencias") == "1",
        aplicar_configuracion=formulario.get("aplicar_configuracion", "1") == "1",
        importar_semanas=formulario.get("importar_semanas", "1") == "1",
    )


@rutas.get("/importar")
def importar(request: Request, error: str = "", db: Session = Depends(sesion)):
    recientes = list(db.scalars(select(Importacion).order_by(Importacion.id.desc()).limit(10)))
    return pagina(request, "importar.html", {"pestana": "configuracion", "recientes": recientes, "error": error})


@rutas.post("/importar")
async def subir(request: Request, db: Session = Depends(sesion)):
    formulario = await request.form()
    archivo = formulario.get("archivo")
    if not isinstance(archivo, UploadFile) and not hasattr(archivo, "read"):
        return RedirectResponse("/importar?error=Elige un archivo.", status_code=303)
    contenido = await archivo.read()
    if not contenido:
        return RedirectResponse("/importar?error=El archivo está vacío.", status_code=303)
    if len(contenido) > 5_000_000:
        return RedirectResponse("/importar?error=El archivo pesa más de 5 MB.", status_code=303)
    try:
        nueva = importacion.preparar(db, archivo.filename or "archivo", contenido, _opciones(formulario))
    except Exception as problema:  # archivo dañado o formato desconocido
        return RedirectResponse(f"/importar?error=No se pudo leer el archivo ({type(problema).__name__}).", status_code=303)
    return RedirectResponse(f"/importar/{nueva.id}", status_code=303)


def _importacion_de(db: Session, importacion_id: int) -> Importacion:
    hecha = db.get(Importacion, importacion_id)
    if hecha is None:
        raise HTTPException(status_code=404, detail="Esa importación no existe.")
    return hecha


@rutas.get("/importar/{importacion_id}")
def vista_previa(request: Request, importacion_id: int, error: str = "", db: Session = Depends(sesion)):
    hecha = _importacion_de(db, importacion_id)
    return pagina(
        request,
        "importar_vista.html",
        {
            "pestana": "configuracion",
            "imp": hecha,
            "vista": hecha.datos["vista"],
            "hojas": hecha.datos["hojas"],
            "opciones": hecha.datos["opciones"],
            "campos_catalogo": importacion.CAMPOS_CATALOGO,
            "campos_historial": importacion.CAMPOS_HISTORIAL,
            "error": error,
        },
    )


@rutas.post("/importar/{importacion_id}/mapeo")
async def remapear(request: Request, importacion_id: int, db: Session = Depends(sesion)):
    hecha = _importacion_de(db, importacion_id)
    if hecha.estado != "vista_previa":
        return RedirectResponse(f"/importar/{importacion_id}", status_code=303)
    formulario = await request.form()
    mapeos = {}
    for indice, hoja in enumerate(hecha.datos["hojas"]):
        campos_hoja = importacion.CAMPOS_CATALOGO if hoja["tipo"] == "catalogo" else importacion.CAMPOS_HISTORIAL
        mapeos[hoja["nombre"]] = {
            destino: str(formulario.get(f"mapa_{indice}_{destino}", "")) for destino in campos_hoja
        }
    importacion.reanalizar(db, hecha, _opciones(formulario), mapeos)
    return RedirectResponse(f"/importar/{importacion_id}", status_code=303)


@rutas.post("/importar/{importacion_id}/confirmar")
def confirmar(importacion_id: int, tareas_fondo: BackgroundTasks, db: Session = Depends(sesion)):
    hecha = _importacion_de(db, importacion_id)
    try:
        importacion.aplicar(db, hecha)
    except ErrorDeOperacion as problema:
        return RedirectResponse(f"/importar/{importacion_id}?error={problema}", status_code=303)
    _reevaluar(tareas_fondo)
    return RedirectResponse(f"/importar/{importacion_id}", status_code=303)


@rutas.post("/importar/{importacion_id}/descartar")
def descartar(importacion_id: int, db: Session = Depends(sesion)):
    hecha = _importacion_de(db, importacion_id)
    if hecha.estado == "vista_previa":
        hecha.estado = "descartada"
        db.commit()
    return RedirectResponse("/importar", status_code=303)


@rutas.get("/plantillas/{nombre}")
def plantilla(nombre: str):
    from app import plantilla as generador

    archivos = {"plantilla_salvamed.xlsx": generador.plantilla, "ejemplo_simulado.xlsx": generador.ejemplo}
    if nombre not in archivos:
        raise HTTPException(status_code=404, detail="Esa plantilla no existe.")
    return Response(
        archivos[nombre](),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


# ---------------------------------------------------------------- Telegram


@rutas.post("/telegram/webhook")
async def webhook(request: Request):
    a = ajustes()
    if a.telegram_modo != "webhook":
        raise HTTPException(status_code=404)
    secreto = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not a.secreto_webhook or secreto != a.secreto_webhook:
        raise HTTPException(status_code=403)
    try:
        update = json.loads(await request.body())
    except ValueError:
        raise HTTPException(status_code=400)
    from sqlalchemy.orm import Session as SesionNueva

    with SesionNueva(motor) as db:
        telegram.manejar_update(db, update)
    return JSONResponse({"ok": True})
