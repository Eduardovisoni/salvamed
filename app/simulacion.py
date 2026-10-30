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

Recepción. Solo en la simulación llega sola: cuando se cumple el plazo L (en
días de operación) desde que el pedido se realizó, se registra la entrada.

Comparación. La corrida lleva un segundo inventario, «El Roble hoy», con las mismas
solicitudes de los servicios, guardado en `parametros["roble"]`. No usa el modelo:
cuando alguien nota que queda poco de un producto avisa de boca en boca, al día
siguiente el gerente lo pide al proveedor («lo de siempre») y llega por mensajería
en el mismo plazo L que con SALVAMED, con el mismo costo de envío. En los dos lados,
si algo ya no hay y lo necesita un servicio crítico, se compra de emergencia
(llega en 2 horas, con recargo); si lo pide otro servicio, se cuenta como faltante.
Los supuestos están en docs/comparacion_empirica.md.
"""

import math
import random
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import avisos, inventario, pedidos
from app.consumo import GUATEMALA, dia_local, hora_local
from app.db import (
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
    Proveedor,
    ahora,
)
from app.inventario import LineaPedida
from app.modelo import cantidad_por_paquetes, punto_de_pedido
from app.planificacion import configuracion, evaluar_varios

MINUTOS_POR_SEGUNDO = 20
VELOCIDADES = (1, 2, 5, 10, 20)
MAX_EVENTOS_POR_PASO = 400
candado = threading.RLock()

# Supuestos de la comparación con «El Roble hoy» (docs/comparacion_empirica.md). Ninguno es dato del sanatorio.
SERVICIOS_EMERGENCIA = ("Emergencia", "Cuidados Intensivos", "Sala de operaciones")
RECARGO_EMERGENCIA = 0.5  # una compra de emergencia cuesta el costo normal más este porcentaje
MINUTOS_EMERGENCIA = 120  # una emergencia llega en 2 horas
AVISO_ROBLE = 0.25  # Roble hoy: alguien avisa cuando queda menos de esta parte de «lo de siempre»
DEMORA_AVISO_ROBLE = 1  # días que tarda el aviso de boca en boca en convertirse en pedido


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
    habito: float = 16  # Roble hoy: días de consumo que trae «lo de siempre» (costumbre, no cálculo)


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
    Producto("Ceftriaxona", "1 g", "vial", "Proveedor A", 12.5, 10, 42, 2, 6, 2,
             (("Encamamiento", 0.45), ("Sala de operaciones", 0.25), ("Cuidados Intensivos", 0.2), ("Emergencia", 0.1)), habito=12),
    Producto("Paracetamol", "1 g/100 ml", "frasco IV", "Proveedor A", 18.0, 12, 190, 2, 15, 3,
             (("Encamamiento", 0.35), ("Emergencia", 0.3), ("Traumatología", 0.2), ("Sala de operaciones", 0.15)), habito=20),
    Producto("Solución salina", "0.9 % · 100 ml", "bolsa", "Proveedor A", 5.5, 20, 330, 2, 22, 4,
             (("Emergencia", 0.35), ("Encamamiento", 0.25), ("Cuidados Intensivos", 0.15), ("Sala de operaciones", 0.15), ("Ambulancia", 0.1)), habito=14),
    Producto("Dexametasona", "4 mg/ml · 2 ml", "ampolla", "Proveedor B", 5.9, 10, 95, 3, 8, 2,
             (("Emergencia", 0.45), ("Encamamiento", 0.25), ("Traumatología", 0.2), ("Cuidados Intensivos", 0.1)), habito=22),
    Producto("Metoclopramida", "10 mg/2 ml", "ampolla", "Proveedor B", 4.7, 10, 110, 3, 7, 2,
             (("Sala de operaciones", 0.4), ("Emergencia", 0.3), ("Encamamiento", 0.3)), habito=11),
    Producto("Salbutamol", "2.5 mg/2.5 ml", "monodosis", "Proveedor B", 4.1, 20, 150, 3, 10, 3,
             (("Emergencia", 0.6), ("Encamamiento", 0.25), ("Cuidados Intensivos", 0.15)), habito=18),
    Producto("Insulina cristalina", "U-100 · 10 ml", "vial", "Proveedor C", 88.0, 1, 22, 3, 1.2, 1,
             (("Encamamiento", 0.6), ("Cuidados Intensivos", 0.4)), habito=24),
    Producto("Adrenalina (epinefrina)", "1 mg/ml · 1 ml", "ampolla", "Proveedor C", 7.5, 10, 30, 3, 1.5, 2,
             (("Emergencia", 0.35), ("Cuidados Intensivos", 0.3), ("Ambulancia", 0.25), ("Sala de operaciones", 0.1)), habito=10),
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
    c = db.scalar(select(Corrida).where(Corrida.estado == "activa").order_by(Corrida.id.desc()))
    if c is not None and "nivel" not in (c.parametros.get("roble") or {}):
        # Corrida de una versión anterior, sin la comparación con el Roble hoy: se da por terminada.
        c.estado = "terminada"
        db.commit()
        return None
    return c


def crear_corrida(db: Session, clave: str = "base", semilla: int | None = None, demora_horas: float = 0) -> Corrida:
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
            "costo_envio": cfg.costo_pedido,
            "tasa": cfg.tasa_mantenimiento,
            "roble": _roble_inicial(escenario),
            "salvamed": {"emergencias": 0, "emergencias_u": 0, "recargo_q": 0.0, "faltantes_q": 0.0, "dinero_dias": 0.0},
        }
        db.add(corrida)
        db.flush()
        momento = momento_de(corrida, 0)
        proveedores = {}
        ids = []
        for p in escenario.productos:
            if p.proveedor not in proveedores:
                # Los medicamentos de un proveedor viajan juntos (se agrupan en el mismo envío): el costo del
                # envío se reparte entre ellos para la EOQ de cada uno (S = envío ÷ medicamentos del proveedor).
                juntos = sum(1 for x in escenario.productos if x.proveedor == p.proveedor)
                proveedor = Proveedor(corrida_id=corrida.id, nombre=p.proveedor, costo_envio=cfg.costo_pedido, articulos_por_envio=juntos)
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
        _evento(
            db, corrida, 0, "inicio",
            texto=f"Corrida #{corrida.id}: {escenario.titulo} (semilla {corrida.semilla}). Los dos inventarios arrancan iguales y reciben "
            "las mismas solicitudes. SALVAMED calcula su plan cuando haya días completos; el Roble hoy pide de boca en boca.",
        )
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
        clientes = solicitudes_del_dia(escenario, c.semilla, dia, md, fecha.weekday())
        siguiente_cliente = dia * md + clientes[c.evento_siguiente].minuto if c.evento_siguiente < len(clientes) else None
        cierre = (dia + 1) * md
        llegada = _siguiente_llegada(db, c)
        candidatos = [(cierre, 3, None)]
        if siguiente_cliente is not None:
            candidatos.append((siguiente_cliente, 2, clientes[c.evento_siguiente]))
        if llegada is not None:
            candidatos.append((max(llegada[0], c.minuto), 0, llegada[1]))
        roble = _roble_siguiente(c)
        if roble is not None:
            candidatos.append((max(roble[0], c.minuto), 1, roble[1]))
        minuto, clase, dato = min(candidatos, key=lambda x: (x[0], x[1]))
        if minuto > hasta:
            break
        c.minuto = minuto
        procesados += 1
        if clase == 0:
            hubo = _recibir(db, c, dato, minuto)
        elif clase == 1:
            hubo = _roble_evento(db, c, dato, minuto)
        elif clase == 2:
            hubo = _atender(db, c, dato, dia, minuto)
            c.evento_siguiente += 1
        else:
            hubo = _cerrar_dia(db, c, dia, minuto, len(clientes))
        db.commit()
        if detener_en_evento and hubo:
            break
    if not c.causas and c.minuto < hasta and procesados < limite:
        c.minuto = hasta
    if procesados >= limite:
        _reanclar(c)  # el reloj no puede ir más rápido de lo que se procesa
    db.commit()
    return procesados


def _medicamentos(db: Session, c: Corrida) -> list[Medicamento]:
    return [db.get(Medicamento, i) for i in c.parametros["meds"]]


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
            faltantes.append((m, cantidad - entregable))
    if lineas:
        despacho = inventario.despachar(
            db, solicitud.servicio, lineas, f"sim{c.id}-{dia}-{solicitud.numero}", momento, corrida_id=c.id, usuario="Servicio simulado"
        )
        _evento(
            db, c, minuto, "despacho", cantidad=sum(n for _, n in items), texto=solicitud.servicio,
            items=items, costo=despacho.costo_total, servicio=solicitud.servicio, solicitud=solicitud.numero,
        )
    if faltantes:
        sv = dict(c.parametros["salvamed"])
        for m, falta in faltantes:
            # Ya no hay: si lo necesita un servicio crítico se compra de emergencia (sin preguntar); si no, falta.
            if solicitud.servicio in SERVICIOS_EMERGENCIA and _emergencia_salvamed(db, c, m, falta, minuto, solicitud.servicio, sv):
                continue
            c.faltantes += falta
            sv["faltantes_q"] = sv.get("faltantes_q", 0.0) + falta * (m.costo_compra or 0)
            _evento(
                db, c, minuto, "faltante", m.id, falta, lado="salvamed",
                texto=f"{solicitud.servicio} pidió {m.nombre} y no había: faltaron {falta} {m.unidad}", servicio=solicitud.servicio,
            )
        c.parametros = dict(c.parametros, salvamed=sv)
    _roble_atender(db, c, solicitud, minuto)
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
            if e.estado.clave == "pedir":
                texto = f"{m.nombre} llegó al punto de reorden: posición {e.posicion} ≤ {e.plan.R_pedido} (R {e.plan.R:.1f} hacia arriba). Aviso enviado por Telegram."
            else:
                texto = (f"➕ {m.nombre} va en el mismo envío: todavía no llega a R ({e.posicion} > {e.plan.R_pedido}), "
                         "pero ya está en «pedir pronto»; así el envío se paga una sola vez.")
            _evento(db, c, minuto, "aviso", m.id, linea.cantidad_pedida, texto=texto, pedido=nuevo.id)
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
    items = [[m.id, linea.cantidad_recibida] for linea, m in lineas]
    que = "la emergencia" if pedido.origen == "emergencia" else f"el pedido #{pedido.id}"
    detalle = ", ".join(f"+{linea.cantidad_recibida} {m.nombre}" for linea, m in lineas)
    _evento(
        db, c, minuto, "recepcion", cantidad=sum(n for _, n in items), lado="salvamed", items=items, pedido=pedido.id, origen=pedido.origen,
        texto=f"🚚 Con SALVAMED llegó {que}: {detalle}",
    )
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
    _contar_bodega(c, _medicamentos(db, c))
    return _revisar_varios(db, c, _medicamentos(db, c), minuto, momento_de(c, minuto))


def _contar_bodega(c: Corrida, meds) -> None:
    """Foto diaria del dinero parado en cada bodega, para el costo de mantener."""
    sv = dict(c.parametros["salvamed"])
    sv["dinero_dias"] = sv.get("dinero_dias", 0.0) + sum(m.existencia * (m.costo_compra or 0) for m in meds)
    roble = dict(c.parametros["roble"])
    productos = ESCENARIOS[c.escenario].productos
    roble["dinero_dias"] = roble.get("dinero_dias", 0.0) + sum(ex * p.costo for ex, p in zip(roble["existencia"], productos))
    c.parametros = dict(c.parametros, salvamed=sv, roble=roble)


def _emergencia_salvamed(db: Session, c: Corrida, m: Medicamento, falta: int, minuto: float, servicio: str, sv: dict) -> bool:
    """Ya no hay y lo necesita un servicio crítico: compra de emergencia automática, igual que en el Roble hoy.

    Trae lo que faltó más lo que se usa mientras llegaría un pedido normal (L días, con μD estimado).
    Si ya viene una emergencia de ese medicamento no se pide otra. Devuelve si se pidió.
    """
    abierta = db.scalar(
        select(Pedido.id)
        .join(PedidoLinea, PedidoLinea.pedido_id == Pedido.id)
        .where(PedidoLinea.medicamento_id == m.id, Pedido.origen == "emergencia", Pedido.estado.in_(("pendiente", "realizado")))
    )
    if abierta is not None:
        return False
    momento = momento_de(c, minuto)
    e = evaluar_varios(db, [m], momento)[m.id]
    indice = c.parametros["meds"].index(m.id)
    producto = ESCENARIOS[c.escenario].productos[indice]
    mu = e.consumo.mu_d if e.consumo.disponible else producto.consumo
    cantidad = cantidad_por_paquetes(max(falta + mu * (m.tiempo_entrega or producto.plazo) - m.existencia, falta), m.unidades_paquete)
    pedido = pedidos.crear_pedido(db, e, momento, origen="emergencia", cantidad=cantidad)
    pedido.costo_envio = 0
    pedido.nota = f"Emergencia: {servicio} lo necesitó y no había"
    llegada = minuto + MINUTOS_EMERGENCIA
    pedidos.marcar_realizado(db, pedido, "Automático (no había)", momento, momento_de(c, llegada))
    c.parametros = dict(c.parametros, llegadas={**(c.parametros.get("llegadas") or {}), str(pedido.id): llegada})
    recargo = cantidad * (m.costo_compra or 0) * RECARGO_EMERGENCIA
    sv["emergencias"] = sv.get("emergencias", 0) + 1
    sv["emergencias_u"] = sv.get("emergencias_u", 0) + falta
    sv["recargo_q"] = sv.get("recargo_q", 0.0) + recargo
    avisos.encolar(db, "emergencia", avisos.texto_emergencia(m, pedido, servicio, falta, cantidad, momento_de(c, llegada), recargo), c.id, pedido.id, m.id)
    _evento(
        db, c, minuto, "emergencia", m.id, cantidad, lado="salvamed", servicio=servicio, pedido=pedido.id,
        texto=f"🚨 Con SALVAMED: {servicio} pidió {m.nombre} y ya no había. Emergencia de {cantidad} {m.unidad} "
        f"(+{RECARGO_EMERGENCIA * 100:.0f} %), llega en {MINUTOS_EMERGENCIA / 60:g} h. Aviso informativo por Telegram.",
    )
    return True


# ---------------------------------------------------------------- «El Roble hoy» (inventario sombra, sin modelo)


def _roble_inicial(escenario: Escenario) -> dict:
    return {
        "existencia": [p.existencia for p in escenario.productos],
        "nivel": [max(int(round(p.consumo * p.habito)), 1) for p in escenario.productos],  # «lo de siempre»
        "avisos": {},  # índice → minuto en que el aviso llega al gerente
        "en_camino": {},  # índice → unidades pedidas que no han llegado
        "llegadas": [],  # [{"minuto", "tipo": "envio" | "emergencia", "proveedor", "items": [[índice, cantidad], ...]}]
        "envios": 0,
        "envios_q": 0.0,
        "emergencias": 0,
        "emergencias_u": 0,
        "recargo_q": 0.0,
        "faltantes_u": 0,
        "faltantes_q": 0.0,
        "dinero_dias": 0.0,
    }


def _roble_siguiente(c: Corrida) -> tuple[float, dict] | None:
    roble = c.parametros.get("roble")
    if not roble:
        return None
    candidatos = [(float(x["minuto"]), x) for x in roble.get("llegadas", [])]
    candidatos += [(float(m), {"tipo": "aviso", "indice": int(i), "minuto": m}) for i, m in roble.get("avisos", {}).items()]
    return min(candidatos, key=lambda x: x[0]) if candidatos else None


def _roble_evento(db: Session, c: Corrida, dato: dict, minuto: float) -> bool:
    roble = dict(c.parametros["roble"])
    productos = ESCENARIOS[c.escenario].productos
    md = _minutos_dia(c)
    existencia = list(roble["existencia"])
    en_camino = {int(k): v for k, v in roble.get("en_camino", {}).items()}
    avisos_pendientes = {int(k): v for k, v in roble.get("avisos", {}).items()}
    llegadas = list(roble.get("llegadas", []))
    if dato["tipo"] == "aviso":
        # El aviso llegó al gerente: pide por teléfono al proveedor todo lo que le han avisado de ese proveedor.
        proveedor = productos[dato["indice"]].proveedor
        items = []
        for i in sorted(avisos_pendientes):
            if productos[i].proveedor != proveedor:
                continue
            del avisos_pendientes[i]
            falta = roble["nivel"][i] - existencia[i] - en_camino.get(i, 0)
            if falta > 0:
                cantidad = cantidad_por_paquetes(falta, productos[i].paquete)
                items.append([i, cantidad])
                en_camino[i] = en_camino.get(i, 0) + cantidad
        if items:
            plazo = max(productos[i].plazo for i, _ in items)
            llegadas.append({"minuto": minuto + plazo * md, "tipo": "envio", "proveedor": proveedor, "items": items})
            costo = float(c.parametros.get("costo_envio", 35))
            roble["envios"] = roble.get("envios", 0) + 1
            roble["envios_q"] = roble.get("envios_q", 0.0) + costo
            detalle = ", ".join(f"{n}× {productos[i].nombre}" for i, n in items)
            _evento(db, c, minuto, "pedido", cantidad=sum(n for _, n in items), lado="roble", items=items,
                    texto=f"📞 Roble hoy: el gerente pidió «lo de siempre» a {proveedor}: {detalle}. Envío Q {costo:,.2f}, llega en {plazo:g} días.")
    else:
        for i, x in enumerate(llegadas):
            if x == dato:
                del llegadas[i]
                break
        for i, cantidad in dato["items"]:
            existencia[i] += cantidad
            if dato["tipo"] == "envio":
                en_camino[i] = max(en_camino.get(i, 0) - cantidad, 0)
        que = "la emergencia" if dato["tipo"] == "emergencia" else f"el pedido de {dato.get('proveedor', '')}"
        detalle = ", ".join(f"+{n} {productos[i].nombre}" for i, n in dato["items"])
        _evento(db, c, minuto, "recepcion", cantidad=sum(n for _, n in dato["items"]), lado="roble", items=dato["items"],
                origen=dato["tipo"], texto=f"🚚 Roble hoy: llegó {que}: {detalle}")
    roble.update(
        existencia=existencia,
        en_camino={str(k): v for k, v in en_camino.items() if v},
        avisos={str(k): v for k, v in avisos_pendientes.items()},
        llegadas=llegadas,
    )
    c.parametros = dict(c.parametros, roble=roble)
    return False


def _roble_atender(db: Session, c: Corrida, solicitud: Solicitud, minuto: float) -> None:
    """La misma solicitud, atendida por el inventario del Roble hoy."""
    roble = dict(c.parametros["roble"])
    productos = ESCENARIOS[c.escenario].productos
    md = _minutos_dia(c)
    existencia = list(roble["existencia"])
    en_camino = {int(k): v for k, v in roble.get("en_camino", {}).items()}
    avisos_pendientes = {int(k): v for k, v in roble.get("avisos", {}).items()}
    llegadas = list(roble.get("llegadas", []))
    for i, cantidad in solicitud.compras:
        p = productos[i]
        entregable = min(cantidad, existencia[i])
        existencia[i] -= entregable
        falta = cantidad - entregable
        if falta > 0:
            ya_viene = any(x["tipo"] == "emergencia" and x["items"][0][0] == i for x in llegadas)
            if solicitud.servicio in SERVICIOS_EMERGENCIA and not ya_viene:
                traer = cantidad_por_paquetes(falta + p.consumo * p.plazo, p.paquete)
                llegadas.append({"minuto": minuto + MINUTOS_EMERGENCIA, "tipo": "emergencia", "items": [[i, traer]]})
                recargo = traer * p.costo * RECARGO_EMERGENCIA
                roble["emergencias"] = roble.get("emergencias", 0) + 1
                roble["emergencias_u"] = roble.get("emergencias_u", 0) + falta
                roble["recargo_q"] = roble.get("recargo_q", 0.0) + recargo
                _evento(db, c, minuto, "emergencia", cantidad=traer, lado="roble", servicio=solicitud.servicio, indice=i,
                        texto=f"🚨 Roble hoy: {solicitud.servicio} pidió {p.nombre} y ya no había. Emergencia de {traer} {p.unidad} "
                        f"(+{RECARGO_EMERGENCIA * 100:.0f} %), llega en {MINUTOS_EMERGENCIA / 60:g} h.")
            else:
                roble["faltantes_u"] = roble.get("faltantes_u", 0) + falta
                roble["faltantes_q"] = roble.get("faltantes_q", 0.0) + falta * p.costo
                _evento(db, c, minuto, "faltante", cantidad=falta, lado="roble", servicio=solicitud.servicio, indice=i,
                        texto=f"❌ Roble hoy: {solicitud.servicio} pidió {p.nombre} y no había: faltaron {falta} {p.unidad}.")
        # Alguien nota que ya se va a acabar y avisa: bodega → jefe de turno → doctor → gerente (un día).
        if existencia[i] < roble["nivel"][i] * AVISO_ROBLE and i not in avisos_pendientes and not en_camino.get(i):
            avisos_pendientes[i] = minuto + DEMORA_AVISO_ROBLE * md
            _evento(db, c, minuto, "anotado", lado="roble", indice=i,
                    texto=f"📣 Roble hoy: bodega avisa que ya se va a acabar {p.nombre} ({existencia[i]} {p.unidad}) → jefe de turno → doctor → gerente.")
    roble.update(existencia=existencia, avisos={str(k): v for k, v in avisos_pendientes.items()}, llegadas=llegadas)
    c.parametros = dict(c.parametros, roble=roble)


def _marcador(db: Session, c: Corrida, meds) -> dict:
    sv = c.parametros.get("salvamed") or {}
    roble = c.parametros.get("roble") or {}
    tasa = float(c.parametros.get("tasa", 0.25))
    dias = c.dia_cerrado + 1
    envios_sv = db.execute(
        select(func.count(), func.coalesce(func.sum(Pedido.costo_envio), 0)).where(
            Pedido.corrida_id == c.id, Pedido.origen != "emergencia", Pedido.estado.in_((REALIZADO, "recibido"))
        )
    ).one()

    def lado(envios, envios_q, emergencias, recargo, faltantes_u, faltantes_q, dinero_dias):
        mantener = dinero_dias * tasa / 365
        total = envios_q + recargo + mantener
        return {
            "envios": envios,
            "envios_q": envios_q,
            "emergencias": emergencias,
            "recargo_q": recargo,
            "faltantes_u": faltantes_u,
            "faltantes_q": faltantes_q,
            "mantener_q": mantener,
            "bodega_promedio_q": dinero_dias / dias if dias > 0 else 0.0,
            "total": total,
            "total_con_faltantes": total + faltantes_q,
        }

    return {
        "dias": max(dias, 0),
        "roble": lado(
            roble.get("envios", 0), roble.get("envios_q", 0.0), roble.get("emergencias", 0), roble.get("recargo_q", 0.0),
            roble.get("faltantes_u", 0), roble.get("faltantes_q", 0.0), roble.get("dinero_dias", 0.0),
        ),
        "salvamed": lado(
            int(envios_sv[0]), float(envios_sv[1]), sv.get("emergencias", 0), sv.get("recargo_q", 0.0),
            c.faltantes, sv.get("faltantes_q", 0.0), sv.get("dinero_dias", 0.0),
        ),
    }


def _cobertura(posicion: float, mu: float, plazo: float) -> str:
    """Color del estante del Roble hoy: vacío, rojo (no llega a cubrir un pedido), amarillo (justo) o verde."""
    if posicion <= 0:
        return "vacio"
    if mu <= 0:
        return "bien"
    dias = posicion / mu
    if dias < plazo:
        return "pedir"
    if dias < plazo + 3:
        return "pronto"
    return "bien"


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
        elif accion == "terminar":
            c.pausa_manual = True
            _evento(db, c, c.minuto, "fin", texto=f"Fin de la demostración en el día {int(c.minuto // md) + 1}. Resumen comparativo en pantalla.")
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
        llegada = minuto + plazo * _minutos_dia(c)
        cambio, texto = pedidos.marcar_realizado(db, pedido, usuario, momento_de(c, minuto), momento_de(c, llegada))
        if not cambio:
            return cambio, texto
        c.parametros = dict(c.parametros, llegadas={**(c.parametros.get("llegadas") or {}), str(pedido.id): llegada})
        cantidad = sum(l.cantidad_pedida for l, _ in pedidos.lineas_de(db, pedido))
        demora = f" (respuesta modelada: +{c.demora_gestion / 60:g} h virtuales)" if c.demora_gestion else ""
        _evento(
            db, c, c.minuto, "pedido", cantidad=cantidad,
            texto=f"Pedido #{pedido.id} realizado por {usuario}{demora}: {cantidad} unidades; llega en {plazo:g} días de operación ({momento_de(c, llegada):%d/%m %H:%M}).",
            pedido=pedido.id, llegada=llegada,
        )
        _quitar(db, c, pedido.id)
        db.commit()
        return True, f"Pedido #{pedido.id} registrado (simulación). Llega el {momento_de(c, llegada):%d/%m %H:%M} virtual."


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
                "indice": len(tarjetas),
            }
        )
    productos = ESCENARIOS[c.escenario].productos
    roble = c.parametros.get("roble") or _roble_inicial(ESCENARIOS[c.escenario])
    en_camino_roble = {int(k): v for k, v in (roble.get("en_camino") or {}).items()}
    for x in roble.get("llegadas", []):
        if x["tipo"] == "emergencia":
            for i, n in x["items"]:
                en_camino_roble[i] = en_camino_roble.get(i, 0) + n
    tarjetas_roble = [
        {
            "id": meds[i].id,
            "indice": i,
            "nombre": p.nombre,
            "presentacion": p.presentacion,
            "unidad": p.unidad,
            "existencia": roble["existencia"][i],
            "nivel": roble["nivel"][i],
            "habito": p.habito,
            "en_camino": en_camino_roble.get(i, 0),
            "anotado": str(i) in (roble.get("avisos") or {}),
            "estado": _cobertura(roble["existencia"][i] + en_camino_roble.get(i, 0), p.consumo, p.plazo),
        }
        for i, p in enumerate(productos)
    ]
    recientes = list(
        db.scalars(select(EventoSim).where(EventoSim.corrida_id == c.id, EventoSim.id > desde_evento).order_by(EventoSim.id.desc()).limit(150))
    )
    importantes = list(
        db.scalars(
            select(EventoSim)
            .where(EventoSim.corrida_id == c.id, EventoSim.id > desde_evento, EventoSim.tipo != "despacho")
            .order_by(EventoSim.id.desc())
            .limit(300)
        )
    )
    eventos = sorted({ev.id: ev for ev in recientes + importantes}.values(), key=lambda ev: ev.id)
    from app import dinero

    resumen = dinero.resumen(db, c.id)
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
        "roble": {"tarjetas": tarjetas_roble},
        "marcador": _marcador(db, c, meds),
        "supuestos": {
            "costo_envio": float(c.parametros.get("costo_envio", 35)),
            "recargo": RECARGO_EMERGENCIA,
            "emergencia_horas": MINUTOS_EMERGENCIA / 60,
            "aviso_roble": AVISO_ROBLE,
            "tasa": float(c.parametros.get("tasa", 0.25)),
        },
        "pausa": pausa,
        "seleccionado": seleccionado,
        "serie": serie(db, c, seleccionado),
    }


def _detalle_pausa(db: Session, c: Corrida, pedido_id: int, evaluaciones) -> dict:
    pedido = db.get(Pedido, pedido_id)
    todas = pedidos.lineas_de(db, pedido)
    linea, m = todas[0]
    e = evaluaciones.get(m.id)
    mensajes = list(db.scalars(select(Mensaje).where(Mensaje.pedido_id == pedido.id).order_by(Mensaje.id)))
    return {
        "pedido": pedido.id,
        "mismo_envio": [f"{l.cantidad_pedida} {med.nombre}" for l, med in todas[1:]],
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


def serie(db: Session, c: Corrida, medicamento_id: int) -> dict:
    """Existencia en el tiempo (en minutos de jornada), R e IS de cada día, pedidos y recepciones."""
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
    return {"medicamento_id": medicamento_id, "puntos": puntos, "planes": planes, "marcas": marcas, "ahora": c.minuto, "minutos_dia": md}


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
                "faltantes_roble": (c.parametros.get("roble") or {}).get("faltantes_u", 0),
                "estado": c.estado,
            }
        )
    return resultado
