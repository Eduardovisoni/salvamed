"""Simulación del Sanatorio El Roble en tiempo acelerado.

Los servicios del sanatorio (Emergencia, Encamamiento, Cuidados Intensivos,
Sala de operaciones, Traumatología y Ambulancia) piden medicamentos al
inventario del sanatorio a lo largo de las 24 horas, cada uno con su propio horario:
las rondas de encamamiento, las cirugías programadas de día, más emergencias en
la noche y el fin de semana. Los medicamentos, cantidades y horarios son
simulados; solo los nombres de los servicios vienen de lo que publica el
sanatorio.

Usa las mismas funciones que la operación (despachar, evaluar_reposicion,
recibir, los avisos de Telegram), pero todo lo que crea lleva `corrida_id`:
nunca toca el inventario, los costos ni los pedidos reales.

Reloj. El tiempo virtual se mide en minutos de operación; el sanatorio opera
24 horas. A velocidad ×1, 1 segundo real son 20 minutos virtuales: un día dura
72 s; a ×5, unos 14 s, y una semana menos de dos minutos. La velocidad solo
cambia el ritmo: las solicitudes de cada día salen de una semilla (escenario +
número de día), así que con la misma semilla la corrida produce los mismos
despachos, avisos y recepciones.

El estado vive en la base, no en el navegador: refrescar o abrir otra pestaña
no duplica despachos, recepciones ni mensajes. Cada solicitud usa un
identificador fijo como token de despacho y el avance se hace con un candado.

Pausa de exposición. Cuando un medicamento llega a su punto de reorden, el reloj
virtual se detiene en ese instante, se envía el aviso por Telegram y se espera a
que el expositor (en el papel del encargado de compras) pulse «Pedido
realizado». Es un recurso para explicar el flujo: no significa que un sanatorio
real deje de atender mientras el encargado responde. La respuesta del encargado se modela como
inmediata en tiempo virtual, o con la demora de gestión que se elija al crear
la corrida. Si varios medicamentos avisan a la vez, cada aviso es una causa de
pausa y el reloj sigue solo cuando se confirmaron todas.

Recepción. Solo en la simulación llega sola. El plazo real de cada pedido es
aleatorio, entre L − 1 y L + 1 días de operación (en promedio L, que es el que
usa la fórmula de la clase): el proveedor dice L, pero unas veces llega antes y
otras después.

Comparación. Al lado corre «El Roble hoy» (app/roble.py): las mismas solicitudes
con la regla de pedir cuando se acaba y a ojo. Lo que un servicio pide y no hay
se compra de emergencia con recargo, en los dos lados. Los costos de cada lado
son pedir (envíos) + mantener (H por unidad y día guardada) + el recargo de las
emergencias.
"""

import math
import random
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import lru_cache

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import inventario, pedidos, roble
from app.consumo import GUATEMALA, dia_local, hora_local
from app.db import (
    CANCELADO,
    REALIZADO,
    Corrida,
    EventoSim,
    Medicamento,
    Mensaje,
    Movimiento,
    Pedido,
    PedidoLinea,
    PlanDiario,
    Despacho,
    DespachoLinea,
    Proveedor,
    ahora,
)
from app.inventario import LineaPedida
from app.modelo import punto_de_pedido
from app.planificacion import configuracion, evaluar_varios

MINUTOS_POR_SEGUNDO = 20
VELOCIDADES = (1, 2, 4, 5)
MAX_EVENTOS_POR_PASO = 400
RECARGO_EMERGENCIA = 20  # % sobre el precio de compra de lo que se consigue de emergencia
candado = threading.RLock()


# ---------------------------------------------------------------- Escenarios


@dataclass(frozen=True)
class Servicio:
    nombre: str
    icono: str
    por_hora: tuple[float, ...]  # peso de cada hora del día (0 a 23)
    por_dia: tuple[float, ...]  # factor de lunes a domingo


SERVICIOS = (
    Servicio("Emergencia", "🚨", (3, 2, 2, 2, 2, 2, 3, 4, 5, 5, 5, 5, 5, 5, 5, 5, 6, 7, 8, 8, 8, 7, 6, 4), (1, 1, 1, 1, 1.05, 1.1, 1.1)),
    Servicio("Encamamiento", "🛏️", (1, 1, 1, 1, 1, 2, 8, 4, 1, 1, 2, 1, 2, 1, 8, 4, 1, 1, 2, 1, 2, 1, 8, 4), (1,) * 7),
    Servicio("Cuidados Intensivos", "💓", (2, 2, 2, 2, 2, 2, 4, 3, 2, 2, 2, 2, 2, 2, 4, 3, 2, 2, 2, 2, 2, 2, 4, 3), (1,) * 7),
    Servicio("Sala de operaciones", "🩺", (0,) * 7 + (3, 5, 6, 6, 5, 4, 5, 5, 4, 2) + (0,) * 7, (1.15, 1.15, 1.15, 1.15, 1.1, 0.5, 0.2)),
    Servicio("Traumatología", "🦴", (0,) * 8 + (3, 4, 5, 5, 4, 3, 4, 5, 5, 4) + (0,) * 6, (1.1, 1.1, 1.1, 1.1, 1.1, 0.6, 0.3)),
    Servicio("Ambulancia", "🚑", (1,) * 17 + (2, 2, 2, 2, 2) + (1, 1), (1, 1, 1, 1, 1.05, 1.15, 1.15)),
)


@dataclass(frozen=True)
class Producto:
    nombre: str
    presentacion: str
    unidad: str
    proveedor: str
    costo: float
    paquete: int
    existencia: int
    plazo: float
    consumo: float  # unidades por día que el generador busca en promedio (el sistema no lo conoce)
    cantidad_max: int
    reparto: tuple[tuple[str, float], ...]  # qué parte del consumo pide cada servicio


@dataclass(frozen=True)
class Escenario:
    clave: str
    titulo: str
    descripcion: str
    semilla: int
    productos: tuple[Producto, ...]
    servicios: tuple[Servicio, ...] = SERVICIOS
    aumento: tuple[str, int, float] | None = None  # (nombre, desde el día, factor)
    inicio: date = date(2026, 11, 2)


_PRODUCTOS = (
    Producto("Ceftriaxona", "1 g", "vial", "Proveedor A", 12.5, 10, 42, 3, 6, 2,
             (("Encamamiento", 0.45), ("Sala de operaciones", 0.25), ("Cuidados Intensivos", 0.2), ("Emergencia", 0.1))),
    Producto("Paracetamol", "1 g/100 ml", "frasco IV", "Proveedor A", 18.0, 12, 190, 3, 15, 3,
             (("Encamamiento", 0.35), ("Emergencia", 0.3), ("Traumatología", 0.2), ("Sala de operaciones", 0.15))),
    Producto("Solución salina", "0.9 % · 100 ml", "bolsa", "Proveedor A", 5.5, 20, 330, 3, 22, 4,
             (("Emergencia", 0.35), ("Encamamiento", 0.25), ("Cuidados Intensivos", 0.15), ("Sala de operaciones", 0.15), ("Ambulancia", 0.1))),
    Producto("Dexametasona", "4 mg/ml · 2 ml", "ampolla", "Proveedor B", 5.9, 10, 95, 3, 8, 2,
             (("Emergencia", 0.45), ("Encamamiento", 0.25), ("Traumatología", 0.2), ("Cuidados Intensivos", 0.1))),
    Producto("Metoclopramida", "10 mg/2 ml", "ampolla", "Proveedor B", 4.7, 10, 110, 3, 7, 2,
             (("Sala de operaciones", 0.4), ("Emergencia", 0.3), ("Encamamiento", 0.3))),
    Producto("Salbutamol", "2.5 mg/2.5 ml", "monodosis", "Proveedor B", 4.1, 20, 150, 3, 10, 3,
             (("Emergencia", 0.6), ("Encamamiento", 0.25), ("Cuidados Intensivos", 0.15))),
    Producto("Insulina cristalina", "U-100 · 10 ml", "vial", "Proveedor C", 88.0, 1, 22, 4, 1.2, 1,
             (("Encamamiento", 0.6), ("Cuidados Intensivos", 0.4))),
    Producto("Adrenalina (epinefrina)", "1 mg/ml · 1 ml", "ampolla", "Proveedor C", 7.5, 10, 30, 3, 1.5, 2,
             (("Emergencia", 0.35), ("Cuidados Intensivos", 0.3), ("Ambulancia", 0.25), ("Sala de operaciones", 0.1))),
)

ESCENARIOS = {
    e.clave: e
    for e in (
        Escenario(
            "base",
            "Sanatorio en un mes normal",
            "Seis servicios del sanatorio piden ocho medicamentos las 24 horas, con días altos, bajos y sin "
            "consumo de algunos productos. Se ve el primer aviso, la confirmación por Telegram, el consumo de parte de "
            "la reserva mientras corre L y la recepción a tiempo.",
            2026,
            _PRODUCTOS,
        ),
        Escenario(
            "aumento",
            "Aumento de casos respiratorios (salbutamol)",
            "Igual que el mes normal, pero desde el día 15 los servicios piden 2.3 veces más salbutamol. "
            "Se ve cómo la estimación se actualiza, el aviso de aumento y que puede faltar existencia.",
            2026,
            _PRODUCTOS,
            aumento=("Salbutamol", 14, 2.3),
        ),
    )
}


@dataclass(frozen=True)
class Solicitud:
    minuto: float  # minuto dentro del día
    numero: int
    servicio: str
    compras: tuple[tuple[int, int], ...]  # (índice de producto, cantidad)


def _hora(azar: random.Random, pesos: tuple[float, ...]) -> int:
    r = azar.random() * sum(pesos)
    for hora, peso in enumerate(pesos):
        r -= peso
        if r <= 0 and peso > 0:
            return hora
    return max(h for h, p in enumerate(pesos) if p > 0)


def solicitudes_del_dia(escenario: Escenario, semilla: int, dia: int, minutos_dia: int, dia_semana: int) -> list[Solicitud]:
    """Solicitudes de los servicios en un día, siempre las mismas para la misma semilla y el mismo día."""
    azar = random.Random(f"salvamed:{semilla}:{dia}")  # igual en todos los escenarios: así se pueden comparar
    factor = math.exp(azar.gauss(0, 0.12))
    tipo = azar.random()
    if tipo < 0.08:
        factor *= 0.7  # día tranquilo
    elif tipo > 0.92:
        factor *= 1.35  # día con muchos ingresos
    escala = minutos_dia / 1440
    lineas = []
    for servicio in escenario.servicios:
        f = factor * servicio.por_dia[dia_semana]
        for j, p in enumerate(escenario.productos):
            parte = dict(p.reparto).get(servicio.nombre, 0)
            if not parte:
                continue
            media = p.consumo * parte * f
            if escenario.aumento and escenario.aumento[0] == p.nombre and dia >= escenario.aumento[1]:
                media *= escenario.aumento[2]
            # Cada servicio y medicamento tiene su propia secuencia: el aumento de uno no cambia a los demás.
            propio = random.Random(f"salvamed:{semilla}:{dia}:{servicio.nombre}:{p.nombre}")
            for _ in range(_poisson(propio, media / ((1 + p.cantidad_max) / 2))):
                minuto = (_hora(propio, servicio.por_hora) * 60 + propio.random() * 59.9) * escala
                cantidad = min(1 + int(propio.random() ** 1.6 * p.cantidad_max), p.cantidad_max)
                lineas.append((servicio.nombre, round(minuto, 3), j, cantidad))
    lineas.sort()
    solicitudes, actual = [], None
    for nombre, minuto, j, cantidad in lineas:
        if (
            actual is not None
            and actual[0] == nombre
            and minuto - actual[1] <= 20 * escala
            and len(actual[2]) < 3
            and j not in dict(actual[2])
        ):
            actual[2].append((j, cantidad))
            continue
        actual = (nombre, minuto, [(j, cantidad)])
        solicitudes.append(actual)
    solicitudes.sort(key=lambda x: (x[1], x[0]))
    return [Solicitud(minuto, i + 1, nombre, tuple(compras)) for i, (nombre, minuto, compras) in enumerate(solicitudes)]


@lru_cache(maxsize=4096)
def _solicitudes(escenario: Escenario, semilla: int, dia: int, minutos_dia: int, dia_semana: int) -> tuple[Solicitud, ...]:
    return tuple(solicitudes_del_dia(escenario, semilla, dia, minutos_dia, dia_semana))


def _poisson(azar: random.Random, media: float) -> int:
    if media <= 0:
        return 0
    if media > 60:
        return max(0, round(azar.gauss(media, math.sqrt(media))))
    limite, k, p = math.exp(-media), 0, 1.0
    while True:
        p *= azar.random()
        if p <= limite:
            return k
        k += 1


# ---------------------------------------------------------------- Calendario virtual


def _minutos_dia(c: Corrida) -> int:
    return c.parametros["minutos_dia"]


def fecha_del_dia(c: Corrida, indice: int) -> date:
    """El día de operación número `indice` (0 = primer día), saltando los días cerrados."""
    dias_semana = c.parametros["dias_semana"]
    dia = date.fromisoformat(c.parametros["inicio"])
    while dias_semana[dia.weekday()] != "1":
        dia += timedelta(days=1)
    contados = 0
    while contados < indice:
        dia += timedelta(days=1)
        if dias_semana[dia.weekday()] == "1":
            contados += 1
    return dia


def momento_de(c: Corrida, minuto: float) -> datetime:
    md = _minutos_dia(c)
    indice = int(minuto // md)
    resto = minuto - indice * md
    apertura = datetime.combine(fecha_del_dia(c, indice), time(c.parametros["apertura"]), GUATEMALA)
    return apertura + timedelta(minutes=resto)


def minuto_de(c: Corrida, momento: datetime) -> float:
    local = hora_local(momento)
    dias_semana = c.parametros["dias_semana"]
    dia = date.fromisoformat(c.parametros["inicio"])
    indice = 0
    while dia < local.date():
        if dias_semana[dia.weekday()] == "1":
            indice += 1
        dia += timedelta(days=1)
    if dias_semana[date.fromisoformat(c.parametros["inicio"]).weekday()] != "1":
        indice = max(indice - 1, 0)
    apertura = datetime.combine(local.date(), time(c.parametros["apertura"]), GUATEMALA)
    resto = min(max((local - apertura).total_seconds() / 60, 0), _minutos_dia(c))
    return indice * _minutos_dia(c) + resto


# ---------------------------------------------------------------- Corridas


def corrida_activa(db: Session) -> Corrida | None:
    return db.scalar(select(Corrida).where(Corrida.estado == "activa").order_by(Corrida.id.desc()))


def crear_corrida(
    db: Session, clave: str = "base", semilla: int | None = None, demora_horas: float = 0, recargo: float = RECARGO_EMERGENCIA
) -> Corrida:
    with candado:
        escenario = ESCENARIOS.get(clave, ESCENARIOS["base"])
        for vieja in db.scalars(select(Corrida).where(Corrida.estado == "activa")):
            vieja.estado = "terminada"
        cfg = configuracion(db)
        corrida = Corrida(
            escenario=escenario.clave,
            semilla=escenario.semilla if semilla is None else semilla,
            estado="activa",
            creada=ahora(),
            velocidad=1,
            pausa_manual=True,
            causas=[],
            demora_gestion=max(demora_horas, 0) * 60,
        )
        corrida.parametros = {
            "inicio": escenario.inicio.isoformat(),
            "apertura": cfg.hora_apertura,
            "cierre": cfg.hora_cierre,
            "minutos_dia": (cfg.hora_cierre - cfg.hora_apertura) * 60,
            "dias_semana": cfg.dias_semana,
            "meds": [],
            "llegadas": {},
            "tarifas": {"S": cfg.costo_pedido, "tasa": cfg.tasa_mantenimiento, "A": cfg.dias_operacion_anio, "recargo": max(recargo, 0) / 100},
            "area": [0.0] * len(escenario.productos),
            "t_area": 0.0,
            "emergencia": [[0, 0] for _ in escenario.productos],
            "ordenes": [0] * len(escenario.productos),
        }
        db.add(corrida)
        db.flush()
        momento = momento_de(corrida, 0)
        proveedores = {}
        ids = []
        for p in escenario.productos:
            if p.proveedor not in proveedores:
                proveedor = Proveedor(corrida_id=corrida.id, nombre=p.proveedor, costo_envio=cfg.costo_pedido)
                db.add(proveedor)
                db.flush()
                proveedores[p.proveedor] = proveedor
            m = inventario.dar_de_alta(
                db,
                {
                    "nombre": p.nombre,
                    "presentacion": p.presentacion,
                    "unidad": p.unidad,
                    "proveedor_id": proveedores[p.proveedor].id,
                    "costo_compra": p.costo,
                    "unidades_paquete": p.paquete,
                    "tiempo_entrega": p.plazo,
                    "origen": "simulacion",
                },
                p.existencia,
                momento,
                corrida_id=corrida.id,
                usuario="Escenario",
            )
            ids.append(m.id)
        corrida.parametros = dict(corrida.parametros, meds=ids)
        _evento(db, corrida, 0, "inicio", texto=f"Corrida #{corrida.id}: {escenario.titulo} (semilla {corrida.semilla}). Sin historia previa: el plan se calcula cuando haya días completos.")
        db.commit()
        return corrida


def _evento(db: Session, c: Corrida, minuto: float, tipo: str, medicamento_id=None, cantidad=None, texto="", **datos) -> EventoSim:
    evento = EventoSim(
        corrida_id=c.id, minuto=minuto, tipo=tipo, medicamento_id=medicamento_id, cantidad=cantidad, texto=texto[:300], datos=datos
    )
    db.add(evento)
    return evento


def corriendo(c: Corrida) -> bool:
    return c.estado == "activa" and not c.pausa_manual and not c.causas


def minuto_objetivo(c: Corrida, real: datetime) -> float:
    if not corriendo(c) or c.real_ancla is None:
        return c.minuto
    segundos = (real - c.real_ancla).total_seconds()
    return max(c.minuto, c.minuto_ancla + max(segundos, 0) * MINUTOS_POR_SEGUNDO * c.velocidad)


def _reanclar(c: Corrida) -> None:
    c.minuto_ancla = c.minuto
    c.real_ancla = ahora()


# ---------------------------------------------------------------- Avance


def _llegadas(c: Corrida) -> dict[int, float]:
    return {int(k): v for k, v in (c.parametros.get("llegadas") or {}).items()}


def _siguiente_llegada(db: Session, c: Corrida) -> tuple[float, int] | None:
    pendientes = []
    for pedido_id, minuto in _llegadas(c).items():
        pedido = db.get(Pedido, pedido_id)
        if pedido is not None and pedido.estado == REALIZADO:
            pendientes.append((minuto, pedido_id))
    return min(pendientes) if pendientes else None


def avanzar(db: Session, c: Corrida, hasta: float, detener_en_evento: bool = False, limite: int = MAX_EVENTOS_POR_PASO) -> int:
    """Procesa en orden todo lo que pasa hasta el minuto `hasta`. Devuelve cuántos eventos procesó."""
    escenario = ESCENARIOS[c.escenario]
    md = _minutos_dia(c)
    procesados = 0
    while c.estado == "activa" and not c.causas and procesados < limite:
        dia = c.dia_cerrado + 1
        fecha = fecha_del_dia(c, dia)
        clientes = _solicitudes(escenario, c.semilla, dia, md, fecha.weekday())
        siguiente_cliente = dia * md + clientes[c.evento_siguiente].minuto if c.evento_siguiente < len(clientes) else None
        cierre = (dia + 1) * md
        llegada = _siguiente_llegada(db, c)
        candidatos = [(cierre, 2, None)]
        if siguiente_cliente is not None:
            candidatos.append((siguiente_cliente, 1, clientes[c.evento_siguiente]))
        if llegada is not None:
            candidatos.append((max(llegada[0], c.minuto), 0, llegada[1]))
        minuto, clase, dato = min(candidatos, key=lambda x: (x[0], x[1]))
        if minuto > hasta:
            break
        _acumular(db, c, minuto)
        c.minuto = minuto
        procesados += 1
        if clase == 0:
            hubo = _recibir(db, c, dato, minuto)
        elif clase == 1:
            hubo = _atender(db, c, dato, dia, minuto)
            c.evento_siguiente += 1
        else:
            hubo = _cerrar_dia(db, c, dia, minuto, len(clientes))
        db.commit()
        if detener_en_evento and hubo:
            break
    if not c.causas and c.minuto < hasta and procesados < limite:
        _acumular(db, c, hasta)
        c.minuto = hasta
    if procesados >= limite:
        _reanclar(c)  # el reloj no puede ir más rápido de lo que se procesa
    db.commit()
    return procesados


def _medicamentos(db: Session, c: Corrida) -> list[Medicamento]:
    return [db.get(Medicamento, i) for i in c.parametros["meds"]]


def _acumular(db: Session, c: Corrida, hasta: float) -> None:
    """Suma lo que estuvo guardado (unidades × minutos) hasta `hasta`: con eso sale el costo de mantener."""
    p = c.parametros
    if "area" not in p or hasta <= p["t_area"]:
        return
    pasado = hasta - p["t_area"]
    area = [a + m.existencia * pasado for a, m in zip(p["area"], _medicamentos(db, c))]
    c.parametros = dict(p, area=area, t_area=hasta)


def _atender(db: Session, c: Corrida, solicitud: Solicitud, dia: int, minuto: float) -> bool:
    meds = _medicamentos(db, c)
    momento = momento_de(c, minuto)
    lineas, faltantes, items = [], [], []
    for indice, cantidad in solicitud.compras:
        m = meds[indice]
        entregable = min(cantidad, m.existencia)
        if entregable > 0:
            lineas.append(LineaPedida(m.id, entregable))
            items.append([m.id, entregable])
        if entregable < cantidad:
            faltantes.append((indice, m, cantidad - entregable))
    if lineas:
        despacho = inventario.despachar(
            db, solicitud.servicio, lineas, f"sim{c.id}-{dia}-{solicitud.numero}", momento, corrida_id=c.id, usuario="Servicio simulado"
        )
        _evento(
            db, c, minuto, "despacho", cantidad=sum(n for _, n in items), texto=solicitud.servicio,
            items=items, costo=despacho.costo_total, servicio=solicitud.servicio, solicitud=solicitud.numero,
        )
    if faltantes and "emergencia" in c.parametros:
        emergencia = [list(x) for x in c.parametros["emergencia"]]
        for indice, _, falta in faltantes:
            emergencia[indice][0] += falta
            emergencia[indice][1] += 1
        c.parametros = dict(c.parametros, emergencia=emergencia)
    for _, m, falta in faltantes:
        c.faltantes += falta
        _evento(
            db, c, minuto, "faltante", m.id, falta,
            texto=f"{solicitud.servicio} pidió {m.nombre} y no alcanzó: faltaron {falta} {m.unidad}, se compran de emergencia",
            servicio=solicitud.servicio,
        )
    return _revisar_varios(db, c, [meds[indice] for indice, _ in solicitud.compras], minuto, momento)


def _revisar(db: Session, c: Corrida, m: Medicamento, minuto: float, momento: datetime) -> bool:
    return _revisar_varios(db, c, [m], minuto, momento)


def _revisar_varios(db: Session, c: Corrida, meds: list[Medicamento], minuto: float, momento: datetime) -> bool:
    antes = {m.id: m.ultimo_aviso_aumento for m in meds}
    evaluaciones, nuevos = pedidos.evaluar_grupo(db, meds, momento)
    hubo_aumento = False
    for m in meds:
        e = evaluaciones[m.id]
        if e.aumento and m.ultimo_aviso_aumento != antes[m.id]:
            hubo_aumento = True
            _evento(
                db, c, minuto, "aumento", m.id,
                texto=f"Aviso de aumento: {m.nombre} pasó de {e.aumento.anterior:.1f} a {e.aumento.reciente:.1f} por día (z = {e.aumento.z:.1f}). Nuevo R {e.plan.R if e.plan else 0:.1f}.",
            )
    for nuevo in nuevos:
        c.causas = list(c.causas or []) + [nuevo.id]
        for linea, m in pedidos.lineas_de(db, nuevo):
            e = evaluaciones[m.id]
            _evento(
                db, c, minuto, "aviso", m.id, linea.cantidad_pedida,
                texto=f"{m.nombre} llegó al punto de reorden: posición {e.posicion} ≤ {e.plan.R_pedido} (R {e.plan.R:.1f} hacia arriba). Aviso enviado por Telegram.",
                pedido=nuevo.id,
            )
        _evento(db, c, minuto, "pausa", texto=f"Reloj en pausa de exposición: esperando «Pedido realizado» del pedido #{nuevo.id}.", pedido=nuevo.id)
    return bool(nuevos) or hubo_aumento


def _recibir(db: Session, c: Corrida, pedido_id: int, minuto: float) -> bool:
    pedido = db.get(Pedido, pedido_id)
    momento = momento_de(c, minuto)
    lineas = pedidos.lineas_de(db, pedido)
    inventario.recibir(db, pedido, {l.id: l.por_recibir for l, _ in lineas}, momento, "Recepción simulada")
    llegadas = dict(c.parametros.get("llegadas") or {})
    llegadas.pop(str(pedido_id), None)
    c.parametros = dict(c.parametros, llegadas=llegadas)
    for linea, m in lineas:
        _evento(db, c, minuto, "recepcion", m.id, linea.cantidad_recibida, texto=f"Llegó el pedido #{pedido.id}: +{linea.cantidad_recibida} {m.unidad} de {m.nombre}", pedido=pedido.id)
    _revisar_varios(db, c, [m for _, m in lineas], minuto, momento)
    return True


def _cerrar_dia(db: Session, c: Corrida, dia: int, minuto: float, clientes: int) -> bool:
    c.dia_cerrado = dia
    c.evento_siguiente = 0
    inicio = momento_de(c, dia * _minutos_dia(c))
    consumido = db.scalar(
        select(func.coalesce(func.sum(Despacho.costo_total), 0)).where(
            Despacho.corrida_id == c.id, Despacho.fecha >= inicio, Despacho.fecha < momento_de(c, minuto)
        )
    )
    _evento(
        db, c, minuto, "cierre",
        texto=f"Fin del día {dia + 1}: {clientes} solicitudes de los servicios, consumo Q {consumido:,.2f}. La estimación se actualiza con el día completo.",
        dia=dia + 1, solicitudes=clientes, consumido=float(consumido),
    )
    return _revisar_varios(db, c, _medicamentos(db, c), minuto, momento_de(c, minuto))


# ---------------------------------------------------------------- Controles


def paso(db: Session, c: Corrida) -> None:
    """Lo llama cada consulta del navegador: avanza hasta donde indica el reloj."""
    with candado:
        db.refresh(c)
        if corriendo(c):
            avanzar(db, c, minuto_objetivo(c, ahora()))
            if not corriendo(c):
                db.commit()


def controlar(db: Session, c: Corrida, accion: str, valor: str = "") -> None:
    with candado:
        db.refresh(c)
        if c.estado != "activa":
            return
        if corriendo(c):
            avanzar(db, c, minuto_objetivo(c, ahora()))
        md = _minutos_dia(c)
        if accion == "pausar":
            c.pausa_manual = True
        elif accion == "reanudar":
            c.pausa_manual = False
        elif accion == "velocidad" and valor.isdigit() and int(valor) in VELOCIDADES:
            c.velocidad = int(valor)
        elif accion == "dia" and not c.causas:
            avanzar(db, c, (c.dia_cerrado + 2) * md, limite=3000)
        elif accion == "evento" and not c.causas:
            avanzar(db, c, c.minuto + 14 * md, detener_en_evento=True, limite=3000)
        _reanclar(c)
        db.commit()


def confirmar_pedido(db: Session, pedido: Pedido, usuario: str) -> tuple[bool, str]:
    """«Pedido realizado» en la simulación (desde Telegram o desde la web)."""
    with candado:
        c = db.get(Corrida, pedido.corrida_id)
        if c is None or c.estado != "activa":
            return False, "Esa corrida de simulación ya terminó o se reinició: el botón ya no tiene efecto."
        db.refresh(c)
        minuto = c.minuto + (c.demora_gestion or 0)
        plazo = pedidos.plazo_de(db, pedido)
        real, ordenes = _plazo_real(db, c, pedido, plazo)
        llegada = minuto + real * _minutos_dia(c)
        estimada = minuto + plazo * _minutos_dia(c)  # lo que dijo el proveedor
        cambio, texto = pedidos.marcar_realizado(db, pedido, usuario, momento_de(c, minuto), momento_de(c, estimada))
        if not cambio:
            return cambio, texto
        c.parametros = dict(c.parametros, llegadas={**(c.parametros.get("llegadas") or {}), str(pedido.id): llegada})
        if ordenes is not None:
            c.parametros = dict(c.parametros, ordenes=ordenes)
        cantidad = sum(l.cantidad_pedida for l, _ in pedidos.lineas_de(db, pedido))
        demora = f" (respuesta modelada: +{c.demora_gestion / 60:g} h virtuales)" if c.demora_gestion else ""
        _evento(
            db, c, c.minuto, "pedido", cantidad=cantidad,
            texto=f"Pedido #{pedido.id} realizado por {usuario}{demora}: {cantidad} unidades. El proveedor dice {plazo:g} días; esta vez tarda {real:.1f} y llega el {momento_de(c, llegada):%d/%m %H:%M}.",
            pedido=pedido.id, llegada=llegada,
        )
        _quitar(db, c, pedido.id)
        db.commit()
        return True, f"Pedido #{pedido.id} registrado (simulación). Llega el {momento_de(c, llegada):%d/%m %H:%M} virtual."


def _plazo_real(db: Session, c: Corrida, pedido: Pedido, plazo: float) -> tuple[float, list[int] | None]:
    """Sorteo del plazo de este pedido (el mismo que usa el Roble para su pedido número k del medicamento)
    y la cuenta de pedidos por medicamento que queda si se confirma."""
    if "ordenes" not in c.parametros:
        return plazo, None
    ids = c.parametros["meds"]
    ordenes = list(c.parametros["ordenes"])
    indices = [ids.index(m.id) for _, m in pedidos.lineas_de(db, pedido)]
    real = roble.plazo_aleatorio(c.semilla, indices[0], ordenes[indices[0]], plazo)
    for i in indices:
        ordenes[i] += 1
    return real, ordenes


def _quitar(db: Session, c: Corrida, pedido_id: int) -> None:
    """Quita una causa de pausa. Si era la última, el reloj sigue a la velocidad que tenía."""
    causas = [x for x in (c.causas or []) if x != pedido_id]
    if causas == list(c.causas or []):
        return
    c.causas = causas
    if causas:
        _evento(db, c, c.minuto, "pausa", texto=f"Quedan {len(causas)} avisos por confirmar: el reloj sigue en pausa.")
        return
    _reanclar(c)
    if c.pausa_manual:
        _evento(db, c, c.minuto, "reanudar", texto="Confirmación recibida. El reloj sigue en pausa manual.")
    else:
        _evento(db, c, c.minuto, "reanudar", texto=f"Confirmación recibida: el reloj sigue a ×{c.velocidad}.")


def quitar_causa(db: Session, pedido: Pedido) -> None:
    with candado:
        c = db.get(Corrida, pedido.corrida_id)
        if c is not None:
            _quitar(db, c, pedido.id)
            db.commit()


# ---------------------------------------------------------------- Estado para la pantalla


def estado(db: Session, c: Corrida, desde_evento: int = 0, seleccionado: int | None = None) -> dict:
    momento = momento_de(c, c.minuto)
    meds = _medicamentos(db, c)
    evaluaciones = evaluar_varios(db, meds, momento)
    llegadas = _llegadas(c)
    abiertos = {}
    for pedido_id, minuto in llegadas.items():
        for linea in db.scalars(select(PedidoLinea).where(PedidoLinea.pedido_id == pedido_id)):
            abiertos[linea.medicamento_id] = {"pedido": pedido_id, "llega": momento_de(c, minuto).strftime("%d/%m %H:%M"), "cantidad": linea.por_recibir}
    tarjetas = []
    for m in meds:
        e = evaluaciones[m.id]
        tarjetas.append(
            {
                "id": m.id,
                "nombre": m.nombre,
                "presentacion": m.presentacion,
                "unidad": m.unidad,
                "existencia": m.existencia,
                "en_camino": e.en_camino,
                "estado": e.estado.clave,
                "etiqueta": e.estado.etiqueta,
                "R": e.plan.R if e.plan else None,
                "R_pedido": e.plan.R_pedido if e.plan else None,
                "IS": e.plan.IS if e.plan else None,
                "Q": e.plan.Q if e.plan else None,
                "sugerida": e.sugerida,
                "mu": e.consumo.mu_d,
                "sigma": e.consumo.sigma_d,
                "dias": e.consumo.dias,
                "calidad": e.consumo.calidad,
                "hoy": e.consumo.hoy,
                "pendientes": e.pendientes,
                "camino": abiertos.get(m.id),
            }
        )
    eventos = list(
        db.scalars(select(EventoSim).where(EventoSim.corrida_id == c.id, EventoSim.id > desde_evento).order_by(EventoSim.id.desc()).limit(150))
    )
    eventos.reverse()
    from app import dinero

    resumen = dinero.resumen(db, c.id)
    lados, estantes_roble = comparacion(db, c, abiertos)
    pausa = None
    if c.causas:
        pausa = _detalle_pausa(db, c, c.causas[0], evaluaciones)
        seleccionado = seleccionado or pausa["medicamento_id"]
    seleccionado = seleccionado if seleccionado in c.parametros["meds"] else c.parametros["meds"][0]
    md = _minutos_dia(c)
    return {
        "corrida": {
            "id": c.id,
            "escenario": c.escenario,
            "titulo": ESCENARIOS[c.escenario].titulo,
            "semilla": c.semilla,
            "estado": c.estado,
            "demora_horas": (c.demora_gestion or 0) / 60,
        },
        "reloj": {
            "minuto": c.minuto,
            "dia": int(c.minuto // md) + 1,
            "fecha": momento.strftime("%d/%m/%Y"),
            "hora": momento.strftime("%H:%M"),
            "dia_semana": ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")[momento.weekday()],
            "velocidad": c.velocidad,
            "corriendo": corriendo(c),
            "pausa_manual": c.pausa_manual,
            "esperando": len(c.causas or []),
            "minutos_dia": md,
            "minutos_por_segundo": MINUTOS_POR_SEGUNDO,
        },
        "tarjetas": tarjetas,
        "eventos": [
            {"id": ev.id, "minuto": ev.minuto, "tipo": ev.tipo, "medicamento_id": ev.medicamento_id, "cantidad": ev.cantidad, "texto": ev.texto, "datos": ev.datos, "hora": momento_de(c, ev.minuto).strftime("%d/%m %H:%M")}
            for ev in eventos
        ],
        "dinero": {
            "consumido": resumen.consumido,
            "por_servicio": [list(x) for x in resumen.por_servicio],
            "unidades": resumen.unidades,
            "compras": resumen.compras_recibidas,
            "compromisos": resumen.compromisos,
            "por_gestionar": resumen.por_gestionar,
            "numero_despachos": resumen.numero_despachos,
            "faltantes": c.faltantes,
        },
        "servicios": [{"nombre": x.nombre, "icono": x.icono} for x in ESCENARIOS[c.escenario].servicios],
        "pausa": pausa,
        "seleccionado": seleccionado,
        "serie": serie(db, c, seleccionado, estantes_roble),
        "comparacion": lados,
    }


def _detalle_pausa(db: Session, c: Corrida, pedido_id: int, evaluaciones) -> dict:
    pedido = db.get(Pedido, pedido_id)
    linea, m = pedidos.lineas_de(db, pedido)[0]
    e = evaluaciones.get(m.id)
    mensajes = list(db.scalars(select(Mensaje).where(Mensaje.pedido_id == pedido.id).order_by(Mensaje.id)))
    return {
        "pedido": pedido.id,
        "medicamento_id": m.id,
        "nombre": m.nombre_completo,
        "unidad": m.unidad,
        "existencia": linea.existencia_aviso,
        "posicion": linea.posicion_aviso,
        "mu": linea.mu_d,
        "sigma": linea.sigma_d,
        "L": linea.tiempo_entrega,
        "Z": e.plan.Z if e and e.plan else None,
        "p": e.parametros["p"].valor if e else None,
        "sigma_L": e.plan.sigma_L if e and e.plan else None,
        "R": linea.punto_reorden,
        "R_pedido": punto_de_pedido(linea.punto_reorden) if linea.punto_reorden is not None else None,
        "IS": linea.inventario_seguridad,
        "Q": linea.cantidad_eoq,
        "sugerida": linea.cantidad_sugerida,
        "pedida": linea.cantidad_pedida,
        "paquete": m.unidades_paquete,
        "dias": e.consumo.dias if e else None,
        "en_cola": len(c.causas),
        "mensajes": [
            {"id": x.id, "estado": x.estado, "chat": x.chat_id, "error": x.ultimo_error, "intentos": x.intentos, "texto": x.texto}
            for x in mensajes
        ],
    }


def _solicitudes_atendidas(c: Corrida):
    """Las solicitudes que el lado de SALVAMED ya atendió, en orden: son las mismas que recibe el Roble."""
    escenario = ESCENARIOS[c.escenario]
    md = _minutos_dia(c)
    for dia in range(c.dia_cerrado + 2):
        clientes = _solicitudes(escenario, c.semilla, dia, md, fecha_del_dia(c, dia).weekday())
        hasta = len(clientes) if dia <= c.dia_cerrado else c.evento_siguiente
        for s in clientes[:hasta]:
            yield dia * md + s.minuto, s.servicio, s.compras


def _lado(filas: list[dict]) -> dict:
    claves = ("pedir", "mantener", "emergencia", "total", "pedidos", "despachado", "emergencia_u", "emergencia_veces")
    return {"filas": filas, **{k: sum(f[k] for f in filas) for k in claves}, "agotados": sum(1 for f in filas if f["existencia"] == 0)}


def _agrupar(c: Corrida, sucesos: list[tuple], meds: list[Medicamento], recargo: str, cuantos: int = 4) -> list[dict]:
    """Lo último que pasó en un lado. Las compras de emergencia seguidas de un mismo medicamento van en una sola línea."""
    nombres = {m.id: m for m in meds}
    lista, abiertas = [], {}
    for minuto, tipo, med_id, cantidad, texto in sucesos:
        if tipo != "emergencia":
            abiertas.pop(med_id, None)
            lista.append({"minuto": minuto, "tipo": tipo, "texto": texto})
            continue
        grupo = abiertas.get(med_id)
        if grupo is None:
            grupo = abiertas[med_id] = {"tipo": "emergencia", "cantidad": 0, "veces": 0}
            lista.append(grupo)
        grupo["cantidad"] += cantidad
        grupo["veces"] += 1
        grupo["minuto"] = minuto
        m = nombres.get(med_id)
        grupo["texto"] = f"No hay {m.nombre if m else '?'}: se compraron {grupo['cantidad']} de emergencia ({recargo})"
    lista = sorted(reversed(lista), key=lambda x: x["minuto"], reverse=True)  # lo más nuevo primero, también en el mismo minuto
    return [{"hora": momento_de(c, x["minuto"]).strftime("%d/%m %H:%M"), "tipo": x["tipo"], "texto": x["texto"]} for x in lista[:cuantos]]


def comparacion(db: Session, c: Corrida, abiertos: dict) -> tuple[dict | None, list]:
    """El Roble hoy contra SALVAMED con las mismas solicitudes. Devuelve el resumen y los estantes del Roble."""
    p = c.parametros
    if "tarifas" not in p:
        return None, []  # corrida creada antes de la comparación
    escenario = ESCENARIOS[c.escenario]
    md = _minutos_dia(c)
    tarifas = roble.Tarifas(**p["tarifas"])
    estantes, sucesos = roble.simular(escenario.productos, _solicitudes_atendidas(c), c.minuto, md, c.semilla, c.demora_gestion or 0)
    ids = p["meds"]
    meds = _medicamentos(db, c)

    # SALVAMED: un envío por pedido realizado; si el pedido trae varios medicamentos, el envío se reparte entre ellos.
    envios, numero, n_envios = [0.0] * len(ids), [0] * len(ids), 0
    for pedido in db.scalars(select(Pedido).where(Pedido.corrida_id == c.id, Pedido.realizado_en.is_not(None), Pedido.estado != CANCELADO)):
        n_envios += 1
        lineas = pedidos.lineas_de(db, pedido)
        for _, m in lineas:
            envios[ids.index(m.id)] += pedido.costo_envio / len(lineas)
            numero[ids.index(m.id)] += 1
    despachado = dict(
        db.execute(
            select(DespachoLinea.medicamento_id, func.sum(DespachoLinea.cantidad))
            .join(Despacho, Despacho.id == DespachoLinea.despacho_id)
            .where(Despacho.corrida_id == c.id, Despacho.estado == "confirmado")
            .group_by(DespachoLinea.medicamento_id)
        ).all()
    )
    resto = max(c.minuto - p["t_area"], 0)

    filas_salvamed, filas_roble = [], []
    for i, (prod, m, e) in enumerate(zip(escenario.productos, meds, estantes)):
        base = {"id": m.id, "nombre": m.nombre, "unidad": m.unidad}
        dias_s = (p["area"][i] + m.existencia * resto) / md
        camino = abiertos.get(m.id)
        filas_salvamed.append(
            {
                **base,
                "existencia": m.existencia,
                "en_camino": camino["cantidad"] if camino else 0,
                "llega": camino["llega"] if camino else None,
                "pedidos": numero[i],
                "despachado": int(despachado.get(m.id, 0)),
                "emergencia_u": p["emergencia"][i][0],
                "emergencia_veces": p["emergencia"][i][1],
                "unidades_dia": dias_s,
                **roble.costos(prod.costo, envios[i], dias_s, p["emergencia"][i][0], tarifas),
            }
        )
        dias_r = e.area / md
        filas_roble.append(
            {
                **base,
                "existencia": e.existencia,
                "en_camino": e.en_camino,
                "llega": momento_de(c, e.llega).strftime("%d/%m %H:%M") if e.llega is not None else None,
                "pedidos": e.pedidos,
                "despachado": e.despachado,
                "emergencia_u": e.emergencia_u,
                "emergencia_veces": e.emergencia_veces,
                "unidades_dia": dias_r,
                **roble.costos(prod.costo, e.pedidos * tarifas.S, dias_r, e.emergencia_u, tarifas),
            }
        )
    salvamed, el_roble = _lado(filas_salvamed), _lado(filas_roble)
    salvamed["envios"], el_roble["envios"] = n_envios, el_roble["pedidos"]
    recargo = f"+{tarifas.recargo * 100:g} %"
    roble_eventos = _agrupar(
        c,
        [
            (s.minuto, s.tipo, meds[s.indice].id, s.cantidad,
             f"Se acabó {meds[s.indice].nombre}: piden {s.cantidad} a ojo, llega el {momento_de(c, s.llega):%d/%m %H:%M}"
             if s.tipo == "agotado" else f"Llegó el pedido de {meds[s.indice].nombre}: +{s.cantidad}")
            for s in sorted(sucesos, key=lambda s: s.minuto)
        ],
        meds,
        recargo,
    )
    importantes = db.scalars(
        select(EventoSim)
        .where(EventoSim.corrida_id == c.id, EventoSim.tipo.in_(("aviso", "pedido", "recepcion", "faltante")))
        .order_by(EventoSim.id.desc())
        .limit(60)
    ).all()
    salvamed_eventos = _agrupar(
        c,
        [(ev.minuto, "emergencia" if ev.tipo == "faltante" else ev.tipo, ev.medicamento_id, ev.cantidad or 0, ev.texto) for ev in reversed(importantes)],
        meds,
        recargo,
    )
    ahorro = el_roble["total"] - salvamed["total"]
    return {
        "tarifas": {"S": tarifas.S, "tasa": tarifas.tasa, "A": tarifas.A, "recargo": tarifas.recargo},
        "salvamed": {**salvamed, "eventos": salvamed_eventos},
        "roble": {**el_roble, "eventos": roble_eventos},
        "demanda": {"salvamed": salvamed["despachado"] + salvamed["emergencia_u"], "roble": el_roble["despachado"] + el_roble["emergencia_u"]},
        "ahorro": ahorro,
        "ahorro_pct": ahorro / el_roble["total"] * 100 if el_roble["total"] > 0 else None,
    }, estantes


def serie(db: Session, c: Corrida, medicamento_id: int, estantes_roble: list | None = None) -> dict:
    """Existencia en el tiempo (en minutos de jornada), R e IS de cada día, pedidos y recepciones.

    También la existencia del Roble con las mismas solicitudes, para dibujarlas juntas."""
    puntos = [
        [round(minuto_de(c, mv.fecha), 2), mv.existencia_despues, mv.tipo]
        for mv in db.scalars(select(Movimiento).where(Movimiento.medicamento_id == medicamento_id).order_by(Movimiento.fecha, Movimiento.id))
    ]
    md = _minutos_dia(c)
    planes = []
    for plan in db.scalars(select(PlanDiario).where(PlanDiario.medicamento_id == medicamento_id).order_by(PlanDiario.dia)):
        indice = int(minuto_de(c, datetime.combine(plan.dia, time(c.parametros["apertura"]), GUATEMALA)) // md)
        planes.append([indice * md, (indice + 1) * md, plan.punto_reorden, plan.inventario_seguridad])
    marcas = []
    for pedido, linea in db.execute(
        select(Pedido, PedidoLinea).join(PedidoLinea, PedidoLinea.pedido_id == Pedido.id).where(PedidoLinea.medicamento_id == medicamento_id)
    ).tuples():
        marcas.append([round(minuto_de(c, pedido.creado), 2), "aviso", f"Aviso #{pedido.id}"])
        if pedido.realizado_en:
            marcas.append([round(minuto_de(c, pedido.realizado_en), 2), "pedido", f"Pedido {linea.cantidad_pedida}"])
    for mv in db.scalars(select(Movimiento).where(Movimiento.medicamento_id == medicamento_id, Movimiento.tipo == "recepcion")):
        marcas.append([round(minuto_de(c, mv.fecha), 2), "recepcion", f"+{mv.cantidad}"])
    roble_puntos = []
    if estantes_roble and medicamento_id in c.parametros["meds"]:
        roble_puntos = estantes_roble[c.parametros["meds"].index(medicamento_id)].puntos
    return {
        "medicamento_id": medicamento_id, "puntos": puntos, "planes": planes, "marcas": marcas, "roble": roble_puntos,
        "ahora": c.minuto, "minutos_dia": md,
    }


def corridas_anteriores(db: Session, limite: int = 8) -> list[dict]:
    resultado = []
    for c in db.scalars(select(Corrida).order_by(Corrida.id.desc()).limit(limite)):
        consumido = db.scalar(select(func.coalesce(func.sum(Despacho.costo_total), 0)).where(Despacho.corrida_id == c.id))
        numero_pedidos = db.scalar(select(func.count()).select_from(Pedido).where(Pedido.corrida_id == c.id))
        resultado.append(
            {
                "id": c.id,
                "escenario": ESCENARIOS.get(c.escenario, ESCENARIOS["base"]).titulo,
                "semilla": c.semilla,
                "demora": (c.demora_gestion or 0) / 60,
                "dias": c.dia_cerrado + 1,
                "consumido": float(consumido),
                "pedidos": numero_pedidos,
                "faltantes": c.faltantes,
                "estado": c.estado,
            }
        )
    return resultado
