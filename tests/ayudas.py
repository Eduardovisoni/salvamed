from datetime import timedelta

from app import inventario
from app.consumo import dia_local
from app.db import ConsumoHistorico, Proveedor, ahora


def proveedor(db, nombre="Proveedor A", envio=35.0):
    p = Proveedor(nombre=nombre, costo_envio=envio)
    db.add(p)
    db.flush()
    return p


def medicamento(db, nombre="Ceftriaxona", existencia=100, diarios=(5, 7) * 15, proveedor_id=None, **extra):
    """Un medicamento con 30 días de historia diaria importada (μD = 6, σD ≈ 1.02 por defecto)."""
    datos = {
        "nombre": nombre,
        "presentacion": "1 g",
        "unidad": "vial",
        "costo_compra": 10.0,
        "unidades_paquete": 10,
        "proveedor_id": proveedor_id,
    }
    datos.update(extra)
    m = inventario.dar_de_alta(db, datos, existencia, ahora())
    hoy = dia_local(ahora())
    for i, cantidad in enumerate(diarios):
        db.add(ConsumoHistorico(medicamento_id=m.id, periodo="dia", inicio=hoy - timedelta(days=i + 1), cantidad=cantidad))
    db.commit()
    return m
