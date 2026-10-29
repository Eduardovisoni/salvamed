import pytest

from app.modelo import Datos, calcular_plan, estado_de

SONY = Datos(dias_habiles=365, mu_d=20, sigma_d=6.15, L=14, S=10, H=0.5, nivel_servicio=99.5)


def test_reproduce_el_ejemplo_de_la_clase_12():
    p = calcular_plan(SONY)
    assert p.D == 7300
    assert round(p.Z, 3) == 2.576
    assert round(p.Q, 2) == 540.37
    assert round(p.sigma_L, 2) == 23.01
    assert round(p.R, 2) == 339.27
    assert round(p.N, 2) == 13.51
    assert round(p.T, 2) == 27.02
    assert round(p.CT, 2) == 270.19
    assert round(p.IS, 2) == 59.27
    assert round(p.H_IS, 2) == 29.64


@pytest.mark.parametrize(
    ("existencia", "clave"),
    [(339, "pedir"), (339.27, "pedir"), (340, "pronto"), (479, "pronto"), (480, "bien")],
)
def test_estado_segun_la_existencia(existencia, clave):
    p = calcular_plan(SONY)
    assert estado_de(existencia, SONY.mu_d, p).clave == clave


def test_sin_variacion_no_hay_inventario_de_seguridad():
    p = calcular_plan(Datos(365, 20, 0, 14, 10, 0.5, 95))
    assert p.IS == 0
    assert p.R == 280
