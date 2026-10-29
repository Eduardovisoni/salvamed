import pytest
from sqlalchemy import func, select

from app import dinero, inventario, pedidos
from app.db import Mensaje, Movimiento, Pedido, PedidoLinea, Venta, ahora
from app.inventario import ErrorDeOperacion, LineaPedida
from app.planificacion import evaluar
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
    assert e.parametros["S"].valor == 35 and e.parametros["L"].valor == 3
    assert e.plan.D == pytest.approx(6 * 365)
    assert e.sugerida % 10 == 0 and e.sugerida >= e.plan.Q


def test_venta_confirma_todo_o_nada(db):
    a = medicamento(db, "A", existencia=10)
    b = medicamento(db, "B", existencia=2)
    with pytest.raises(ErrorDeOperacion):
        inventario.vender(db, [LineaPedida(a.id, 3), LineaPedida(b.id, 5)], "t1", ahora())
    db.refresh(a), db.refresh(b)
    assert (a.existencia, b.existencia) == (10, 2)
    assert db.scalar(select(func.count()).select_from(Venta)) == 0


def test_el_mismo_carrito_no_se_cobra_dos_veces(db):
    m = medicamento(db, existencia=10)
    v1 = inventario.vender(db, [LineaPedida(m.id, 3)], "mismo", ahora())
    v2 = inventario.vender(db, [LineaPedida(m.id, 3)], "mismo", ahora())
    db.refresh(m)
    assert v1.id == v2.id
    assert m.existencia == 7
    assert v1.total == 45


def test_editar_el_catalogo_no_cambia_ventas_pasadas(db):
    m = medicamento(db, existencia=10)
    venta = inventario.vender(db, [LineaPedida(m.id, 2)], "v", ahora())
    m.precio_venta = 99
    m.costo_compra = 50
    db.commit()
    db.refresh(venta)
    assert venta.total == 30 and venta.costo_total == 20
    r = dinero.resumen(db)
    assert r.margen_bruto == 10


def test_anular_devuelve_unidades_con_un_movimiento(db):
    m = medicamento(db, existencia=10)
    venta = inventario.vender(db, [LineaPedida(m.id, 4)], "v", ahora())
    with pytest.raises(ErrorDeOperacion):
        inventario.anular_venta(db, venta, "", ahora())
    inventario.anular_venta(db, venta, "Producto equivocado", ahora())
    db.refresh(m)
    assert m.existencia == 10
    tipos = [mv.tipo for mv in db.scalars(select(Movimiento).where(Movimiento.medicamento_id == m.id).order_by(Movimiento.id))]
    assert tipos == ["apertura", "venta", "anulacion"]
    assert dinero.resumen(db).ventas == 0
    with pytest.raises(ErrorDeOperacion):
        inventario.anular_venta(db, venta, "otra vez", ahora())


def test_ajuste_explica_la_diferencia(db):
    m = medicamento(db, existencia=10)
    with pytest.raises(ErrorDeOperacion):
        inventario.ajustar(db, m, 8, "", ahora())
    mv = inventario.ajustar(db, m, 8, "Conteo físico", ahora())
    assert (mv.cantidad, mv.existencia_despues, mv.nota) == (-2, 8, "Conteo físico")


def test_llegar_al_punto_de_reorden_crea_un_solo_pedido_y_su_aviso(db):
    m = medicamento(db, existencia=40)
    venta = inventario.vender(db, [LineaPedida(m.id, 25)], "v", ahora())
    assert venta.id
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
