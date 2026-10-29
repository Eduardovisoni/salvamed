"""Simulación de una farmacia en tiempo acelerado.

Usa las mismas funciones que la operación (vender, evaluar_reposicion, recibir,
los avisos de Telegram), pero todo lo que crea lleva `corrida_id`: nunca toca el
inventario, el dinero ni los pedidos reales.

Reloj. El tiempo virtual se mide en minutos de farmacia abierta (de la hora de
apertura a la de cierre, solo los días que abre). A velocidad ×1, 1 segundo real
son 10 minutos virtuales: un día de 12 horas dura 72 s; a ×5, unos 14 s, y una
semana menos de dos minutos. La velocidad solo cambia el ritmo: los clientes de
cada día salen de una semilla (escenario + número de día), así que con la misma
semilla la corrida produce las mismas ventas, avisos y recepciones.

El estado vive en la base, no en el navegador: refrescar o abrir otra pestaña
no duplica ventas, recepciones ni mensajes. Cada cliente usa un identificador
fijo como token de venta y el avance se hace con un candado.

Pausa de exposición. Cuando un medicamento llega a su punto de reorden, el reloj
virtual se detiene en ese instante, se envía el aviso por Telegram y se espera a
que el expositor (en el papel del encargado) pulse «Pedido realizado». Es un
recurso para explicar el flujo: no significa que una farmacia real deje de
vender mientras el jefe responde. La respuesta del encargado se modela como
inmediata en tiempo virtual, o con la demora de gestión que se elija al crear
la corrida. Si varios medicamentos avisan a la vez, cada aviso es una causa de
pausa y el reloj sigue solo cuando se confirmaron todas.

Recepción. Solo en la simulación llega sola: cuando se cumple el plazo L (en
días de operación) desde que el pedido se realizó, se registra la entrada.
"""

import math
import random
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import inventario, pedidos
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
    Proveedor,
    Venta,
    ahora,
)
from app.inventario import LineaPedida
from app.planificacion import configuracion, evaluar_varios

MINUTOS_POR_SEGUNDO = 10
VELOCIDADES = (1, 2, 4, 5)
MAX_EVENTOS_POR_PASO = 400
candado = threading.RLock()


# ---------------------------------------------------------------- Escenarios


@dataclass(frozen=True)
class Producto:
    nombre: str
    presentacion: str
    unidad: str
    proveedor: str
    costo: float
    precio: float
    paquete: int
    existencia: int
    plazo: float
    consumo: float  # unidades por día que el generador busca en promedio (no lo conoce el sistema)
    cantidad_max: int = 2


@dataclass(frozen=True)
class Escenario:
    clave: str
    titulo: str
    descripcion: str
    semilla: int
    productos: tuple[Producto, ...]
    patron_semana: tuple[float, ...] = (1.1, 1.0, 1.0, 1.05, 1.2, 0.9, 0.65)
    aumento: tuple[str, int, float] | None = None  # (nombre, desde el día, factor)
    inicio: date = date(2026, 11, 2)


_PRODUCTOS = (
    Producto("Ceftriaxona", "1 g", "vial", "Proveedor A", 12.5, 16.9, 10, 42, 3, 6, 2),
    Producto("Paracetamol", "1 g/100 ml", "frasco IV", "Proveedor A", 18.0, 24.3, 12, 120, 3, 15, 3),
    Producto("Solución salina", "0.9 % · 100 ml", "bolsa", "Proveedor A", 5.5, 7.45, 20, 260, 3, 22, 4),
    Producto("Dexametasona", "4 mg/ml · 2 ml", "ampolla", "Proveedor B", 5.9, 8.0, 10, 95, 3, 8, 2),
    Producto("Metoclopramida", "10 mg/2 ml", "ampolla", "Proveedor B", 4.7, 6.35, 10, 110, 3, 7, 2),
    Producto("Salbutamol", "2.5 mg/2.5 ml", "monodosis", "Proveedor B", 4.1, 5.55, 20, 150, 3, 10, 3),
    Producto("Insulina cristalina", "U-100 · 10 ml", "vial", "Proveedor C", 88.0, 118.8, 1, 16, 4, 1.2, 1),
    Producto("Adrenalina (epinefrina)", "1 mg/ml · 1 ml", "ampolla", "Proveedor C", 7.5, 10.15, 10, 22, 3, 1.5, 2),
)

ESCENARIOS = {
    e.clave: e
    for e in (
        Escenario(
            "base",
            "Farmacia en un mes normal",
            "Ocho medicamentos, días altos, bajos y sin ventas. Se ve el primer aviso, la confirmación por Telegram, "
            "el consumo de parte de la reserva mientras corre L y la recepción a tiempo.",
            2026,
            _PRODUCTOS,
        ),
        Escenario(
            "aumento",
            "Aumento de demanda de salbutamol",
            "Igual que el mes normal, pero desde el día 9 el salbutamol se vende 2.3 veces más. "
            "Se ve cómo la estimación se actualiza, el aviso de aumento y que puede haber faltantes.",
            2026,
            _PRODUCTOS,
            aumento=("Salbutamol", 9, 2.3),
        ),
    )
}


@dataclass(frozen=True)
class Cliente:
    minuto: float  # minuto dentro del día
    numero: int
    compras: tuple[tuple[int, int], ...]  # (índice de producto, cantidad)


def clientes_del_dia(escenario: Escenario, semilla: int, dia: int, minutos_dia: int, dia_semana: int) -> list[Cliente]:
    """Clientes de un día, siempre los mismos para la misma semilla y el mismo día."""
    azar = random.Random(f"salvamed:{semilla}:{escenario.clave}:{dia}")
    factor = escenario.patron_semana[dia_semana] * math.exp(azar.gauss(0, 0.18))
    tipo = azar.random()
    if tipo < 0.08:
        factor *= 0.45  # día flojo
    elif tipo > 0.92:
        factor *= 1.5  # día alto
    pesos = []
    for p in escenario.productos:
        media_cantidad = (1 + p.cantidad_max) / 2
        peso = p.consumo / media_cantidad
        if escenario.aumento and escenario.aumento[0] == p.nombre and dia >= escenario.aumento[1]:
            peso *= escenario.aumento[2]
        pesos.append(peso)
    articulos_por_cliente = 1.45
    esperado = sum(pesos) / articulos_por_cliente * factor
    numero = _poisson(azar, esperado)
    clientes = []
    for i in range(numero):
        k = 1 + (azar.random() < 0.35) + (azar.random() < 0.1)
        elegidos = []
        disponibles = list(range(len(pesos)))
        for _ in range(min(k, len(disponibles))):
            total = sum(pesos[j] for j in disponibles)
            r = azar.random() * total
            for j in disponibles:
                r -= pesos[j]
                if r <= 0:
                    break
            disponibles.remove(j)
            cantidad = 1 + int(azar.random() ** 1.6 * escenario.productos[j].cantidad_max)
            elegidos.append((j, min(cantidad, escenario.productos[j].cantidad_max)))
        clientes.append(Cliente(round(azar.random() * (minutos_dia - 1), 3), i + 1, tuple(elegidos)))
    clientes.sort(key=lambda c: c.minuto)
    return clientes


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
                    "precio_venta": p.precio,
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
        clientes = clientes_del_dia(escenario, c.semilla, dia, md, fecha.weekday())
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
        c.minuto = hasta
    if procesados >= limite:
        _reanclar(c)  # el reloj no puede ir más rápido de lo que se procesa
    db.commit()
    return procesados


def _medicamentos(db: Session, c: Corrida) -> list[Medicamento]:
    return [db.get(Medicamento, i) for i in c.parametros["meds"]]


def _atender(db: Session, c: Corrida, cliente: Cliente, dia: int, minuto: float) -> bool:
    meds = _medicamentos(db, c)
    momento = momento_de(c, minuto)
    lineas, faltantes, items = [], [], []
    for indice, cantidad in cliente.compras:
        m = meds[indice]
        vendible = min(cantidad, m.existencia)
        if vendible > 0:
            lineas.append(LineaPedida(m.id, vendible))
            items.append([m.id, vendible])
        if vendible < cantidad:
            faltantes.append((m, cantidad - vendible))
    if lineas:
        venta = inventario.vender(db, lineas, f"sim{c.id}-{dia}-{cliente.numero}", momento, corrida_id=c.id, usuario="Cliente simulado")
        _evento(db, c, minuto, "venta", cantidad=sum(n for _, n in items), texto=f"Cliente {cliente.numero}", items=items, total=venta.total, cliente=cliente.numero)
    for m, falta in faltantes:
        c.faltantes += falta
        _evento(db, c, minuto, "faltante", m.id, falta, texto=f"No alcanzó {m.nombre}: faltaron {falta} {m.unidad}")
    return _revisar_varios(db, c, [meds[indice] for indice, _ in cliente.compras], minuto, momento)


def _revisar(db: Session, c: Corrida, m: Medicamento, minuto: float, momento: datetime) -> bool:
    return _revisar_varios(db, c, [m], minuto, momento)


def _revisar_varios(db: Session, c: Corrida, meds: list[Medicamento], minuto: float, momento: datetime) -> bool:
    evaluaciones, nuevos = pedidos.evaluar_grupo(db, meds, momento)
    for nuevo in nuevos:
        c.causas = list(c.causas or []) + [nuevo.id]
        for linea, m in pedidos.lineas_de(db, nuevo):
            e = evaluaciones[m.id]
            _evento(
                db, c, minuto, "aviso", m.id, linea.cantidad_pedida,
                texto=f"{m.nombre} llegó al punto de reorden: posición {e.posicion} ≤ R {e.plan.R:.1f}. Aviso enviado por Telegram.",
                pedido=nuevo.id,
            )
        _evento(db, c, minuto, "pausa", texto=f"Reloj en pausa de exposición: esperando «Pedido realizado» del pedido #{nuevo.id}.", pedido=nuevo.id)
    return bool(nuevos)


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
    vendido = db.scalar(
        select(func.coalesce(func.sum(Venta.total), 0)).where(Venta.corrida_id == c.id, Venta.fecha >= inicio, Venta.fecha < momento_de(c, minuto))
    )
    _evento(db, c, minuto, "cierre", texto=f"Cierre del día {dia + 1}: {clientes} clientes, ventas Q {vendido:,.2f}. Se actualiza la estimación con el día completo.", dia=dia + 1, clientes=clientes, ventas=float(vendido))
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
                "precio": m.precio_venta,
            }
        )
    eventos = list(
        db.scalars(select(EventoSim).where(EventoSim.corrida_id == c.id, EventoSim.id > desde_evento).order_by(EventoSim.id.desc()).limit(150))
    )
    eventos.reverse()
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
            "ventas": resumen.ventas,
            "costo": resumen.costo_ventas,
            "margen": resumen.margen_bruto,
            "compras": resumen.compras_recibidas,
            "compromisos": resumen.compromisos,
            "por_gestionar": resumen.por_gestionar,
            "numero_ventas": resumen.numero_ventas,
            "faltantes": c.faltantes,
        },
        "pausa": pausa,
        "seleccionado": seleccionado,
        "serie": serie(db, c, seleccionado),
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
    """Existencia en el tiempo (en minutos de farmacia abierta), R e IS de cada día, pedidos y recepciones."""
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
        resumen_ventas = db.execute(
            select(func.coalesce(func.sum(Venta.total), 0), func.count(Venta.id)).where(Venta.corrida_id == c.id)
        ).one()
        numero_pedidos = db.scalar(select(func.count()).select_from(Pedido).where(Pedido.corrida_id == c.id))
        resultado.append(
            {
                "id": c.id,
                "escenario": ESCENARIOS.get(c.escenario, ESCENARIOS["base"]).titulo,
                "semilla": c.semilla,
                "demora": (c.demora_gestion or 0) / 60,
                "dias": c.dia_cerrado + 1,
                "ventas": float(resumen_ventas[0]),
                "pedidos": numero_pedidos,
                "faltantes": c.faltantes,
                "estado": c.estado,
            }
        )
    return resultado
