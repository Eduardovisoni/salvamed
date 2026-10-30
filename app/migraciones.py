"""Migraciones del esquema, numeradas y en orden.

La versión 1 era la app de salidas por sala (tablas `medicamentos` y
`movimientos`). La versión 2 crea tablas nuevas con otros nombres y no borra ni
modifica las de la versión 1: quedan en la base como respaldo de solo lectura.
Así un despliegue nunca destruye datos, y la operación nueva arranca sin
movimientos inventados.

Para cambios futuros se agrega una función a PASOS (por ejemplo con
ALTER TABLE ... ADD COLUMN). `create_all` solo crea tablas que no existen; no
agrega columnas a una tabla existente, por eso no basta para migrar.

Antes de desplegar una versión nueva en Railway conviene sacar un respaldo:
    pg_dump "$DATABASE_URL" > respaldo.sql
"""

from sqlalchemy import Column, Integer, MetaData, Table, func, inspect, select, text
from sqlalchemy.orm import Session

_meta = MetaData()
_version = Table("salvamed_esquema", _meta, Column("version", Integer, nullable=False))


def _paso_2(conexion) -> None:
    from app.db import Base, Configuracion

    Base.metadata.create_all(conexion)
    with Session(bind=conexion) as s:
        if s.get(Configuracion, 1) is None:
            s.add(Configuracion(id=1))
            s.flush()


def _paso_3(conexion) -> None:
    """Medicamentos controlados: probabilidad de servicio, días y costo del trámite de autorización."""
    columnas = {c["name"] for c in inspect(conexion).get_columns("configuracion")}
    for nombre, valor in (("nivel_controlados", 99), ("dias_tramite_controlados", 15), ("costo_tramite_controlados", 300)):
        if nombre not in columnas:
            conexion.execute(text(f"ALTER TABLE configuracion ADD COLUMN {nombre} FLOAT DEFAULT {valor}"))
        conexion.execute(text(f"UPDATE configuracion SET {nombre} = {valor} WHERE {nombre} IS NULL"))


PASOS = {2: _paso_2, 3: _paso_3}
VERSION_ACTUAL = max(PASOS)


def version_de(conexion) -> int:
    revisor = inspect(conexion)
    if not revisor.has_table("salvamed_esquema"):
        return 1 if revisor.has_table("medicamentos") else 0
    return conexion.execute(select(func.max(_version.c.version))).scalar() or 0


def migrar(motor) -> int:
    with motor.begin() as conexion:
        actual = version_de(conexion)
        _meta.create_all(conexion)
        for numero in sorted(PASOS):
            if numero > actual:
                PASOS[numero](conexion)
                actual = numero
        conexion.execute(_version.delete())
        conexion.execute(_version.insert().values(version=actual))
    return actual
