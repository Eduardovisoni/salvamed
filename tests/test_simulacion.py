from sqlalchemy import func, select

import pytest

from app import dinero, pedidos, roble, simulacion
from app.db import Despacho, EventoSim, Medicamento, Movimiento, Pedido
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
    total = db.scalar(select(func.coalesce(func.sum(Despacho.costo_total), 0)).where(Despacho.corrida_id == c.id))
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
    assert c.minuto == minuto_aviso  # en pausa no se despacha
    ok, _ = simulacion.confirmar_pedido(db, pedido, "Expositor")
    assert ok and simulacion.corriendo(c)
    otra_vez, _ = simulacion.confirmar_pedido(db, pedido, "Expositor")
    assert not otra_vez
    linea, _ = pedidos.lineas_de(db, pedido)[0]
    md = c.parametros["minutos_dia"]
    llegada = c.parametros["llegadas"][str(pedido.id)]
    assert minuto_aviso + (linea.tiempo_entrega - 1) * md <= llegada <= minuto_aviso + (linea.tiempo_entrega + 1) * md
    db.refresh(pedido)
    assert abs(simulacion.minuto_de(c, pedido.fecha_estimada) - (minuto_aviso + linea.tiempo_entrega * md)) < 1  # lo que dijo el proveedor
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
    assert dinero.resumen(db).consumido == 0
    assert dinero.resumen(db, c.id).consumido > 0
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


def test_escenario_de_aumento_pide_mas_salbutamol(db):
    escenario = simulacion.ESCENARIOS["aumento"]
    indice = [p.nombre for p in escenario.productos].index("Salbutamol")

    def unidades(e, dia):
        return sum(n for sol in simulacion.solicitudes_del_dia(e, 2026, dia, 1440, dia % 7) for j, n in sol.compras if j == indice)

    despues = sum(unidades(escenario, d) for d in range(14, 21))
    base = sum(unidades(simulacion.ESCENARIOS["base"], d) for d in range(14, 21))
    assert despues > base * 1.5
    assert unidades(escenario, 5) == unidades(simulacion.ESCENARIOS["base"], 5)


def test_cada_servicio_pide_en_su_horario(db):
    escenario = simulacion.ESCENARIOS["base"]
    solicitudes = [s for d in range(7) for s in simulacion.solicitudes_del_dia(escenario, 2026, d, 1440, d)]
    cirugias = [s.minuto / 60 for s in solicitudes if s.servicio == "Sala de operaciones"]
    assert cirugias and all(7 <= h < 17 for h in cirugias)
    assert {s.servicio for s in solicitudes} == {x.nombre for x in escenario.servicios}


def test_pantalla_y_api(cliente):
    assert cliente.get("/simulacion").status_code == 200
    assert cliente.post("/simulacion/nueva", data={"escenario": "base", "semilla": "5"}, follow_redirects=False).status_code == 303
    estado = cliente.get("/simulacion/estado").json()
    assert estado["corrida"]["semilla"] == 5 and len(estado["tarjetas"]) == 8
    assert len(estado["comparacion"]["roble"]["filas"]) == 8 and estado["comparacion"]["tarifas"]["recargo"] == 0.2
    assert [s["nombre"] for s in estado["servicios"]][:2] == ["Emergencia", "Encamamiento"]
    r = cliente.post("/simulacion/control", data={"accion": "dia"}).json()
    assert r["reloj"]["dia"] == 2
    assert any(e["tipo"] == "cierre" for e in r["eventos"])


def test_los_eventos_quedan_guardados(db):
    c = simulacion.crear_corrida(db, "base")
    simulacion.controlar(db, c, "dia")
    tipos = {e.tipo for e in db.scalars(select(EventoSim).where(EventoSim.corrida_id == c.id))}
    assert {"inicio", "despacho", "cierre"} <= tipos


# ---------------------------------------------------------------- El Roble hoy contra SALVAMED


def test_plazo_aleatorio_entre_l_menos_1_y_l_mas_1():
    plazos = [roble.plazo_aleatorio(2026, 0, k, 3) for k in range(400)]
    assert all(2 <= x <= 4 for x in plazos)
    assert abs(sum(plazos) / len(plazos) - 3) < 0.1  # en promedio L, el que usa la fórmula
    assert len({round(x, 3) for x in plazos}) > 300  # no es un número fijo
    assert roble.plazo_aleatorio(2026, 0, 5, 3) == roble.plazo_aleatorio(2026, 0, 5, 3)
    assert roble.plazo_aleatorio(2026, 0, 0, 0.5) >= 0


def test_costos_misma_cuenta_para_los_dos_lados():
    t = roble.Tarifas(S=35, tasa=0.25, A=365, recargo=0.2)
    c = roble.costos(10, envios=70, unidades_dia=36.5, emergencia_u=5, t=t)
    assert c == pytest.approx({"pedir": 70, "mantener": 0.25, "emergencia": 10, "total": 80.25})


class _Producto:
    existencia, plazo, consumo, paquete = 10, 3, 4, 5


def test_el_roble_pide_solo_cuando_se_acaba_y_a_ojo():
    md = 1440
    solicitudes = [(100, "Emergencia", ((0, 4),)), (200, "Emergencia", ((0, 4),)), (300, "Emergencia", ((0, 5),)), (400, "Emergencia", ((0, 2),))]
    estantes, sucesos = roble.simular([_Producto()], solicitudes, 500, md, 7)
    e = estantes[0]
    assert e.pedidos == 1 and e.despachado == 10
    assert e.emergencia_u == 5 and e.emergencia_veces == 2  # 3 de la tercera solicitud y 2 de la cuarta
    agotado = [s for s in sucesos if s.tipo == "agotado"][0]
    assert agotado.minuto == 300  # pidió al llegar a 0, no antes
    assert agotado.cantidad == roble.cantidad_a_ojo(7, 0, 0, 4, 5)
    assert 7 * 4 <= agotado.cantidad <= 21 * 4 + 5 and agotado.cantidad % 5 == 0
    assert agotado.llega == pytest.approx(300 + roble.plazo_aleatorio(7, 0, 0, 3) * md)
    # Lo guardado: 10 hasta el minuto 100, 6 hasta el 200, 2 hasta el 300 y luego 0.
    assert e.area == pytest.approx(10 * 100 + 6 * 100 + 2 * 100)
    llegada = agotado.llega + 1
    estantes, _ = roble.simular([_Producto()], solicitudes, llegada, md, 7)
    assert estantes[0].existencia == agotado.cantidad and estantes[0].en_camino == 0


def _comparar(db, dias, semilla=2026, recargo=20):
    c = simulacion.crear_corrida(db, "base", semilla, 0, recargo)
    _recorrer(db, c, dias)
    return c, simulacion.estado(db, c)["comparacion"]


def test_las_cuentas_de_la_comparacion_cuadran(db):
    c, k = _comparar(db, 10)
    assert k["demanda"]["salvamed"] == k["demanda"]["roble"] > 0  # las mismas solicitudes en los dos lados
    t = roble.Tarifas(**k["tarifas"])
    for lado in (k["salvamed"], k["roble"]):
        assert lado["total"] == pytest.approx(lado["pedir"] + lado["mantener"] + lado["emergencia"])
        for clave in ("pedir", "mantener", "emergencia", "total", "emergencia_u"):
            assert lado[clave] == pytest.approx(sum(f[clave] for f in lado["filas"]))
        for f, p in zip(lado["filas"], simulacion.ESCENARIOS["base"].productos):
            assert f["mantener"] == pytest.approx(p.costo * t.tasa / t.A * f["unidades_dia"])
            assert f["emergencia"] == pytest.approx(f["emergencia_u"] * p.costo * t.recargo)
    assert k["roble"]["pedir"] == pytest.approx(k["roble"]["envios"] * t.S)
    realizados = list(db.scalars(select(Pedido).where(Pedido.corrida_id == c.id, Pedido.realizado_en.is_not(None))))
    assert k["salvamed"]["envios"] == len(realizados) > 0
    assert k["salvamed"]["pedir"] == pytest.approx(sum(p.costo_envio for p in realizados))
    assert k["salvamed"]["emergencia_u"] == c.faltantes
    unidades = db.scalar(select(func.sum(Movimiento.cantidad)).where(Movimiento.corrida_id == c.id, Movimiento.tipo == "despacho"))
    assert k["salvamed"]["despachado"] == abs(unidades)
    assert k["ahorro"] == pytest.approx(k["roble"]["total"] - k["salvamed"]["total"])


def test_salvamed_ahorra_porque_evita_emergencias(db):
    _, k = _comparar(db, 20)
    assert k["roble"]["emergencia_u"] > 5 * max(k["salvamed"]["emergencia_u"], 1)
    assert k["ahorro"] > 0


def test_sin_recargo_el_ahorro_viene_solo_de_las_emergencias(db):
    _, con = _comparar(db, 20, recargo=20)
    _, sin = _comparar(db, 20, recargo=0)
    assert sin["roble"]["emergencia"] == sin["salvamed"]["emergencia"] == 0
    assert con["ahorro"] - sin["ahorro"] == pytest.approx(con["roble"]["emergencia"] - con["salvamed"]["emergencia"])


def test_corrida_vieja_sin_comparacion(db):
    c = simulacion.crear_corrida(db, "base")
    c.parametros = {k: v for k, v in c.parametros.items() if k not in ("tarifas", "area", "t_area", "emergencia", "ordenes")}
    db.commit()
    simulacion.controlar(db, c, "dia")
    estado = simulacion.estado(db, c)
    assert estado["comparacion"] is None and estado["serie"]["roble"] == []
