import re

import pytest
from sqlalchemy import select

from app.db import Medicamento, Movimiento, Pedido
from tests.ayudas import medicamento


def _token(html: str) -> str:
    return re.search(r'name="token" value="([^"]+)"', html).group(1)


def test_salud(cliente):
    assert cliente.get("/salud").json() == {"ok": True}


@pytest.mark.parametrize("ruta", ["/", "/medicamentos", "/medicamentos/nuevo", "/pedidos", "/costos", "/configuracion", "/importar", "/simulacion"])
def test_las_pantallas_cargan_con_la_base_vacia(cliente, ruta):
    assert cliente.get(ruta).status_code == 200


def test_la_operacion_arranca_sin_datos_inventados(cliente, db):
    cliente.get("/")
    assert db.scalar(select(Medicamento.id)) is None
    assert db.scalar(select(Movimiento.id)) is None


def test_alta_minima_desde_el_formulario(cliente, db):
    r = cliente.post("/medicamentos", data={"nombre": "Gasas", "unidad": "paquete", "existencia_inicial": "5"}, follow_redirects=False)
    assert r.status_code == 303
    detalle = cliente.get(r.headers["location"]).text
    assert "Datos pendientes" in detalle and "Falta el costo de compra" in detalle
    m = db.scalar(select(Medicamento).where(Medicamento.nombre == "Gasas"))
    assert m.existencia == 5


def test_alta_valida_y_no_repite_nombre_y_presentacion(cliente):
    datos = {"nombre": "X", "presentacion": "1 g", "unidad": "vial", "costo_compra": "-3"}
    r = cliente.post("/medicamentos", data=datos)
    assert r.status_code == 400 and "Debe ser mayor que 0" in r.text
    datos["costo_compra"] = "3,5"
    assert cliente.post("/medicamentos", data=datos, follow_redirects=False).status_code == 303
    assert "Ya existe" in cliente.post("/medicamentos", data=datos).text


def test_despachar_desde_la_pantalla_y_aviso_de_reposicion(cliente, db):
    m = medicamento(db, existencia=40)
    html = cliente.get("/").text
    assert "Emergencia" in html and "Cuidados Intensivos" in html
    datos = {"token": _token(html), "servicio": "Emergencia", "medicamento_id": [str(m.id)], "cantidad": ["25"]}
    r = cliente.post("/despachos", data=datos, follow_redirects=False)
    assert r.status_code == 303
    pagina = cliente.get(r.headers["location"]).text
    assert "Despacho #" in pagina and "a Emergencia" in pagina and "necesita reposición" in pagina
    assert cliente.post("/despachos", data=datos, follow_redirects=False).headers["location"] == r.headers["location"]
    db.refresh(m)
    assert m.existencia == 15
    assert "Emergencia" in cliente.get("/costos").text
    pedido = db.scalar(select(Pedido))
    assert pedido.estado == "pendiente"
    assert cliente.post(f"/pedidos/{pedido.id}/realizado", follow_redirects=False).status_code == 303
    db.refresh(pedido)
    assert pedido.estado == "realizado" and pedido.confirmado_por.startswith("Web")


def test_despacho_a_un_servicio_que_no_existe(cliente, db):
    m = medicamento(db, existencia=5)
    r = cliente.post("/despachos", data={"token": "x1", "servicio": "Cafetería", "medicamento_id": [str(m.id)], "cantidad": ["1"]})
    assert r.status_code == 400 and "Elige el servicio" in r.text


def test_no_despacha_mas_de_lo_que_hay(cliente, db):
    m = medicamento(db, existencia=3)
    r = cliente.post("/despachos", data={"token": "abc", "servicio": "Encamamiento", "medicamento_id": [str(m.id)], "cantidad": ["5"]})
    assert r.status_code == 400 and "Solo hay 3" in r.text


def test_los_servicios_se_configuran(cliente, db):
    from app.db import Configuracion

    formulario = {
        "dias_operacion_anio": "365", "hora_apertura": "0", "hora_cierre": "24", "nivel_servicio": "95",
        "tasa_mantenimiento": "25", "costo_pedido": "35", "tiempo_entrega": "3", "ventana_dias": "30",
        "dias_aviso": "7", "aumento_umbral": "1.5", "servicios": "Emergencia\nPediatría\n\nEmergencia",
        "nivel_controlados": "99", "dias_tramite_controlados": "15", "costo_tramite_controlados": "300",
    }
    formulario.update({f"dia_{i}": "1" for i in range(7)})
    assert cliente.post("/configuracion", data=formulario, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Configuracion, 1).servicios == "Emergencia\nPediatría"
    formulario["servicios"] = "   "
    assert cliente.post("/configuracion", data=formulario).status_code == 400


def test_detalle_muestra_el_plan_y_la_fuente(cliente, db):
    m = medicamento(db)
    html = cliente.get(f"/medicamentos/{m.id}").text
    for texto in ("Plan de inventario (clase 12)", "Q = √(2DS / H)", "tasa anual", "no entra en el promedio", "h) Política del inventario"):
        assert texto in html


def test_con_clave_hay_que_entrar(cliente, monkeypatch):
    monkeypatch.setenv("SALVAMED_CLAVE", "secreta")
    r = cliente.get("/medicamentos", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/entrar")
    assert cliente.post("/configuracion/demo", follow_redirects=False).status_code == 401
    assert cliente.post("/entrar", data={"clave": "mala"}).status_code == 401
    r = cliente.post("/entrar", data={"clave": "secreta", "siguiente": "/pedidos"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/pedidos"
    assert cliente.get("/medicamentos").status_code == 200
    cliente.post("/salir")
    cliente.cookies.clear()


def test_en_linea_sin_clave_es_solo_lectura(cliente, monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    assert cliente.get("/medicamentos").status_code == 200
    assert cliente.post("/configuracion/demo").status_code == 403


def test_cargar_la_demostracion_es_explicito_y_no_duplica(cliente, db):
    cliente.post("/configuracion/demo")
    cliente.post("/configuracion/demo")
    assert len(db.scalars(select(Medicamento)).all()) == 30
    assert cliente.get("/medicamentos").text.count('class="enlace-fila"') == 30


def test_plantillas_descargables(cliente):
    for nombre in ("plantilla_salvamed.xlsx", "ejemplo_simulado.xlsx"):
        r = cliente.get(f"/plantillas/{nombre}")
        assert r.status_code == 200 and r.content[:2] == b"PK"
