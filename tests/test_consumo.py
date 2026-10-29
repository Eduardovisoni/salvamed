from datetime import datetime, timedelta, timezone
from statistics import mean, stdev

import pytest

from app.consumo import GUATEMALA, consumo_de

AHORA = datetime(2026, 10, 29, 2, 30, tzinfo=timezone.utc)  # 28/10 20:30 en Guatemala
HACE_40_DIAS = AHORA - timedelta(days=40)


def _dia(atras: int, hora: int = 9) -> datetime:
    local = AHORA.astimezone(GUATEMALA).replace(hour=hora, minute=0) - timedelta(days=atras)
    return local.astimezone(timezone.utc)


def test_promedio_y_desviacion_de_los_ultimos_30_dias_con_ceros():
    salidas = [(_dia(0), 10), (_dia(0, 15), 2), (_dia(1), 6), (_dia(29), 4), (_dia(30), 500)]
    c = consumo_de(salidas, HACE_40_DIAS, AHORA, 99, 99)
    esperado = [4] + [0] * 27 + [6, 12]
    assert c.del_historial
    assert c.dias == 30
    assert list(c.por_dia) == esperado
    assert c.mu_d == pytest.approx(mean(esperado))
    assert c.sigma_d == pytest.approx(stdev(esperado))


def test_cuenta_el_dia_con_la_hora_de_guatemala():
    noche = datetime(2026, 10, 28, 23, 30, tzinfo=GUATEMALA).astimezone(timezone.utc)
    assert noche.day == 29
    c = consumo_de([(noche, 7)], HACE_40_DIAS, AHORA, 1, 1)
    assert c.por_dia[-1] == 7


def test_con_menos_de_7_dias_usa_lo_estimado():
    c = consumo_de([(_dia(0), 50)], AHORA - timedelta(days=3), AHORA, 12, 4)
    assert not c.del_historial
    assert c.dias == 4
    assert (c.mu_d, c.sigma_d) == (12, 4)
    assert c.por_dia[-1] == 50


def test_sin_salidas_en_30_dias_usa_lo_estimado():
    c = consumo_de([], HACE_40_DIAS, AHORA, 12, 4)
    assert not c.del_historial
    assert (c.mu_d, c.sigma_d) == (12, 4)


def test_con_7_dias_ya_usa_el_historial():
    salidas = [(_dia(i), 5 + i) for i in range(7)]
    c = consumo_de(salidas, _dia(6, 6), AHORA, 99, 99)
    assert c.del_historial
    assert c.mu_d == pytest.approx(8)
