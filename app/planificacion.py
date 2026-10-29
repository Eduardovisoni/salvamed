"""Evaluación de cada medicamento: parámetros, plan de la clase 12 y estado.

Separa tres niveles:
- datos de operación (ventas, recepciones, ajustes) que registra el empleado,
- parámetros administrativos (configuración general y excepciones por medicamento),
- resultados que calcula el sistema (μD, σD, D, Q, R, IS, ...).

Cada parámetro lleva su fuente para poder explicar de dónde sale. Si falta un
dato, el plan queda pendiente y se dice qué falta; nunca se inventa un valor.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.consumo import (
    GUATEMALA,
    Aumento,
    Consumo,
    aumento_de,
    consumo_de,
    dia_local,
    dias_de_ventana,
)
from app.db import (
    ABIERTOS,
    REALIZADO,
    Configuracion,
    ConsumoHistorico,
    Medicamento,
    Pedido,
    PedidoLinea,
    PlanDiario,
    Proveedor,
    Venta,
    VentaLinea,
    en_ambito,
)
from app.modelo import ETIQUETAS, Datos, Estado, Plan, calcular_plan, cantidad_por_paquetes, estado_de


def configuracion(db: Session) -> Configuracion:
    cfg = db.get(Configuracion, 1)
    if cfg is None:
        cfg = Configuracion(id=1)
        db.add(cfg)
        db.flush()
    return cfg


@dataclass(frozen=True)
class Parametro:
    valor: float | None
    fuente: str


def _num(valor: float) -> str:
    return f"{valor:,.2f}".rstrip("0").rstrip(".")


def parametros_de(m: Medicamento, proveedor: Proveedor | None, cfg: Configuracion) -> dict[str, Parametro]:
    p = {}
    if m.dias_operacion_anio:
        p["A"] = Parametro(m.dias_operacion_anio, "excepción del medicamento")
    else:
        p["A"] = Parametro(cfg.dias_operacion_anio, "calendario general (supuesto de demostración)")

    if m.nivel_servicio:
        p["p"] = Parametro(m.nivel_servicio, "excepción del medicamento")
    else:
        p["p"] = Parametro(cfg.nivel_servicio, "configuración general")

    if m.tiempo_entrega:
        p["L"] = Parametro(m.tiempo_entrega, "dato del medicamento")
    elif proveedor and proveedor.tiempo_entrega:
        p["L"] = Parametro(proveedor.tiempo_entrega, f"plazo del proveedor {proveedor.nombre}")
    else:
        p["L"] = Parametro(cfg.tiempo_entrega, "plazo general (supuesto: constante)")

    if m.costo_pedido:
        p["S"] = Parametro(m.costo_pedido, "costo de pedido propio del medicamento")
    elif proveedor and proveedor.costo_envio:
        k = max(proveedor.articulos_por_envio or 1, 1)
        fuente = f"envío de {proveedor.nombre} (Q{_num(proveedor.costo_envio)})"
        if k > 1:
            fuente += f" repartido entre {k} artículos por envío"
        p["S"] = Parametro(proveedor.costo_envio / k, fuente)
    else:
        p["S"] = Parametro(cfg.costo_pedido, "costo general por pedido (supuesto: solo envío)")

    if m.costo_mantener:
        p["H"] = Parametro(m.costo_mantener, "costo de mantener documentado")
    elif m.costo_compra:
        tasa = m.tasa_mantenimiento if m.tasa_mantenimiento is not None else cfg.tasa_mantenimiento
        origen = "del medicamento" if m.tasa_mantenimiento is not None else "general, supuesto académico"
        p["H"] = Parametro(
            m.costo_compra * tasa,
            f"costo de compra Q{_num(m.costo_compra)} × tasa anual {_num(tasa * 100)} % ({origen})",
        )
    else:
        p["H"] = Parametro(None, "falta el costo de compra o un H documentado")
    return p


@dataclass
class Evaluacion:
    m: Medicamento
    consumo: Consumo
    parametros: dict[str, Parametro]
    plan: Plan | None
    pendientes: list[str]
    existencia: int
    en_camino: int
    por_gestionar: int
    estado: Estado
    sugerida: int
    pedido_abierto: Pedido | None
    aumento: Aumento | None
    hoy: date
    advertencias: list[str] = field(default_factory=list)
    riesgo_agotamiento: bool = False

    @property
    def posicion(self) -> int:
        return self.existencia + self.en_camino

    @property
    def consumo_en_L(self) -> float | None:
        if not self.consumo.disponible:
            return None
        return self.consumo.mu_d * self.parametros["L"].valor

    @property
    def ajuste_paquete(self) -> float | None:
        if self.plan is None:
            return None
        return self.sugerida - self.plan.Q


def _inicio_del_dia(dia: date) -> datetime:
    return datetime.combine(dia, time(), GUATEMALA)


def _ventas_por_dia(db: Session, ids: list[int], desde: date, momento: datetime, corrida_id):
    consulta = (
        select(VentaLinea.medicamento_id, Venta.fecha, VentaLinea.cantidad)
        .join(Venta, Venta.id == VentaLinea.venta_id)
        .where(
            VentaLinea.medicamento_id.in_(ids),
            Venta.estado == "confirmada",
            en_ambito(Venta.corrida_id, corrida_id),
            Venta.fecha >= _inicio_del_dia(desde),
            Venta.fecha <= momento,
        )
    )
    resultado = defaultdict(lambda: defaultdict(int))
    for medicamento_id, fecha, cantidad in db.execute(consulta):
        resultado[medicamento_id][dia_local(fecha)] += cantidad
    return resultado


def _historico(db: Session, ids: list[int], desde: date):
    diarios = defaultdict(dict)
    semanales = defaultdict(list)
    consulta = (
        select(ConsumoHistorico)
        .where(ConsumoHistorico.medicamento_id.in_(ids))
        .order_by(ConsumoHistorico.inicio)
    )
    for h in db.scalars(consulta):
        if h.periodo == "dia":
            if h.inicio >= desde:
                diarios[h.medicamento_id][h.inicio] = h.cantidad
        else:
            semanales[h.medicamento_id].append(h.cantidad)
    return diarios, semanales


def _lineas_abiertas(db: Session, ids: list[int]):
    consulta = (
        select(PedidoLinea, Pedido)
        .join(Pedido, Pedido.id == PedidoLinea.pedido_id)
        .where(PedidoLinea.medicamento_id.in_(ids), Pedido.estado.in_(ABIERTOS))
        .order_by(Pedido.id)
    )
    resultado = defaultdict(list)
    for linea, pedido in db.execute(consulta):
        resultado[linea.medicamento_id].append((linea, pedido))
    return resultado


def evaluar_varios(db: Session, medicamentos: list[Medicamento], momento: datetime) -> dict[int, Evaluacion]:
    if not medicamentos:
        return {}
    cfg = configuracion(db)
    corrida_id = medicamentos[0].corrida_id
    hoy = dia_local(momento)
    ventana = dias_de_ventana(hoy, cfg.ventana_dias, cfg.dias_semana)
    desde = ventana[0] if ventana else hoy
    ids = [m.id for m in medicamentos]
    ventas = _ventas_por_dia(db, ids, desde, momento, corrida_id)
    diarios, semanales = _historico(db, ids, desde)
    abiertas = _lineas_abiertas(db, ids)
    proveedores = {
        p.id: p
        for p in db.scalars(select(Proveedor).where(Proveedor.id.in_({m.proveedor_id for m in medicamentos if m.proveedor_id})))
    }
    resultado = {}
    for m in medicamentos:
        consumo = consumo_de(
            ventas[m.id], diarios[m.id], semanales[m.id], m.observado_desde, hoy, cfg.ventana_dias, cfg.dias_semana
        )
        resultado[m.id] = _evaluar(m, consumo, proveedores.get(m.proveedor_id), cfg, abiertas[m.id], hoy, momento)
    return resultado


def evaluar(db: Session, m: Medicamento, momento: datetime) -> Evaluacion:
    return evaluar_varios(db, [m], momento)[m.id]


def _evaluar(m, consumo, proveedor, cfg, abiertas, hoy, momento) -> Evaluacion:
    parametros = parametros_de(m, proveedor, cfg)
    pendientes = []
    if not consumo.disponible:
        pendientes.append(consumo.descripcion)
    if parametros["H"].valor is None:
        pendientes.append("Falta el costo de compra para estimar H (o un H documentado)")
    elif parametros["H"].valor <= 0:
        pendientes.append("H debe ser mayor que 0")
    if not parametros["S"].valor or parametros["S"].valor <= 0:
        pendientes.append("S debe ser mayor que 0")

    plan = None
    if not pendientes:
        plan = calcular_plan(
            Datos(
                dias_habiles=parametros["A"].valor,
                mu_d=consumo.mu_d,
                sigma_d=consumo.sigma_d,
                L=parametros["L"].valor,
                S=parametros["S"].valor,
                H=parametros["H"].valor,
                nivel_servicio=parametros["p"].valor,
            )
        )

    en_camino = sum(linea.por_recibir for linea, pedido in abiertas if pedido.estado == REALIZADO)
    por_gestionar = sum(linea.cantidad_pedida for linea, pedido in abiertas if pedido.estado != REALIZADO)
    pedido_abierto = abiertas[-1][1] if abiertas else None
    posicion = m.existencia + en_camino

    if plan is None:
        estado = Estado("pendiente", ETIQUETAS["pendiente"], None, None)
    elif plan.D == 0:
        estado = Estado("bien", "Sin ventas en la ventana", None, None)
    else:
        estado = estado_de(posicion, consumo.mu_d, plan, cfg.dias_aviso)

    sugerida = cantidad_por_paquetes(plan.Q, m.unidades_paquete) if plan else 0
    evaluacion = Evaluacion(
        m=m,
        consumo=consumo,
        parametros=parametros,
        plan=plan,
        pendientes=pendientes,
        existencia=m.existencia,
        en_camino=en_camino,
        por_gestionar=por_gestionar,
        estado=estado,
        sugerida=sugerida,
        pedido_abierto=pedido_abierto,
        aumento=aumento_de(consumo, cfg.aumento_umbral),
        hoy=hoy,
    )
    _advertencias(evaluacion, abiertas, momento)
    return evaluacion


def _advertencias(e: Evaluacion, abiertas, momento: datetime) -> None:
    m, mu = e.m, e.consumo.mu_d
    if e.plan and mu and mu > 0 and e.estado.clave == "pedir":
        espera = e.parametros["L"].valor
        realizados = [p for _, p in abiertas if p.estado == REALIZADO and p.fecha_estimada]
        if realizados:
            espera = max((realizados[0].fecha_estimada - momento).total_seconds() / 86400, 0)
        if m.existencia / mu < espera:
            e.riesgo_agotamiento = True
            e.advertencias.append(
                f"Con el consumo promedio, la existencia alcanza para {m.existencia / mu:.1f} días y "
                f"el pedido tarda unos {espera:.1f} días: hay riesgo de agotarse antes de recibirlo."
            )
    if m.capacidad_maxima and e.sugerida and m.existencia + e.sugerida > m.capacidad_maxima:
        e.advertencias.append(
            f"La existencia más el pedido sugerido ({m.existencia + e.sugerida}) supera la capacidad "
            f"de almacenamiento indicada ({m.capacidad_maxima})."
        )
    if m.vencimiento_proximo and mu and mu > 0:
        dias = (m.vencimiento_proximo - e.hoy).days
        if (m.existencia + e.sugerida) / mu > dias:
            e.advertencias.append(
                f"Con el consumo actual, parte del inventario podría vencer antes de usarse "
                f"(vencimiento próximo {m.vencimiento_proximo:%d/%m/%Y})."
            )


def registrar_plan_del_dia(db: Session, e: Evaluacion) -> None:
    """Guarda el plan vigente hoy; si cambia durante el día se actualiza el del mismo día."""
    existente = db.scalar(
        select(PlanDiario).where(PlanDiario.medicamento_id == e.m.id, PlanDiario.dia == e.hoy)
    )
    if existente is None:
        existente = PlanDiario(corrida_id=e.m.corrida_id, medicamento_id=e.m.id, dia=e.hoy)
        db.add(existente)
    existente.mu_d = e.consumo.mu_d
    existente.sigma_d = e.consumo.sigma_d
    existente.dias_base = e.consumo.dias
    existente.calidad = e.consumo.calidad
    existente.punto_reorden = e.plan.R if e.plan else None
    existente.inventario_seguridad = e.plan.IS if e.plan else None
    existente.cantidad_eoq = e.plan.Q if e.plan else None


def manana(dia: date) -> date:
    return dia + timedelta(days=1)
