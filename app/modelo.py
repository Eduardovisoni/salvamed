"""Modelo estocástico de inventario de revisión continua (clase 12).

La demanda diaria sigue una distribución normal (media μD, desviación σD) y el
tiempo de entrega L es constante. Las fórmulas son las mismas de la clase:

    D    = μD · días hábiles
    Q    = √(2DS / H)
    σL   = √(L · σD²)
    R    = μD · L + Z · σL
    N    = D / Q
    T    = Q / D · días hábiles
    CT   = (D / Q) · S + (Q / 2) · H
    IS   = R − L · μD
    H_IS = H · IS
"""

from dataclasses import dataclass
from math import sqrt
from statistics import NormalDist

DIAS_DE_AVISO = 7


@dataclass(frozen=True)
class Datos:
    dias_habiles: float
    mu_d: float
    sigma_d: float
    L: float
    S: float
    H: float
    nivel_servicio: float


@dataclass(frozen=True)
class Plan:
    D: float
    Z: float
    Q: float
    sigma_L: float
    R: float
    N: float
    T: float
    CT: float
    IS: float
    H_IS: float


def z_de(nivel_servicio: float) -> float:
    return NormalDist().inv_cdf(nivel_servicio / 100)


def calcular_plan(datos: Datos) -> Plan:
    D = datos.mu_d * datos.dias_habiles
    Z = z_de(datos.nivel_servicio)
    Q = sqrt(2 * D * datos.S / datos.H)
    sigma_L = sqrt(datos.L * datos.sigma_d**2)
    R = datos.mu_d * datos.L + Z * sigma_L
    N = D / Q
    T = Q / D * datos.dias_habiles
    CT = D / Q * datos.S + Q / 2 * datos.H
    IS = R - datos.L * datos.mu_d
    H_IS = datos.H * IS
    return Plan(D=D, Z=Z, Q=Q, sigma_L=sigma_L, R=R, N=N, T=T, CT=CT, IS=IS, H_IS=H_IS)


@dataclass(frozen=True)
class Estado:
    clave: str
    etiqueta: str
    dias_para_reorden: float
    dias_de_cobertura: float


def estado_de(existencia: float, mu_d: float, plan: Plan) -> Estado:
    dias_para_reorden = (existencia - plan.R) / mu_d
    dias_de_cobertura = existencia / mu_d
    if existencia <= plan.R:
        return Estado("pedir", "Pedir ya", dias_para_reorden, dias_de_cobertura)
    if dias_para_reorden <= DIAS_DE_AVISO:
        return Estado("pronto", "Pedir pronto", dias_para_reorden, dias_de_cobertura)
    return Estado("bien", "Abastecido", dias_para_reorden, dias_de_cobertura)
