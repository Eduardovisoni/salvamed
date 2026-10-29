import re

import pytest

DATOS_SONY = {
    "nombre": "Televisor Sony (ejemplo de clase)",
    "unidad": "televisores",
    "existencia": "300",
    "consumo_promedio": "20",
    "desviacion": "6.15",
    "tiempo_entrega": "14",
    "costo_pedido": "10",
    "costo_mantener": "0.5",
    "nivel_servicio": "99.5",
    "dias_habiles": "365",
}


def _id_de(respuesta):
    return re.search(r"/medicamentos/(\d+)", respuesta.headers["location"]).group(1)


def test_salud(cliente):
    assert cliente.get("/salud").json() == {"ok": True}


def test_la_pantalla_principal_es_para_registrar_salidas(cliente):
    r = cliente.get("/")
    assert r.status_code == 200
    assert 'action="/salidas"' in r.text
    assert 'href="/medicamentos?estado=pedir"' in r.text
    assert "Costo anual del inventario" in r.text
    assert 'class="tabla"' not in r.text


def test_salidas_redirige_a_la_principal(cliente):
    r = cliente.get("/salidas?medicamento=5", follow_redirects=False)
    assert r.headers["location"] == "/?medicamento=5"


def test_la_tabla_trae_los_30_medicamentos_iniciales(cliente):
    r = cliente.get("/medicamentos")
    assert r.status_code == 200
    assert r.text.count('class="enlace-fila"') >= 30
    assert "Ceftriaxona" in r.text
    assert "Pedir ya" in r.text


def test_crear_ver_editar_y_eliminar(cliente):
    r = cliente.post("/medicamentos", data=DATOS_SONY, follow_redirects=False)
    assert r.status_code == 303
    medicamento_id = _id_de(r)

    detalle = cliente.get(f"/medicamentos/{medicamento_id}?guardado=1").text
    for valor in ("540.37", "339.27", "23.01", "13.51", "27.02", "Q\u00a0270.19", "59.27", "Q\u00a029.64"):
        assert valor in detalle
    assert "Pedir ya" in detalle
    assert "Datos guardados" in detalle

    formulario = cliente.get(f"/medicamentos/{medicamento_id}/editar").text
    assert 'value="6.15"' in formulario

    r = cliente.post(
        f"/medicamentos/{medicamento_id}",
        data={**DATOS_SONY, "existencia": "600"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "Abastecido" in cliente.get(f"/medicamentos/{medicamento_id}").text

    r = cliente.post(f"/medicamentos/{medicamento_id}/eliminar", follow_redirects=False)
    assert r.status_code == 303
    assert cliente.get(f"/medicamentos/{medicamento_id}").status_code == 404


def test_valida_los_datos(cliente):
    malos = {
        **DATOS_SONY,
        "nombre": "Prueba de validación",
        "nivel_servicio": "100",
        "consumo_promedio": "0",
        "existencia": "2.5",
        "costo_pedido": "abc",
    }
    r = cliente.post("/medicamentos", data=malos)
    assert r.status_code == 400
    assert "Debe ser menor que 100." in r.text
    assert "Debe ser mayor que 0." in r.text
    assert "Debe ser un número entero." in r.text
    assert "Escribe un número." in r.text
    assert 'value="Prueba de validación"' in r.text


def test_no_permite_nombres_repetidos(cliente):
    r = cliente.post("/medicamentos", data={**DATOS_SONY, "nombre": "ceftriaxona"})
    assert r.status_code == 400
    assert "Ya existe un medicamento con ese nombre." in r.text


def test_acepta_coma_decimal(cliente):
    r = cliente.post(
        "/medicamentos",
        data={**DATOS_SONY, "nombre": "Prueba coma", "desviacion": "6,15"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "23.01" in cliente.get(f"/medicamentos/{_id_de(r)}").text
    cliente.post(f"/medicamentos/{_id_de(r)}/eliminar")


def test_un_medicamento_que_no_existe_da_404(cliente):
    assert cliente.get("/medicamentos/999999").status_code == 404


def _medicamento(nombre):
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.db import Medicamento, motor

    with Session(motor) as db:
        m = db.scalar(select(Medicamento).where(Medicamento.nombre == nombre))
        db.expunge(m)
        return m


def _consumo(medicamento_id):
    from sqlalchemy.orm import Session

    from app.db import Medicamento, ahora, motor
    from app.main import _consumos

    with Session(motor) as db:
        m = db.get(Medicamento, medicamento_id)
        return _consumos(db, [m], ahora())[m.id]


def test_los_iniciales_traen_30_dias_de_salidas(cliente):
    c = _consumo(_medicamento("Ceftriaxona").id)
    assert c.del_historial and c.dias == 30
    assert 11 < c.mu_d < 13


def test_registrar_salida_baja_la_existencia_y_recalcula_el_consumo(cliente):
    m = _medicamento("Furosemida")
    antes = _consumo(m.id)
    r = cliente.post(
        "/salidas",
        data={"medicamento_id": str(m.id), "cantidad": "60", "nota": "Sala de Emergencias"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "medicamento=" not in r.headers["location"]
    pagina = cliente.get(r.headers["location"]).text
    assert "Salida registrada" in pagina
    assert "Deshacer" in pagina
    assert " selected>" not in pagina
    assert _medicamento("Furosemida").existencia == m.existencia - 60
    assert _consumo(m.id).mu_d == pytest.approx(antes.mu_d + 60 / 30)

    hecho = re.search(r"hecho=(\d+)", r.headers["location"]).group(1)
    r = cliente.post(f"/movimientos/{hecho}/deshacer", data={"volver": "salida"}, follow_redirects=False)
    assert r.status_code == 303
    assert _medicamento("Furosemida").existencia == m.existencia
    assert _consumo(m.id).mu_d == pytest.approx(antes.mu_d)


def test_no_deja_sacar_mas_de_lo_que_hay(cliente):
    m = _medicamento("Insulina cristalina")
    r = cliente.post(
        "/salidas",
        data={"medicamento_id": str(m.id), "cantidad": str(m.existencia + 1), "nota": "Sala de traumas"},
    )
    assert r.status_code == 400
    assert f"Solo hay {m.existencia} frascos en bodega." in r.text
    assert _medicamento("Insulina cristalina").existencia == m.existencia


def test_valida_la_salida(cliente):
    r = cliente.post("/salidas", data={"medicamento_id": "", "cantidad": "1.5", "nota": "Cocina"})
    assert r.status_code == 400
    assert "Elige un medicamento." in r.text
    assert "Debe ser un número entero." in r.text
    assert "Elige una sala." in r.text


def test_la_pagina_de_ingresos_propone_q(cliente):
    m = _medicamento("Morfina")
    r = cliente.get(f"/ingresos?medicamento={m.id}")
    assert r.status_code == 200
    assert 'action="/ingresos"' in r.text
    assert "Cantidad que entra" in r.text
    assert "Elige una sala" not in r.text
    assert re.search(rf'value="{m.id}" data-q="\d+" selected', r.text)


def test_registrar_ingreso_saca_del_rojo(cliente):
    m = _medicamento("Morfina")
    assert "Pedir ya" in cliente.get(f"/medicamentos/{m.id}").text
    r = cliente.post(
        "/ingresos",
        data={"medicamento_id": str(m.id), "cantidad": "200", "nota": "Factura 1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/ingresos?hecho=")
    pagina = cliente.get(r.headers["location"]).text
    assert "Ingreso registrado" in pagina
    assert _medicamento("Morfina").existencia == m.existencia + 200
    assert 'class="estado estado--bien"' in cliente.get(f"/medicamentos/{m.id}").text


def test_valida_el_ingreso(cliente):
    r = cliente.post("/ingresos", data={"medicamento_id": "999999", "cantidad": "0"})
    assert r.status_code == 400
    assert "Elige un medicamento." in r.text
    assert "Debe ser 1 o más." in r.text


def test_el_detalle_ya_no_tiene_formularios_de_movimiento(cliente):
    m = _medicamento("Haloperidol")
    pagina = cliente.get(f"/medicamentos/{m.id}").text
    assert f'href="/?medicamento={m.id}"' in pagina
    assert f'href="/ingresos?medicamento={m.id}"' in pagina
    assert "Historial de movimientos" in pagina
    assert cliente.post(f"/medicamentos/{m.id}/salidas", data={"cantidad": "1"}).status_code in (404, 405)


def test_un_medicamento_nuevo_usa_lo_estimado_y_se_puede_eliminar_con_movimientos(cliente):
    r = cliente.post("/medicamentos", data={**DATOS_SONY, "nombre": "Prueba nuevo"}, follow_redirects=False)
    medicamento_id = _id_de(r)
    r = cliente.post(
        "/salidas",
        data={"medicamento_id": medicamento_id, "cantidad": "5", "nota": "Sala de traumas"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "Sala de traumas" in cliente.get(f"/medicamentos/{medicamento_id}").text
    c = _consumo(int(medicamento_id))
    assert not c.del_historial and c.mu_d == 20
    assert "se necesitan 7" in cliente.get(f"/medicamentos/{medicamento_id}").text
    r = cliente.post(f"/medicamentos/{medicamento_id}/eliminar", follow_redirects=False)
    assert r.status_code == 303
    assert cliente.get(f"/medicamentos/{medicamento_id}").status_code == 404
