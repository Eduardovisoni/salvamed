"""Modelo estocástico de inventario de revisión continua (clase 12).

La demanda diaria sigue una distribución normal (media μD, desviación σD) y el
tiempo de entrega L es constante. Las fórmulas son las mismas de la clase:

    D    = μD · A                      (A = días de operación por año)
    Q    = √(2DS / H)                  cantidad económica de pedido (EOQ)
    σL   = √(L · σD²) = σD · √L
    IS   = Z · σL  (= R − L · μD)      inventario de seguridad
    R    = μD · L + IS                 punto de reorden
    N    = D / Q
    T    = Q / D · A                   días de operación entre pedidos (no es L)
    CT   = (D / Q) · S + (Q / 2) · H   costo de pedir y mantener el inventario de ciclo
    H_IS = H · IS

Los valores se guardan sin redondear. Si no hay demanda (D = 0) no se hacen
pedidos: Q, N y CT valen 0 y T no está definido.
"""

from dataclasses import dataclass
from math import ceil, sqrt
from statistics import NormalDist


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
    T: float | None
    CT: float
    IS: float
    H_IS: float


def z_de(nivel_servicio: float) -> float:
    return NormalDist().inv_cdf(nivel_servicio / 100)


def calcular_plan(datos: Datos) -> Plan:
    if datos.S <= 0 or datos.H <= 0:
        raise ValueError("S y H deben ser mayores que 0.")
    if datos.mu_d < 0 or datos.sigma_d < 0 or datos.L < 0 or datos.dias_habiles <= 0:
        raise ValueError("Datos fuera de rango.")
    D = datos.mu_d * datos.dias_habiles
    Z = z_de(datos.nivel_servicio)
    Q = sqrt(2 * D * datos.S / datos.H)
    sigma_L = sqrt(datos.L * datos.sigma_d**2)
    IS = Z * sigma_L
    R = datos.mu_d * datos.L + IS
    if D > 0:
        N = D / Q
        T = Q / D * datos.dias_habiles
        CT = D / Q * datos.S + Q / 2 * datos.H
    else:
        N, T, CT = 0.0, None, 0.0
    H_IS = datos.H * IS
    return Plan(D=D, Z=Z, Q=Q, sigma_L=sigma_L, R=R, N=N, T=T, CT=CT, IS=IS, H_IS=H_IS)


def cantidad_por_paquetes(Q: float, unidades_paquete: int) -> int:
    """Q redondeada hacia arriba a paquetes completos. El redondeo ya no es el óptimo exacto."""
    if Q <= 0:
        return 0
    paquete = max(int(unidades_paquete or 1), 1)
    return max(ceil(Q / paquete - 1e-9), 1) * paquete


@dataclass(frozen=True)
class Estado:
    clave: str
    etiqueta: str
    dias_para_reorden: float | None
    dias_de_cobertura: float | None


ETIQUETAS = {
    "pedir": "Pedir ya",
    "pronto": "Pedir pronto",
    "bien": "Abastecido",
    "pendiente": "Datos pendientes",
}


def estado_de(posicion: float, mu_d: float, plan: Plan, dias_de_aviso: float = 7) -> Estado:
    """Compara la posición de inventario con el punto de reorden."""
    if mu_d > 0:
        dias_para_reorden = (posicion - plan.R) / mu_d
        dias_de_cobertura = posicion / mu_d
    else:
        dias_para_reorden = dias_de_cobertura = None
    if posicion <= plan.R:
        clave = "pedir"
    elif dias_para_reorden is not None and dias_para_reorden <= dias_de_aviso:
        clave = "pronto"
    else:
        clave = "bien"
    return Estado(clave, ETIQUETAS[clave], dias_para_reorden, dias_de_cobertura)
