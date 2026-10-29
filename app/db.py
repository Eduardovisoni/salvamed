import os
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.consumo import Consumo
from app.modelo import Datos


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
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


motor = _crear_motor()


def ahora() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Medicamento(Base):
    __tablename__ = "medicamentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120), unique=True)
    unidad: Mapped[str] = mapped_column(String(40))
    existencia: Mapped[int] = mapped_column(Integer)
    consumo_promedio: Mapped[float] = mapped_column(Float)
    desviacion: Mapped[float] = mapped_column(Float)
    tiempo_entrega: Mapped[float] = mapped_column(Float)
    costo_pedido: Mapped[float] = mapped_column(Float)
    costo_mantener: Mapped[float] = mapped_column(Float)
    nivel_servicio: Mapped[float] = mapped_column(Float)
    dias_habiles: Mapped[int] = mapped_column(Integer, default=365)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)
    actualizado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)

    def datos(self, consumo: Consumo) -> Datos:
        return Datos(
            dias_habiles=self.dias_habiles,
            mu_d=consumo.mu_d,
            sigma_d=consumo.sigma_d,
            L=self.tiempo_entrega,
            S=self.costo_pedido,
            H=self.costo_mantener,
            nivel_servicio=self.nivel_servicio,
        )


class Movimiento(Base):
    __tablename__ = "movimientos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    medicamento_id: Mapped[int] = mapped_column(
        ForeignKey("medicamentos.id", ondelete="CASCADE"), index=True
    )
    tipo: Mapped[str] = mapped_column(String(10))
    cantidad: Mapped[int] = mapped_column(Integer)
    nota: Mapped[str] = mapped_column(String(120), default="")
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora, index=True)


SALIDA = "salida"
ENTRADA = "entrada"


def preparar_base() -> None:
    from app.datos_iniciales import MEDICAMENTOS, salidas_simuladas

    Base.metadata.create_all(motor)
    with Session(motor) as sesion:
        if sesion.scalar(select(func.count()).select_from(Medicamento)):
            return
        momento = ahora()
        for indice, datos in enumerate(MEDICAMENTOS):
            salidas, creado = salidas_simuladas(datos, indice, momento)
            medicamento = Medicamento(**datos, creado=creado, actualizado=momento)
            sesion.add(medicamento)
            sesion.flush()
            sesion.add_all(
                Movimiento(
                    medicamento_id=medicamento.id,
                    tipo=SALIDA,
                    cantidad=cantidad,
                    nota="Consumo del día (simulado)",
                    fecha=fecha,
                )
                for fecha, cantidad in salidas
            )
        sesion.commit()


def sesion():
    with Session(motor) as s:
        yield s
