"""Tablas de SALVAMED.

SALVAMED lleva el inventario de medicamentos del Sanatorio El Roble: los medicamentos salen
por despachos a los servicios (Emergencia, Encamamiento, ...), sin datos de pacientes.

Operación y simulación comparten las mismas tablas y las mismas reglas: lo que
distingue a la simulación es `corrida_id`. En operación siempre es NULL, así que
ninguna corrida puede tocar el inventario, los costos ni los pedidos reales.

Las tablas de la versión 1 (`medicamentos` y `movimientos`) no se tocan: quedan
como respaldo en la misma base. Ver app/migraciones.py.
"""

import os
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    TypeDecorator,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


def url_de_la_base() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return "sqlite:///./salvamed.db"
    for prefijo in ("postgres://", "postgresql://"):
        if url.startswith(prefijo):
            return "postgresql+psycopg://" + url[len(prefijo) :]
    return url


def _crear_motor():
    url = url_de_la_base()
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
    return create_engine(url, pool_pre_ping=True)


motor = _crear_motor()


def ahora() -> datetime:
    return datetime.now(timezone.utc)


class FechaUTC(TypeDecorator):
    """Fecha y hora siempre en UTC y con zona horaria (SQLite las devuelve sin zona)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, valor, dialect):
        if valor is None:
            return None
        if valor.tzinfo is None:
            return valor.replace(tzinfo=timezone.utc)
        return valor.astimezone(timezone.utc)

    def process_result_value(self, valor, dialect):
        if valor is None:
            return None
        if valor.tzinfo is None:
            return valor.replace(tzinfo=timezone.utc)
        return valor.astimezone(timezone.utc)


def en_ambito(columna, corrida_id: int | None):
    """Filtro de modo: NULL es operación; un número es una corrida de simulación."""
    return columna.is_(None) if corrida_id is None else columna == corrida_id


class Base(DeclarativeBase):
    pass


# Servicios publicados por el sanatorio (ficha de TuSalud y página de Facebook, octubre de 2026).
SERVICIOS_INICIALES = "\n".join(
    ("Emergencia", "Encamamiento", "Cuidados Intensivos", "Sala de operaciones", "Traumatología", "Ambulancia")
)


def lista_de_servicios(texto: str) -> list[str]:
    return [s.strip() for s in (texto or "").splitlines() if s.strip()]


class Configuracion(Base):
    """Parámetros administrativos generales. Siempre hay una sola fila (id = 1)."""

    __tablename__ = "configuracion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dias_operacion_anio: Mapped[int] = mapped_column(Integer, default=365)
    dias_semana: Mapped[str] = mapped_column(String(7), default="1111111")
    hora_apertura: Mapped[int] = mapped_column(Integer, default=0)
    hora_cierre: Mapped[int] = mapped_column(Integer, default=24)
    servicios: Mapped[str] = mapped_column(String(600), default=SERVICIOS_INICIALES)
    nivel_servicio: Mapped[float] = mapped_column(Float, default=95)
    tasa_mantenimiento: Mapped[float] = mapped_column(Float, default=0.25)
    costo_pedido: Mapped[float] = mapped_column(Float, default=150)
    tiempo_entrega: Mapped[float] = mapped_column(Float, default=3)
    ventana_dias: Mapped[int] = mapped_column(Integer, default=30)
    dias_aviso: Mapped[int] = mapped_column(Integer, default=7)
    aumento_umbral: Mapped[float] = mapped_column(Float, default=1.5)
    # Medicamentos controlados (estupefacientes y psicotrópicos): cada compra necesita autorización del MSPAS.
    nivel_controlados: Mapped[float] = mapped_column(Float, default=99)
    dias_tramite_controlados: Mapped[float] = mapped_column(Float, default=15)
    costo_tramite_controlados: Mapped[float] = mapped_column(Float, default=300)
    actualizado: Mapped[datetime] = mapped_column(FechaUTC, default=ahora)


class AjusteInterno(Base):
    """Valores internos sueltos (por ejemplo, el offset de Telegram)."""

    __tablename__ = "ajustes_internos"

    clave: Mapped[str] = mapped_column(String(80), primary_key=True)
    valor: Mapped[str] = mapped_column(String(400), default="")


class Proveedor(Base):
    __tablename__ = "proveedores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    corrida_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    nombre: Mapped[str] = mapped_column(String(120))
    tiempo_entrega: Mapped[float | None] = mapped_column(Float, nullable=True)
    costo_envio: Mapped[float | None] = mapped_column(Float, nullable=True)
    articulos_por_envio: Mapped[int] = mapped_column(Integer, default=1)
    contacto: Mapped[str] = mapped_column(String(160), default="")


class Medicamento(Base):
    __tablename__ = "catalogo_medicamentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    corrida_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    nombre: Mapped[str] = mapped_column(String(120))
    presentacion: Mapped[str] = mapped_column(String(80), default="")
    unidad: Mapped[str] = mapped_column(String(40))
    sku: Mapped[str] = mapped_column(String(40), default="")
    clasificacion: Mapped[str] = mapped_column(String(40), default="")
    proveedor_id: Mapped[int | None] = mapped_column(
        ForeignKey("proveedores.id", ondelete="SET NULL"), nullable=True
    )
    costo_compra: Mapped[float | None] = mapped_column(Float, nullable=True)
    unidades_paquete: Mapped[int] = mapped_column(Integer, default=1)
    existencia: Mapped[int] = mapped_column(Integer, default=0)
    costo_promedio: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Excepciones a la configuración general (NULL = usar la general).
    nivel_servicio: Mapped[float | None] = mapped_column(Float, nullable=True)
    tasa_mantenimiento: Mapped[float | None] = mapped_column(Float, nullable=True)
    costo_mantener: Mapped[float | None] = mapped_column(Float, nullable=True)
    costo_pedido: Mapped[float | None] = mapped_column(Float, nullable=True)
    tiempo_entrega: Mapped[float | None] = mapped_column(Float, nullable=True)
    dias_operacion_anio: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capacidad_maxima: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vencimiento_proximo: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Desde qué día cuentan como observados los despachos registrados.
    observado_desde: Mapped[date | None] = mapped_column(Date, nullable=True)
    origen: Mapped[str] = mapped_column(String(20), default="registro")
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    ultimo_aviso_aumento: Mapped[date | None] = mapped_column(Date, nullable=True)
    creado: Mapped[datetime] = mapped_column(FechaUTC, default=ahora)
    actualizado: Mapped[datetime] = mapped_column(FechaUTC, default=ahora)

    @property
    def nombre_completo(self) -> str:
        return f"{self.nombre} {self.presentacion}".strip()


# Tipos de movimiento. La cantidad lleva signo: positiva entra, negativa sale.
APERTURA = "apertura"
DESPACHO = "despacho"
RECEPCION = "recepcion"
AJUSTE = "ajuste"
ANULACION = "anulacion"


class Movimiento(Base):
    __tablename__ = "movimientos_inventario"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    corrida_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    medicamento_id: Mapped[int] = mapped_column(
        ForeignKey("catalogo_medicamentos.id", ondelete="CASCADE"), index=True
    )
    tipo: Mapped[str] = mapped_column(String(12))
    cantidad: Mapped[int] = mapped_column(Integer)
    existencia_despues: Mapped[int] = mapped_column(Integer)
    costo_unitario: Mapped[float | None] = mapped_column(Float, nullable=True)
    despacho_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    pedido_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    referencia_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nota: Mapped[str] = mapped_column(String(200), default="")
    usuario: Mapped[str] = mapped_column(String(80), default="")
    fecha: Mapped[datetime] = mapped_column(FechaUTC, default=ahora, index=True)


class Despacho(Base):
    """Entrega de medicamentos a un servicio del sanatorio. No guarda datos de pacientes."""

    __tablename__ = "despachos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    corrida_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    token: Mapped[str] = mapped_column(String(64), unique=True)
    servicio: Mapped[str] = mapped_column(String(60), index=True)
    fecha: Mapped[datetime] = mapped_column(FechaUTC, default=ahora, index=True)
    costo_total: Mapped[float] = mapped_column(Float, default=0)
    estado: Mapped[str] = mapped_column(String(12), default="confirmado")
    nota: Mapped[str] = mapped_column(String(200), default="")
    anulado_en: Mapped[datetime | None] = mapped_column(FechaUTC, nullable=True)
    motivo_anulacion: Mapped[str] = mapped_column(String(200), default="")


class DespachoLinea(Base):
    __tablename__ = "despacho_lineas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    despacho_id: Mapped[int] = mapped_column(ForeignKey("despachos.id", ondelete="CASCADE"), index=True)
    medicamento_id: Mapped[int] = mapped_column(
        ForeignKey("catalogo_medicamentos.id", ondelete="CASCADE"), index=True
    )
    cantidad: Mapped[int] = mapped_column(Integer)
    costo_unitario: Mapped[float | None] = mapped_column(Float, nullable=True)
    costo: Mapped[float] = mapped_column(Float, default=0)


# Estados de un pedido.
PENDIENTE = "pendiente"
REALIZADO = "realizado"
RECIBIDO = "recibido"
CANCELADO = "cancelado"
ABIERTOS = (PENDIENTE, REALIZADO)


class Pedido(Base):
    __tablename__ = "pedidos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    corrida_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    proveedor_id: Mapped[int | None] = mapped_column(
        ForeignKey("proveedores.id", ondelete="SET NULL"), nullable=True
    )
    estado: Mapped[str] = mapped_column(String(12), default=PENDIENTE, index=True)
    origen: Mapped[str] = mapped_column(String(12), default="automatico")
    creado: Mapped[datetime] = mapped_column(FechaUTC, default=ahora)
    realizado_en: Mapped[datetime | None] = mapped_column(FechaUTC, nullable=True)
    confirmado_por: Mapped[str] = mapped_column(String(120), default="")
    fecha_estimada: Mapped[datetime | None] = mapped_column(FechaUTC, nullable=True)
    recibido_en: Mapped[datetime | None] = mapped_column(FechaUTC, nullable=True)
    cancelado_en: Mapped[datetime | None] = mapped_column(FechaUTC, nullable=True)
    costo_envio: Mapped[float] = mapped_column(Float, default=0)
    nota: Mapped[str] = mapped_column(String(200), default="")
    aviso_insuficiente: Mapped[bool] = mapped_column(Boolean, default=False)
    aviso_atraso: Mapped[bool] = mapped_column(Boolean, default=False)


class PedidoLinea(Base):
    __tablename__ = "pedido_lineas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pedido_id: Mapped[int] = mapped_column(ForeignKey("pedidos.id", ondelete="CASCADE"), index=True)
    medicamento_id: Mapped[int] = mapped_column(
        ForeignKey("catalogo_medicamentos.id", ondelete="CASCADE"), index=True
    )
    cantidad_sugerida: Mapped[int] = mapped_column(Integer, default=0)
    cantidad_eoq: Mapped[float | None] = mapped_column(Float, nullable=True)
    cantidad_pedida: Mapped[int] = mapped_column(Integer, default=0)
    cantidad_recibida: Mapped[int] = mapped_column(Integer, default=0)
    costo_unitario: Mapped[float | None] = mapped_column(Float, nullable=True)
    existencia_aviso: Mapped[int | None] = mapped_column(Integer, nullable=True)
    posicion_aviso: Mapped[int | None] = mapped_column(Integer, nullable=True)
    punto_reorden: Mapped[float | None] = mapped_column(Float, nullable=True)
    inventario_seguridad: Mapped[float | None] = mapped_column(Float, nullable=True)
    mu_d: Mapped[float | None] = mapped_column(Float, nullable=True)
    sigma_d: Mapped[float | None] = mapped_column(Float, nullable=True)
    tiempo_entrega: Mapped[float | None] = mapped_column(Float, nullable=True)

    @property
    def por_recibir(self) -> int:
        return max(self.cantidad_pedida - self.cantidad_recibida, 0)


class Mensaje(Base):
    """Bandeja de salida de Telegram: cada aviso queda guardado aunque falle el envío."""

    __tablename__ = "mensajes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    corrida_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    pedido_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    medicamento_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tipo: Mapped[str] = mapped_column(String(20))
    chat_id: Mapped[str] = mapped_column(String(40), default="")
    texto: Mapped[str] = mapped_column(String(4000))
    con_botones: Mapped[bool] = mapped_column(Boolean, default=False)
    estado: Mapped[str] = mapped_column(String(12), default="pendiente", index=True)
    intentos: Mapped[int] = mapped_column(Integer, default=0)
    ultimo_error: Mapped[str] = mapped_column(String(400), default="")
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    creado: Mapped[datetime] = mapped_column(FechaUTC, default=ahora)
    enviado_en: Mapped[datetime | None] = mapped_column(FechaUTC, nullable=True)
    proximo_intento: Mapped[datetime | None] = mapped_column(FechaUTC, nullable=True)


class AccionTelegram(Base):
    """Cada pulsación de botón se registra una sola vez (evita efectos duplicados)."""

    __tablename__ = "telegram_acciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    callback_id: Mapped[str] = mapped_column(String(80), unique=True)
    mensaje_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chat_id: Mapped[str] = mapped_column(String(40), default="")
    usuario: Mapped[str] = mapped_column(String(120), default="")
    datos: Mapped[str] = mapped_column(String(80), default="")
    resultado: Mapped[str] = mapped_column(String(200), default="")
    fecha: Mapped[datetime] = mapped_column(FechaUTC, default=ahora)


class PlanDiario(Base):
    """El plan vigente cada día, para conservar los umbrales históricos en las gráficas."""

    __tablename__ = "planes_diarios"
    __table_args__ = (UniqueConstraint("medicamento_id", "dia"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    corrida_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    medicamento_id: Mapped[int] = mapped_column(
        ForeignKey("catalogo_medicamentos.id", ondelete="CASCADE"), index=True
    )
    dia: Mapped[date] = mapped_column(Date)
    mu_d: Mapped[float | None] = mapped_column(Float, nullable=True)
    sigma_d: Mapped[float | None] = mapped_column(Float, nullable=True)
    dias_base: Mapped[int] = mapped_column(Integer, default=0)
    calidad: Mapped[str] = mapped_column(String(20), default="")
    punto_reorden: Mapped[float | None] = mapped_column(Float, nullable=True)
    inventario_seguridad: Mapped[float | None] = mapped_column(Float, nullable=True)
    cantidad_eoq: Mapped[float | None] = mapped_column(Float, nullable=True)


class ConsumoHistorico(Base):
    """Historia importada. No mueve existencias: solo alimenta μD y σD."""

    __tablename__ = "consumo_historico"
    __table_args__ = (UniqueConstraint("medicamento_id", "periodo", "inicio"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    medicamento_id: Mapped[int] = mapped_column(
        ForeignKey("catalogo_medicamentos.id", ondelete="CASCADE"), index=True
    )
    periodo: Mapped[str] = mapped_column(String(8))  # "dia" o "semana"
    inicio: Mapped[date] = mapped_column(Date)
    cantidad: Mapped[int] = mapped_column(Integer)
    generado: Mapped[bool] = mapped_column(Boolean, default=False)
    procedencia: Mapped[str] = mapped_column(String(200), default="")
    importacion_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Importacion(Base):
    __tablename__ = "importaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    archivo: Mapped[str] = mapped_column(String(200))
    estado: Mapped[str] = mapped_column(String(14), default="vista_previa")
    datos: Mapped[dict] = mapped_column(JSON, default=dict)
    resumen: Mapped[dict] = mapped_column(JSON, default=dict)
    creado: Mapped[datetime] = mapped_column(FechaUTC, default=ahora)
    aplicado_en: Mapped[datetime | None] = mapped_column(FechaUTC, nullable=True)


class Corrida(Base):
    """Una corrida de simulación. El reloj virtual se mide en minutos de jornada."""

    __tablename__ = "corridas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    escenario: Mapped[str] = mapped_column(String(40))
    semilla: Mapped[int] = mapped_column(Integer)
    estado: Mapped[str] = mapped_column(String(12), default="activa", index=True)
    creada: Mapped[datetime] = mapped_column(FechaUTC, default=ahora)
    minuto: Mapped[float] = mapped_column(Float, default=0)
    evento_siguiente: Mapped[int] = mapped_column(Integer, default=0)
    dia_cerrado: Mapped[int] = mapped_column(Integer, default=-1)
    velocidad: Mapped[int] = mapped_column(Integer, default=1)
    pausa_manual: Mapped[bool] = mapped_column(Boolean, default=True)
    causas: Mapped[list] = mapped_column(JSON, default=list)
    real_ancla: Mapped[datetime | None] = mapped_column(FechaUTC, nullable=True)
    minuto_ancla: Mapped[float] = mapped_column(Float, default=0)
    demora_gestion: Mapped[float] = mapped_column(Float, default=0)
    faltantes: Mapped[int] = mapped_column(Integer, default=0)
    parametros: Mapped[dict] = mapped_column(JSON, default=dict)


class EventoSim(Base):
    __tablename__ = "eventos_sim"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    corrida_id: Mapped[int] = mapped_column(Integer, index=True)
    minuto: Mapped[float] = mapped_column(Float)
    tipo: Mapped[str] = mapped_column(String(14))
    medicamento_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cantidad: Mapped[int | None] = mapped_column(Integer, nullable=True)
    texto: Mapped[str] = mapped_column(String(300), default="")
    datos: Mapped[dict] = mapped_column(JSON, default=dict)


def preparar_base() -> None:
    from app.migraciones import migrar

    migrar(motor)


def sesion():
    with Session(motor) as s:
        yield s
