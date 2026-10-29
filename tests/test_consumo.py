from datetime import date, timedelta
from math import sqrt
from statistics import mean, stdev

import pytest

from app.consumo import aumento_de, consumo_de

HOY = date(2026, 10, 29)


def _dia(atras: int) -> date:
    return HOY - timedelta(days=atras)


def test_hoy_no_cuenta_y_los_dias_observados_sin_ventas_valen_cero():
    ventas = {_dia(0): 50, _dia(1): 6, _dia(2): 0, _dia(5): 4}
    c = consumo_de(ventas, {}, [], _dia(30), HOY)
    assert c.hoy == 50
    assert c.dias == 30
    valores = [cantidad for _, cantidad in c.por_dia]
    assert valores[-1] == 6 and valores[-5] == 4 and sum(valores) == 10
    assert c.mu_d == pytest.approx(mean(valores))
    assert c.sigma_d == pytest.approx(stdev(valores))
    assert c.calidad == "completa"


def test_los_dias_antes_del_alta_no_se_vuelven_ceros():
    ventas = {_dia(1): 10, _dia(2): 8, _dia(3): 12}
    c = consumo_de(ventas, {}, [], _dia(3), HOY)
    assert c.dias == 3
    assert c.mu_d == pytest.approx(10)
    assert c.calidad == "provisional"


def test_con_un_solo_dia_no_hay_desviacion():
    c = consumo_de({_dia(1): 5}, {}, [], _dia(1), HOY)
    assert not c.disponible
    assert c.calidad == "insuficiente"


def test_sin_dias_completos_no_hay_estimacion():
    c = consumo_de({HOY: 9}, {}, [], HOY, HOY)
    assert not c.disponible
    assert c.calidad == "sin_datos"
    assert c.hoy == 9


def test_la_historia_diaria_importada_cuenta_como_observada():
    historico = {_dia(i): 4 for i in range(10, 20)}
    c = consumo_de({}, historico, [], None, HOY)
    assert c.dias == 10
    assert c.mu_d == 4 and c.sigma_d == 0


def test_base_semanal_se_convierte_a_dias_y_no_inventa_ventas_diarias():
    semanas = [70, 84, 77, 63]
    c = consumo_de({}, {}, semanas, HOY, HOY)
    assert c.base == "semanal"
    assert c.mu_d == pytest.approx(mean(semanas) / 7)
    assert c.sigma_d == pytest.approx(stdev(semanas) / sqrt(7))
    assert c.por_dia == ()


def test_con_suficientes_dias_diarios_se_prefiere_lo_observado():
    ventas = {_dia(i): 3 for i in range(1, 9)}
    c = consumo_de(ventas, {}, [70, 84], _dia(8), HOY)
    assert c.base == "diaria"
    assert c.mu_d == 3


def test_los_dias_cerrados_no_cuentan():
    domingo_cerrado = "1111110"
    ventas = {_dia(i): 5 for i in range(1, 15)}
    c = consumo_de(ventas, {}, [], _dia(14), HOY, 30, domingo_cerrado)
    assert all(dia.weekday() != 6 for dia, _ in c.por_dia)
    assert c.dias == 12


def test_aumento_compara_la_ultima_semana_con_lo_anterior():
    ventas = {_dia(i): (12 if i <= 7 else 5) for i in range(1, 31)}
    c = consumo_de(ventas, {}, [], _dia(30), HOY)
    a = aumento_de(c, umbral=1.5)
    assert a is not None and a.razon == pytest.approx(12 / 5)
    estable = consumo_de({_dia(i): 5 for i in range(1, 31)}, {}, [], _dia(30), HOY)
    assert aumento_de(estable) is None


def test_sin_historia_suficiente_no_se_evalua_el_aumento():
    ventas = {_dia(i): (12 if i <= 7 else 5) for i in range(1, 15)}
    assert aumento_de(consumo_de(ventas, {}, [], _dia(14), HOY)) is None


def test_un_aumento_que_cabe_en_el_ruido_no_avisa():
    ruido = [2, 9, 3, 8, 1, 10, 4] * 3
    ventas = {_dia(i): (8 if i <= 7 else ruido[i - 8]) for i in range(1, 29)}
    c = consumo_de(ventas, {}, [], _dia(28), HOY)
    assert aumento_de(c, umbral=1.3) is None
