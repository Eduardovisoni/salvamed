from pathlib import Path

from sqlalchemy import func, select

from app import importacion, plantilla
from app.db import ConsumoHistorico, Configuracion, Medicamento, Movimiento, ahora
from app.planificacion import evaluar

CSV = (Path(__file__).resolve().parent.parent / "datos" / "SALVAMED en orden.csv").read_bytes()


def _contar(db, modelo, *filtro):
    return db.scalar(select(func.count()).select_from(modelo).where(*filtro))


def test_lee_el_csv_del_grupo_con_tabulaciones_y_cp1252(db):
    hojas, notas = importacion.leer_archivo("SALVAMED en orden.csv", CSV)
    assert "cp1252" in notas[0] and "tabulaciones" in notas[0]
    assert hojas[0].tipo == "catalogo" and len(hojas[0].filas) == 30
    assert importacion.semanas_de(hojas[0])[0] == "sem_1" and len(importacion.semanas_de(hojas[0])) == 8
    assert hojas[0].filas[0]["medicamento"] == "Ácido tranexámico"


def test_vista_previa_no_guarda_nada_y_explica_las_columnas(db):
    imp = importacion.preparar(db, "x.csv", CSV, importacion.Opciones())
    vista = imp.datos["vista"]
    assert sum(f["accion"] == "crear" for f in vista["catalogo"]) == 30
    motivos = {c["columna"]: c["motivo"] for c in vista["ignoradas"]}
    assert "costo" in motivos["precio_unitario_q"] and "recalcula" in motivos["punto_reorden"]
    assert _contar(db, Medicamento) == 0


def test_aplicar_y_reimportar_no_duplica_ni_cambia_existencias(db):
    opciones = importacion.Opciones(columna_ambigua={"precio_unitario_q": "costo_compra"})
    importacion.aplicar(db, importacion.preparar(db, "x.csv", CSV, opciones))
    assert _contar(db, Medicamento) == 30
    assert _contar(db, Movimiento, Movimiento.tipo == "apertura") == 30
    assert _contar(db, ConsumoHistorico, ConsumoHistorico.periodo == "semana") == 240
    paracetamol = db.scalar(select(Medicamento).where(Medicamento.nombre == "Paracetamol"))
    assert paracetamol.unidad == "frasco IV" and paracetamol.costo_compra == 18
    e = evaluar(db, paracetamol, ahora())
    assert e.consumo.base == "semanal" and e.plan is not None

    segunda = importacion.preparar(db, "x.csv", CSV, opciones)
    assert all(f["accion"] == "actualizar" for f in segunda.datos["vista"]["catalogo"])
    resumen = importacion.aplicar(db, segunda)
    assert resumen["creados"] == 0 and resumen["actualizados"] == 30
    assert _contar(db, Medicamento) == 30
    assert _contar(db, Movimiento) == 30
    assert _contar(db, ConsumoHistorico) == 240


def test_diferencia_de_existencia_solo_con_ajuste_explicito(db):
    importacion.aplicar(db, importacion.preparar(db, "x.csv", CSV, importacion.Opciones()))
    m = db.scalar(select(Medicamento).where(Medicamento.nombre == "Ceftriaxona"))
    m.existencia = 1
    db.commit()
    vista = importacion.preparar(db, "x.csv", CSV, importacion.Opciones())
    fila = next(f for f in vista.datos["vista"]["catalogo"] if f["datos"]["nombre"] == "Ceftriaxona")
    assert "no cambia" in fila["aviso"]
    importacion.aplicar(db, importacion.preparar(db, "x.csv", CSV, importacion.Opciones(ajustar_existencias=True)))
    db.refresh(m)
    assert m.existencia == 145
    ajuste = db.scalar(select(Movimiento).where(Movimiento.medicamento_id == m.id, Movimiento.tipo == "ajuste"))
    assert ajuste.cantidad == 144 and "importación" in ajuste.nota


def test_ejemplo_simulado_con_historia_diaria_y_configuracion(db):
    contenido = plantilla.ejemplo()
    imp = importacion.preparar(db, "ejemplo_simulado.xlsx", contenido, importacion.Opciones())
    tipos = {h["nombre"]: h["tipo"] for h in imp.datos["hojas"]}
    assert tipos["Catálogo"] == "catalogo" and tipos["Historial generado"] == "historial" and tipos["Configuración"] == "configuracion"
    resumen = importacion.aplicar(db, imp)
    assert resumen["creados"] == 8 and resumen["dias"] == 8 * plantilla.DIAS_DE_HISTORIA
    assert db.scalar(select(ConsumoHistorico.generado).limit(1)) is True
    assert db.get(Configuracion, 1).tasa_mantenimiento == 0.25
    otra = importacion.aplicar(db, importacion.preparar(db, "ejemplo_simulado.xlsx", contenido, importacion.Opciones()))
    assert otra["dias"] == 0 and otra["dias_omitidos"] == 8 * plantilla.DIAS_DE_HISTORIA
    ceftriaxona = db.scalar(select(Medicamento).where(Medicamento.nombre == "Ceftriaxona"))
    e = evaluar(db, ceftriaxona, ahora())
    assert e.consumo.base == "diaria" and e.consumo.dias == 30 and e.plan is not None


def test_filas_con_error_se_marcan(db):
    texto = "nombre\tunidad\tcosto_compra\texistencia\nBueno\tcaja\t5\t3\n\tcaja\t5\t3\nMalo\tcaja\tabc\t-1\n".encode()
    vista = importacion.preparar(db, "x.tsv", texto, importacion.Opciones()).datos["vista"]["catalogo"]
    assert [f["accion"] for f in vista] == ["crear", "error", "error"]
    assert any("nombre" in e for e in vista[1]["errores"])


def test_plantilla_vacia_trae_las_hojas(db):
    hojas, _ = importacion.leer_archivo("p.xlsx", plantilla.plantilla())
    assert [(h.nombre, h.tipo) for h in hojas] == [("Léeme", "ignorar"), ("Configuración", "configuracion")]
