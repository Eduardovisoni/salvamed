import pytest
from sqlalchemy import func, select

from app import dinero, inventario, pedidos
from app.db import Despacho, Mensaje, Movimiento, Pedido, PedidoLinea, ahora
from app.inventario import ErrorDeOperacion, LineaPedida
from app.planificacion import configuracion, evaluar
from tests.ayudas import medicamento, proveedor


def test_alta_sin_costos_queda_pendiente_y_registra_la_apertura(db):
    m = inventario.dar_de_alta(db, {"nombre": "Nuevo", "presentacion": "", "unidad": "caja"}, 12, ahora())
    db.commit()
    e = evaluar(db, m, ahora())
    assert e.plan is None
    assert e.estado.clave == "pendiente"
    assert any("H" in p for p in e.pendientes)
    apertura = db.scalar(select(Movimiento).where(Movimiento.medicamento_id == m.id))
    assert (apertura.tipo, apertura.cantidad, apertura.existencia_despues) == ("apertura", 12, 12)


def test_el_plan_usa_la_configuracion_y_dice_de_donde_sale(db):
    m = medicamento(db)
    e = evaluar(db, m, ahora())
    assert e.consumo.mu_d == pytest.approx(6)
    assert e.parametros["H"].valor == pytest.approx(2.5)
    assert "tasa" in e.parametros["H"].fuente
    assert e.parametros["S"].valor == 150 and e.parametros["L"].valor == 3
    assert e.plan.D == pytest.approx(6 * 365)
    assert e.sugerida % 10 == 0 and e.sugerida >= e.plan.Q


def test_despacho_confirma_todo_o_nada(db):
    a = medicamento(db, "A", existencia=10)
    b = medicamento(db, "B", existencia=2)
    with pytest.raises(ErrorDeOperacion):
        inventario.despachar(db, "Emergencia", [LineaPedida(a.id, 3), LineaPedida(b.id, 5)], "t1", ahora())
    db.refresh(a), db.refresh(b)
    assert (a.existencia, b.existencia) == (10, 2)
    assert db.scalar(select(func.count()).select_from(Despacho)) == 0


def test_despacho_exige_servicio(db):
    m = medicamento(db, existencia=10)
    with pytest.raises(ErrorDeOperacion):
        inventario.despachar(db, "  ", [LineaPedida(m.id, 1)], "t", ahora())


def test_la_misma_lista_no_se_descuenta_dos_veces(db):
    m = medicamento(db, existencia=10)
    d1 = inventario.despachar(db, "Encamamiento", [LineaPedida(m.id, 3)], "mismo", ahora())
    d2 = inventario.despachar(db, "Encamamiento", [LineaPedida(m.id, 3)], "mismo", ahora())
    db.refresh(m)
    assert d1.id == d2.id
    assert m.existencia == 7
    assert d1.costo_total == 30 and d1.servicio == "Encamamiento"


def test_editar_el_catalogo_no_cambia_despachos_pasados(db):
    m = medicamento(db, existencia=10)
    registro = inventario.despachar(db, "Emergencia", [LineaPedida(m.id, 2)], "v", ahora())
    m.costo_compra = 50
    m.costo_promedio = 50
    db.commit()
    db.refresh(registro)
    assert registro.costo_total == 20
    r = dinero.resumen(db)
    assert r.consumido == 20 and r.unidades == 2 and r.por_servicio == (("Emergencia", 20.0, 1),)


def test_costo_por_servicio(db):
    m = medicamento(db, existencia=20)
    inventario.despachar(db, "Emergencia", [LineaPedida(m.id, 3)], "a", ahora())
    inventario.despachar(db, "Encamamiento", [LineaPedida(m.id, 1)], "b", ahora())
    inventario.despachar(db, "Emergencia", [LineaPedida(m.id, 1)], "c", ahora())
    r = dinero.resumen(db)
    assert r.por_servicio == (("Emergencia", 40.0, 2), ("Encamamiento", 10.0, 1))
    assert r.consumido == 50 and r.numero_despachos == 3


def test_anular_devuelve_unidades_con_un_movimiento(db):
    m = medicamento(db, existencia=10)
    registro = inventario.despachar(db, "Emergencia", [LineaPedida(m.id, 4)], "v", ahora())
    with pytest.raises(ErrorDeOperacion):
        inventario.anular_despacho(db, registro, "", ahora())
    inventario.anular_despacho(db, registro, "Servicio equivocado", ahora())
    db.refresh(m)
    assert m.existencia == 10
    tipos = [mv.tipo for mv in db.scalars(select(Movimiento).where(Movimiento.medicamento_id == m.id).order_by(Movimiento.id))]
    assert tipos == ["apertura", "despacho", "anulacion"]
    assert dinero.resumen(db).consumido == 0
    with pytest.raises(ErrorDeOperacion):
        inventario.anular_despacho(db, registro, "otra vez", ahora())


def test_ajuste_explica_la_diferencia(db):
    m = medicamento(db, existencia=10)
    with pytest.raises(ErrorDeOperacion):
        inventario.ajustar(db, m, 8, "", ahora())
    mv = inventario.ajustar(db, m, 8, "Conteo físico", ahora())
    assert (mv.cantidad, mv.existencia_despues, mv.nota) == (-2, 8, "Conteo físico")


def test_llegar_al_punto_de_reorden_crea_un_solo_pedido_y_su_aviso(db):
    m = medicamento(db, existencia=40)
    registro = inventario.despachar(db, "Emergencia", [LineaPedida(m.id, 25)], "v", ahora())
    assert registro.id
    e, nuevo = pedidos.evaluar_reposicion(db, m, ahora())
    assert e.estado.clave == "pedir" and nuevo is not None
    linea = db.scalar(select(PedidoLinea).where(PedidoLinea.pedido_id == nuevo.id))
    assert linea.existencia_aviso == 15 and linea.cantidad_pedida == e.sugerida
    aviso = db.scalar(select(Mensaje).where(Mensaje.pedido_id == nuevo.id))
    assert aviso.estado == "simulado" and aviso.con_botones
    _, otro = pedidos.evaluar_reposicion(db, m, ahora())
    assert otro is None
    assert db.scalar(select(func.count()).select_from(Pedido)) == 1


def test_pedido_realizado_no_sube_la_existencia_y_la_recepcion_si(db):
    m = medicamento(db, existencia=15)
    _, pedido = pedidos.evaluar_reposicion(db, m, ahora())
    cambio, _ = pedidos.marcar_realizado(db, pedido, "Encargado", ahora())
    assert cambio
    db.refresh(m)
    assert m.existencia == 15
    linea = db.scalar(select(PedidoLinea).where(PedidoLinea.pedido_id == pedido.id))
    e = evaluar(db, m, ahora())
    assert e.en_camino == linea.cantidad_pedida and e.posicion == 15 + linea.cantidad_pedida
    repetido, texto = pedidos.marcar_realizado(db, pedido, "Otro", ahora())
    assert not repetido and "Encargado" in texto
    inventario.recibir(db, pedido, {linea.id: 100}, ahora())
    db.refresh(m), db.refresh(pedido)
    assert m.existencia == 115 and pedido.estado == "realizado"
    inventario.recibir(db, pedido, {linea.id: 0}, ahora(), cerrar=True)
    db.refresh(pedido)
    assert pedido.estado == "recibido"


def test_no_se_recibe_un_pedido_sin_realizar(db):
    m = medicamento(db, existencia=15)
    _, pedido = pedidos.evaluar_reposicion(db, m, ahora())
    linea = db.scalar(select(PedidoLinea).where(PedidoLinea.pedido_id == pedido.id))
    with pytest.raises(ErrorDeOperacion):
        inventario.recibir(db, pedido, {linea.id: 5}, ahora())


def test_compra_conjunta_cuenta_el_envio_una_vez(db):
    prov = proveedor(db)
    a = medicamento(db, "A", existencia=10, proveedor_id=prov.id)
    b = medicamento(db, "B", existencia=12, proveedor_id=prov.id)
    _, nuevos = pedidos.evaluar_grupo(db, [a, b], ahora())
    assert len(nuevos) == 1
    pedido = nuevos[0]
    lineas = pedidos.lineas_de(db, pedido)
    assert len(lineas) == 2
    assert db.scalar(select(func.count()).select_from(Mensaje)) == 1
    pedidos.marcar_realizado(db, pedido, "Encargado", ahora())
    inventario.recibir(db, pedido, {l.id: l.cantidad_pedida for l, _ in lineas}, ahora())
    esperado = sum(l.cantidad_pedida * 10 for l, _ in lineas) + 35
    assert dinero.resumen(db).compras_recibidas == pytest.approx(esperado)


def test_un_pedido_que_ya_no_alcanza_avisa_una_vez(db):
    m = medicamento(db, existencia=19)
    _, pedido = pedidos.evaluar_reposicion(db, m, ahora())
    linea = db.scalar(select(PedidoLinea).where(PedidoLinea.pedido_id == pedido.id))
    pedidos.cambiar_cantidad(db, linea, 1)
    pedidos.marcar_realizado(db, pedido, "Encargado", ahora())
    pedidos.evaluar_reposicion(db, m, ahora())
    pedidos.evaluar_reposicion(db, m, ahora())
    tipos = [x.tipo for x in db.scalars(select(Mensaje).order_by(Mensaje.id))]
    assert tipos == ["reposicion", "insuficiente"]


def test_sin_ventas_no_se_pide(db):
    m = medicamento(db, existencia=0, diarios=(0,) * 30)
    e, nuevo = pedidos.evaluar_reposicion(db, m, ahora())
    assert e.plan.D == 0 and nuevo is None


def test_los_del_mismo_proveedor_en_pedir_pronto_van_en_el_mismo_envio(db):
    prov = proveedor(db)
    a = medicamento(db, "A", existencia=10, proveedor_id=prov.id)  # llegó a R
    b = medicamento(db, "B", existencia=40, proveedor_id=prov.id)  # «pedir pronto»: le faltan unos días para R
    medicamento(db, "C", existencia=500, proveedor_id=prov.id)  # abastecido: no va
    otro = proveedor(db, "Proveedor B")
    medicamento(db, "D", existencia=40, proveedor_id=otro.id)  # pedir pronto, pero de otro proveedor: no va
    evaluaciones, nuevos = pedidos.evaluar_grupo(db, [a], ahora())
    assert len(nuevos) == 1
    lineas = pedidos.lineas_de(db, nuevos[0])
    assert [m.nombre for _, m in lineas] == ["A", "B"]
    assert evaluaciones[b.id].estado.clave == "pronto"
    aviso = db.scalar(select(Mensaje).where(Mensaje.pedido_id == nuevos[0].id))
    assert "mismo envío" in aviso.texto


def test_un_controlado_usa_99_y_suma_el_tramite_al_plazo_y_al_costo(db):
    cfg = configuracion(db)
    normal = medicamento(db, "Ketorolaco", existencia=200)
    fentanilo = medicamento(db, "Fentanilo", existencia=200, clasificacion="Controlado")
    a, b = evaluar(db, normal, ahora()), evaluar(db, fentanilo, ahora())
    assert b.parametros["p"].valor == 99 and a.parametros["p"].valor == cfg.nivel_servicio
    assert b.parametros["L"].valor == a.parametros["L"].valor + 15
    assert b.parametros["S"].valor == a.parametros["S"].valor + 300
    # Pedir antes y más cantidad que un medicamento normal con el mismo consumo.
    assert b.plan.R > a.plan.R and b.plan.Q > a.plan.Q and b.plan.IS > a.plan.IS
    assert "trámite" in b.parametros["L"].fuente


def test_controlado_con_excepciones_propias(db):
    # Su propia probabilidad manda; el trámite (días y costo) se suma a su plazo y a su costo de envío.
    morfina = medicamento(db, "Morfina", existencia=200, clasificacion="estupefaciente", nivel_servicio=97, tiempo_entrega=5, costo_pedido=50)
    e = evaluar(db, morfina, ahora())
    assert (e.parametros["p"].valor, e.parametros["L"].valor, e.parametros["S"].valor) == (97, 20, 350)


def test_el_aviso_de_un_controlado_lo_dice(db):
    fentanilo = medicamento(db, "Fentanilo", existencia=5, clasificacion="Controlado")
    _, pedido = pedidos.evaluar_reposicion(db, fentanilo, ahora())
    aviso = db.scalar(select(Mensaje).where(Mensaje.pedido_id == pedido.id))
    assert "F-SI-f-13" in aviso.texto


def test_demo_rapida_el_plazo_dura_segundos_y_el_pedido_llega_solo(db, monkeypatch):
    from datetime import timedelta

    monkeypatch.setenv("SALVAMED_SEGUNDOS_POR_DIA", "10")
    m = medicamento(db, existencia=15)
    _, pedido = pedidos.evaluar_reposicion(db, m, ahora())
    momento = ahora()
    pedidos.marcar_realizado(db, pedido, "Encargado", momento)
    plazo = pedidos.plazo_de(db, pedido)
    assert pedido.fecha_estimada - momento == timedelta(seconds=plazo * 10)
    assert pedidos.recibir_vencidos(db, momento + timedelta(seconds=1)) == []  # todavía en camino
    recibidos = pedidos.recibir_vencidos(db, pedido.fecha_estimada)
    db.refresh(m), db.refresh(pedido)
    linea = db.scalar(select(PedidoLinea).where(PedidoLinea.pedido_id == pedido.id))
    assert [p.id for p in recibidos] == [pedido.id] and pedido.estado == "recibido"
    assert m.existencia == 15 + linea.cantidad_pedida
    aviso = db.scalar(select(Mensaje).where(Mensaje.pedido_id == pedido.id, Mensaje.tipo == "recibido"))
    assert aviso is not None and "📦" in aviso.texto
