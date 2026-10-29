from sqlalchemy import func, select

from app import dinero, pedidos, simulacion
from app.db import EventoSim, Medicamento, Movimiento, Pedido, Venta
from tests.ayudas import medicamento


def _hasta_el_aviso(db, c, dias=20):
    for _ in range(dias):
        simulacion.controlar(db, c, "dia")
        if c.causas:
            return db.get(Pedido, c.causas[0])
    raise AssertionError("No hubo aviso")


def _recorrer(db, c, dias, pasos=1):
    """Avanza confirmando cada aviso al instante; `pasos` parte cada día en trozos."""
    md = c.parametros["minutos_dia"]
    pausas = []
    while c.dia_cerrado < dias - 1:
        objetivo = min(c.minuto + md / pasos, dias * md)
        simulacion.avanzar(db, c, objetivo)
        while c.causas:
            pausas.append(round(c.minuto, 3))
            simulacion.confirmar_pedido(db, db.get(Pedido, c.causas[0]), "prueba")
    return pausas


def _huella(db, c):
    total = db.scalar(select(func.coalesce(func.sum(Venta.total), 0)).where(Venta.corrida_id == c.id))
    existencias = [m.existencia for m in db.scalars(select(Medicamento).where(Medicamento.corrida_id == c.id).order_by(Medicamento.id))]
    return round(total, 2), existencias, c.faltantes


def test_misma_semilla_mismos_resultados_sin_importar_el_ritmo(db):
    a = simulacion.crear_corrida(db, "base", 7)
    pausas_a = _recorrer(db, a, 12, pasos=1)
    huella_a = _huella(db, a)
    b = simulacion.crear_corrida(db, "base", 7)
    pausas_b = _recorrer(db, b, 12, pasos=9)
    assert pausas_a == pausas_b and pausas_a
    assert huella_a == _huella(db, b)


def test_arranca_sin_historia_y_sin_plan(db):
    c = simulacion.crear_corrida(db, "base")
    estado = simulacion.estado(db, c)
    assert all(t["R"] is None for t in estado["tarjetas"])
    assert estado["reloj"]["pausa_manual"] is True


def test_pausa_confirmacion_reanudacion_y_recepcion_tras_l(db):
    c = simulacion.crear_corrida(db, "base")
    simulacion.controlar(db, c, "reanudar")
    pedido = _hasta_el_aviso(db, c)
    minuto_aviso = c.minuto
    assert not simulacion.corriendo(c)
    simulacion.avanzar(db, c, c.minuto + 500)
    assert c.minuto == minuto_aviso  # en pausa no se vende
    ok, _ = simulacion.confirmar_pedido(db, pedido, "Expositor")
    assert ok and simulacion.corriendo(c)
    otra_vez, _ = simulacion.confirmar_pedido(db, pedido, "Expositor")
    assert not otra_vez
    linea, _ = pedidos.lineas_de(db, pedido)[0]
    llegada = minuto_aviso + linea.tiempo_entrega * c.parametros["minutos_dia"]
    simulacion.avanzar(db, c, llegada - 1)
    db.refresh(pedido)
    assert pedido.estado == "realizado"
    while c.causas or c.minuto < llegada:
        if c.causas:
            simulacion.confirmar_pedido(db, db.get(Pedido, c.causas[0]), "Expositor")
        simulacion.avanzar(db, c, llegada + 1)
    db.refresh(pedido)
    assert pedido.estado == "recibido"
    recepcion = db.scalar(select(Movimiento).where(Movimiento.pedido_id == pedido.id, Movimiento.tipo == "recepcion"))
    assert abs(simulacion.minuto_de(c, recepcion.fecha) - llegada) < 1


def test_la_simulacion_no_toca_la_operacion(db):
    real = medicamento(db, "Paracetamol", existencia=50)
    c = simulacion.crear_corrida(db, "base")
    _recorrer(db, c, 6)
    db.refresh(real)
    assert real.existencia == 50
    assert dinero.resumen(db).ventas == 0
    assert dinero.resumen(db, c.id).ventas > 0
    assert db.scalar(select(func.count()).select_from(Pedido).where(Pedido.corrida_id.is_(None))) == 0


def test_reiniciar_invalida_los_botones_viejos(db):
    c = simulacion.crear_corrida(db, "base")
    pedido = _hasta_el_aviso(db, c)
    simulacion.crear_corrida(db, "base")
    db.refresh(c)
    assert c.estado == "terminada"
    ok, texto = simulacion.confirmar_pedido(db, pedido, "Expositor")
    assert not ok and "terminó" in texto


def test_varios_avisos_hacen_cola(db):
    c = simulacion.crear_corrida(db, "base")
    pedido = _hasta_el_aviso(db, c)
    c.causas = [pedido.id, 999999]
    db.commit()
    simulacion.confirmar_pedido(db, pedido, "Expositor")
    assert c.causas == [999999] and not simulacion.corriendo(c)


def test_escenario_de_aumento_genera_mas_ventas_del_producto(db):
    escenario = simulacion.ESCENARIOS["aumento"]
    indice = [p.nombre for p in escenario.productos].index("Salbutamol")

    def unidades(e, dia):
        return sum(n for cl in simulacion.clientes_del_dia(e, 2026, dia, 720, dia % 7) for j, n in cl.compras if j == indice)

    antes = sum(unidades(escenario, d) for d in range(9, 16))
    base = sum(unidades(simulacion.ESCENARIOS["base"], d) for d in range(9, 16))
    assert antes > base * 1.5


def test_pantalla_y_api(cliente):
    assert cliente.get("/simulacion").status_code == 200
    assert cliente.post("/simulacion/nueva", data={"escenario": "base", "semilla": "5"}, follow_redirects=False).status_code == 303
    estado = cliente.get("/simulacion/estado").json()
    assert estado["corrida"]["semilla"] == 5 and len(estado["tarjetas"]) == 8
    r = cliente.post("/simulacion/control", data={"accion": "dia"}).json()
    assert r["reloj"]["dia"] == 2
    assert any(e["tipo"] == "cierre" for e in r["eventos"])


def test_los_eventos_quedan_guardados(db):
    c = simulacion.crear_corrida(db, "base")
    simulacion.controlar(db, c, "dia")
    tipos = {e.tipo for e in db.scalars(select(EventoSim).where(EventoSim.corrida_id == c.id))}
    assert {"inicio", "venta", "cierre"} <= tipos
