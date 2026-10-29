import pytest

from app.modelo import Datos, calcular_plan, cantidad_por_paquetes, estado_de

SONY = Datos(dias_habiles=365, mu_d=20, sigma_d=6.15, L=14, S=10, H=0.5, nivel_servicio=99.5)


def test_reproduce_el_ejemplo_de_la_clase_12():
    p = calcular_plan(SONY)
    assert p.D == 7300
    assert round(p.Z, 3) == 2.576
    assert round(p.Q, 2) == 540.37
    assert round(p.sigma_L, 2) == 23.01
    assert round(p.R, 2) == 339.27
    assert p.R_pedido == 340
    assert round(p.N, 2) == 13.51
    assert round(p.T, 2) == 27.02
    assert round(p.CT, 2) == 270.19
    assert round(p.IS, 2) == 59.27
    assert round(p.H_IS, 2) == 29.64


def test_is_es_r_menos_l_por_mu():
    p = calcular_plan(SONY)
    assert p.IS == pytest.approx(p.R - SONY.L * SONY.mu_d)


@pytest.mark.parametrize(
    ("posicion", "clave"),
    [(339, "pedir"), (340, "pedir"), (341, "pronto"), (480, "pronto"), (481, "bien")],
)
def test_estado_segun_la_posicion(posicion, clave):
    p = calcular_plan(SONY)
    assert estado_de(posicion, SONY.mu_d, p).clave == clave


def test_sin_variacion_no_hay_inventario_de_seguridad():
    p = calcular_plan(Datos(365, 20, 0, 14, 10, 0.5, 95))
    assert p.IS == 0
    assert p.R == 280
    assert p.R_pedido == 280


def test_sin_demanda_no_divide_entre_cero():
    p = calcular_plan(Datos(365, 0, 0, 3, 35, 2, 95))
    assert (p.D, p.Q, p.N, p.CT, p.R) == (0, 0, 0, 0, 0)
    assert p.T is None
    e = estado_de(5, 0, p)
    assert e.dias_para_reorden is None and e.dias_de_cobertura is None


@pytest.mark.parametrize("S,H", [(0, 1), (35, 0), (-1, 2)])
def test_s_y_h_deben_ser_positivos(S, H):
    with pytest.raises(ValueError):
        calcular_plan(Datos(365, 5, 1, 3, S, H, 95))


@pytest.mark.parametrize(("Q", "paquete", "esperado"), [(57.3, 10, 60), (60, 10, 60), (0.4, 12, 12), (0, 10, 0), (7.2, 1, 8)])
def test_redondeo_a_paquetes_completos(Q, paquete, esperado):
    assert cantidad_por_paquetes(Q, paquete) == esperado
