"""Catálogo de demostración para el modo operación (solo con una acción explícita).

Usa `datos/SALVAMED en orden.csv`, el archivo del grupo. Solo los nombres de los
medicamentos están contextualizados en el Sanatorio El Roble; existencias, plazos,
totales semanales y costos son simulados. El archivo trae `precio_unitario_q` sin
decir qué es: aquí se toma como costo de compra simulado, porque el archivo lo usa
para valorar el inventario y presupuestar pedidos. Es un supuesto de demostración.
"""

from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.consumo import GUATEMALA, dia_local
from app.db import (
    RECIBIDO, AccionTelegram, ConsumoHistorico, Despacho, DespachoLinea, Importacion, Medicamento, Mensaje, Movimiento,
    Pedido, PedidoLinea, PlanDiario, Proveedor,
)
from app.importacion import Opciones, aplicar, preparar

ARCHIVO = Path(__file__).resolve().parent.parent / "datos" / "SALVAMED en orden.csv"


def cargar_demo(db: Session) -> dict:
    opciones = Opciones(columna_ambigua={"precio_unitario_q": "costo_compra"})
    importacion = preparar(db, "SALVAMED en orden.csv (demostración)", ARCHIVO.read_bytes(), opciones)
    return aplicar(db, importacion)


def borrar_datos(db: Session) -> dict:
    """Deja la operación vacía para volver a cargar o importar desde cero.

    Borra medicamentos, existencias, despachos, pedidos, proveedores, historia importada y
    avisos de la operación. No toca la configuración general ni las simulaciones (corrida_id).
    Los hijos se borran a mano antes que los padres porque SQLite no aplica los ON DELETE.
    """
    medicamentos = select(Medicamento.id).where(Medicamento.corrida_id.is_(None))
    pedidos = select(Pedido.id).where(Pedido.corrida_id.is_(None))
    despachos = select(Despacho.id).where(Despacho.corrida_id.is_(None))
    mensajes = select(Mensaje.id).where(Mensaje.corrida_id.is_(None))
    cuenta = {
        "medicamentos": db.scalar(select(func.count()).select_from(medicamentos.subquery())),
        "despachos": db.scalar(select(func.count()).select_from(despachos.subquery())),
        "pedidos": db.scalar(select(func.count()).select_from(pedidos.subquery())),
    }
    db.execute(delete(AccionTelegram).where(AccionTelegram.mensaje_id.in_(mensajes)))
    db.execute(delete(Mensaje).where(Mensaje.corrida_id.is_(None)))
    db.execute(delete(PedidoLinea).where(PedidoLinea.pedido_id.in_(pedidos)))
    db.execute(delete(Pedido).where(Pedido.corrida_id.is_(None)))
    db.execute(delete(DespachoLinea).where(DespachoLinea.despacho_id.in_(despachos)))
    db.execute(delete(Despacho).where(Despacho.corrida_id.is_(None)))
    db.execute(delete(Movimiento).where(Movimiento.corrida_id.is_(None)))
    db.execute(delete(PlanDiario).where(PlanDiario.corrida_id.is_(None)))
    db.execute(delete(ConsumoHistorico).where(ConsumoHistorico.medicamento_id.in_(medicamentos)))
    db.execute(delete(Medicamento).where(Medicamento.corrida_id.is_(None)))
    db.execute(delete(Proveedor).where(Proveedor.corrida_id.is_(None)))
    db.execute(delete(Importacion))
    db.commit()
    return cuenta


# ---------------------------------------------------------------- Historial simulado


def simular_historial(db: Session, momento: datetime, dias: int = 30) -> dict:
    """Inventa los últimos `dias` de movimientos para los medicamentos que aún no tienen historial.

    Para cada medicamento sin movimientos anteriores a hoy: despachos diarios con un consumo
    parecido al que ya se le estima (o uno plausible según su existencia), un pedido recibido a
    media ventana y una apertura al inicio. Se construye hacia atrás desde la existencia actual,
    así la existencia de hoy no cambia y nunca queda negativa. Los medicamentos que ya tienen
    historial real no se tocan.
    """
    from random import Random

    from app.consumo import abre
    from app.db import APERTURA, DESPACHO, RECEPCION
    from app.planificacion import configuracion

    cfg = configuracion(db)
    hoy = dia_local(momento)
    inicio_hoy = datetime.combine(hoy, time(), GUATEMALA)
    primer_dia = hoy - timedelta(days=dias)
    medicamentos = db.scalars(
        select(Medicamento).where(Medicamento.corrida_id.is_(None), Medicamento.activo).order_by(Medicamento.id)
    ).all()

    despachos_por_dia: dict[date, Despacho] = {}

    def despacho_del(dia: date) -> Despacho:
        if dia not in despachos_por_dia:
            fecha = datetime.combine(dia, time(10, 0), GUATEMALA)
            token = f"historial-simulado-{dia.isoformat()}"
            existente = db.scalar(select(Despacho).where(Despacho.token == token))
            if existente is None:
                existente = Despacho(token=token, servicio="Historial simulado", fecha=fecha, nota="Generado para la demostración")
                db.add(existente)
                db.flush()
            despachos_por_dia[dia] = existente
        return despachos_por_dia[dia]

    simulados = 0
    omitidos = 0
    for m in medicamentos:
        movimientos = db.scalars(
            select(Movimiento).where(Movimiento.medicamento_id == m.id).order_by(Movimiento.fecha, Movimiento.id)
        ).all()
        if movimientos and movimientos[0].fecha < inicio_hoy:
            omitidos += 1
            continue
        # Existencia con la que amaneció hoy: lo que había antes del primer movimiento de hoy.
        if not movimientos:
            existencia_hoy = m.existencia
        elif movimientos[0].tipo == APERTURA:
            existencia_hoy = movimientos[0].existencia_despues
            db.delete(movimientos[0])  # la apertura pasa a ser hace `dias` días
        else:
            existencia_hoy = movimientos[0].existencia_despues - movimientos[0].cantidad
        if existencia_hoy <= 0 and not movimientos:
            omitidos += 1
            continue

        azar = Random(m.id * 7919 + dias)
        plan = db.scalar(
            select(PlanDiario).where(PlanDiario.medicamento_id == m.id, PlanDiario.mu_d.is_not(None)).order_by(PlanDiario.dia.desc())
        )
        semanas = [h.cantidad for h in db.scalars(select(ConsumoHistorico).where(ConsumoHistorico.medicamento_id == m.id, ConsumoHistorico.periodo == "semana"))]
        if plan is not None and plan.mu_d and plan.mu_d > 0:
            mu = plan.mu_d
        elif semanas:
            mu = sum(semanas) / len(semanas) / 7
        else:
            mu = max(existencia_hoy / 18, 0.6)
        sigma = mu * 0.35

        dias_ventana = [primer_dia + timedelta(days=i) for i in range(dias)]
        consumo = {}
        for dia in dias_ventana:
            if not abre(dia, cfg.dias_semana):
                continue
            cantidad = max(0, round(azar.gauss(mu, sigma)))
            if cantidad > 0:
                consumo[dia] = cantidad
        dia_recepcion = hoy - timedelta(days=azar.randint(7, 14))
        plazo = int(m.tiempo_entrega) if m.tiempo_entrega and m.tiempo_entrega > 0 else 3
        paquete = max(m.unidades_paquete or 1, 1)
        recibido = max(paquete, round(mu * 14 / paquete) * paquete)

        # Hacia atrás desde hoy: así la existencia de hoy se respeta y nunca baja de cero.
        actual = existencia_hoy
        eventos = []  # (fecha, tipo, cantidad, existencia_despues, extra)
        for dia in reversed(dias_ventana):
            if dia == dia_recepcion:
                recibido = min(recibido, actual)
                if recibido > 0:
                    eventos.append((datetime.combine(dia, time(15, 0), GUATEMALA), RECEPCION, recibido, actual))
                    actual -= recibido
            if dia in consumo:
                eventos.append((datetime.combine(dia, time(10, 0), GUATEMALA), DESPACHO, -consumo[dia], actual))
                actual += consumo[dia]
        apertura = actual
        costo = m.costo_promedio if m.costo_promedio is not None else m.costo_compra

        db.add(Movimiento(
            medicamento_id=m.id, tipo=APERTURA, cantidad=apertura, existencia_despues=apertura,
            costo_unitario=costo, nota="Apertura (historial simulado)",
            fecha=datetime.combine(primer_dia, time(8, 0), GUATEMALA),
        ))
        pedido = None
        for fecha, tipo, cantidad, despues in reversed(eventos):
            if tipo == DESPACHO:
                despacho = despacho_del(dia_local(fecha))
                subtotal = round((costo or 0) * -cantidad, 2)
                db.add(DespachoLinea(despacho_id=despacho.id, medicamento_id=m.id, cantidad=-cantidad, costo_unitario=costo, costo=subtotal))
                despacho.costo_total = round(despacho.costo_total + subtotal, 2)
                db.add(Movimiento(
                    medicamento_id=m.id, tipo=DESPACHO, cantidad=cantidad, existencia_despues=despues,
                    costo_unitario=costo, despacho_id=despacho.id, nota=despacho.servicio, fecha=fecha,
                ))
            else:
                realizado = fecha - timedelta(days=plazo)
                pedido = Pedido(
                    proveedor_id=m.proveedor_id, estado=RECIBIDO, origen="simulado",
                    creado=realizado - timedelta(hours=1), realizado_en=realizado, confirmado_por="Historial simulado",
                    fecha_estimada=fecha, recibido_en=fecha, nota="Pedido del historial simulado",
                )
                db.add(pedido)
                db.flush()
                db.add(PedidoLinea(
                    pedido_id=pedido.id, medicamento_id=m.id, cantidad_sugerida=cantidad,
                    cantidad_pedida=cantidad, cantidad_recibida=cantidad, costo_unitario=costo,
                ))
                db.add(Movimiento(
                    medicamento_id=m.id, tipo=RECEPCION, cantidad=cantidad, existencia_despues=despues,
                    costo_unitario=costo, pedido_id=pedido.id, nota=f"Pedido #{pedido.id}", fecha=fecha,
                ))
        if m.observado_desde is None or m.observado_desde > primer_dia:
            m.observado_desde = primer_dia
        simulados += 1

    db.commit()
    return {"simulados": simulados, "omitidos": omitidos, "dias": dias}
