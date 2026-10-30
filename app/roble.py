"""El Roble hoy: cómo pide el sanatorio sin SALVAMED, para compararlo en la simulación.

Recibe exactamente las mismas solicitudes de los servicios que el lado de
SALVAMED (misma semilla, mismo día, mismo minuto) y arranca con las mismas
existencias. Solo cambia la regla para pedir:

- Cuándo: cuando el medicamento se acaba (existencia 0) y no hay un pedido en camino.
- Cuánto: a ojo, entre 7 y 21 días del consumo habitual, a paquetes completos.

Lo demás es igual en los dos lados:

- Cada pedido tarda un plazo aleatorio entre L − 1 y L + 1 días (en promedio L).
  El pedido número k de un medicamento usa el mismo sorteo en los dos lados.
- Lo que un servicio pide y no hay se consigue de emergencia, con un recargo
  sobre el precio de compra.
- Costos: envío S por pedido, mantener H = costo × tasa anual por unidad al año,
  y el recargo de las emergencias.

Es una función pura: con los mismos datos da siempre el mismo resultado, así
que no guarda nada en la base y se recalcula en cada consulta.
"""

import math
import random
from dataclasses import dataclass, field
from typing import Iterable

DIAS_A_OJO = (7, 21)


def plazo_aleatorio(semilla: int, indice: int, k: int, L: float) -> float:
    """Días que tarda el pedido número `k` del medicamento `indice`: entre L − 1 y L + 1 (nunca menos de 0)."""
    azar = random.Random(f"salvamed:{semilla}:plazo:{indice}:{k}")
    return azar.uniform(max(L - 1, 0), L + 1)


def cantidad_a_ojo(semilla: int, indice: int, k: int, consumo: float, paquete: int) -> int:
    """Lo que el Roble pide la vez `k`: entre 7 y 21 días del consumo habitual, a paquetes completos."""
    dias = random.Random(f"salvamed:{semilla}:ojo:{indice}:{k}").uniform(*DIAS_A_OJO)
    paquete = max(int(paquete or 1), 1)
    return max(math.ceil(consumo * dias / paquete - 1e-9), 1) * paquete


@dataclass(frozen=True)
class Tarifas:
    S: float  # envío por pedido (Q)
    tasa: float  # tasa anual de mantener (0.25 = 25 %)
    A: float  # días de operación por año
    recargo: float  # recargo de una compra de emergencia (0.20 = 20 %)


def costos(costo_unitario: float, envios: float, unidades_dia: float, emergencia_u: int, t: Tarifas) -> dict[str, float]:
    """La misma cuenta para los dos lados: pedir + mantener + recargo de emergencias."""
    pedir = envios
    mantener = costo_unitario * t.tasa / t.A * unidades_dia
    emergencia = emergencia_u * costo_unitario * t.recargo
    return {"pedir": pedir, "mantener": mantener, "emergencia": emergencia, "total": pedir + mantener + emergencia}


@dataclass
class Estante:
    existencia: int
    en_camino: int = 0
    llega: float | None = None
    pedidos: int = 0
    despachado: int = 0
    emergencia_u: int = 0
    emergencia_veces: int = 0
    area: float = 0.0  # unidades × minutos
    t: float = 0.0
    puntos: list = field(default_factory=list)

    def hasta(self, minuto: float) -> None:
        """Lleva la cuenta de lo guardado hasta `minuto` y recibe el pedido si ya llegó."""
        if self.llega is not None and self.llega <= minuto:
            self.area += self.existencia * (self.llega - self.t)
            self.t = self.llega
            self.existencia += self.en_camino
            self.puntos.append([round(self.llega, 2), self.existencia])
            self.en_camino, self.llega = 0, None
        self.area += self.existencia * (minuto - self.t)
        self.t = max(self.t, minuto)


@dataclass(frozen=True)
class Suceso:
    minuto: float
    tipo: str  # agotado, recepcion, emergencia
    indice: int
    cantidad: int
    llega: float | None = None


def simular(productos, solicitudes: Iterable[tuple[float, str, tuple]], hasta: float, minutos_dia: float, semilla: int, demora: float = 0):
    """Recorre las solicitudes (minuto, servicio, compras) en orden hasta el minuto `hasta`.

    `productos` necesita `existencia`, `plazo`, `consumo` y `paquete` de cada medicamento.
    Devuelve los estantes (uno por producto) y lo que pasó, en orden.
    """
    estantes = [Estante(p.existencia, puntos=[[0, p.existencia]]) for p in productos]
    sucesos: list[Suceso] = []

    def llegadas(minuto: float) -> None:
        for j, e in enumerate(estantes):
            if e.llega is not None and e.llega <= minuto:
                cantidad, cuando = e.en_camino, e.llega
                e.hasta(cuando)
                sucesos.append(Suceso(cuando, "recepcion", j, cantidad))

    for minuto, _servicio, compras in solicitudes:
        if minuto > hasta:
            break
        llegadas(minuto)
        for j, cantidad in compras:
            e, p = estantes[j], productos[j]
            e.hasta(minuto)
            entregable = min(cantidad, e.existencia)
            e.existencia -= entregable
            e.despachado += entregable
            if entregable:
                e.puntos.append([round(minuto, 2), e.existencia])
            if e.existencia == 0 and e.llega is None:
                realizado = minuto + demora
                e.llega = realizado + plazo_aleatorio(semilla, j, e.pedidos, p.plazo) * minutos_dia
                e.en_camino = cantidad_a_ojo(semilla, j, e.pedidos, p.consumo, p.paquete)
                e.pedidos += 1
                sucesos.append(Suceso(minuto, "agotado", j, e.en_camino, e.llega))
            if entregable < cantidad:
                e.emergencia_u += cantidad - entregable
                e.emergencia_veces += 1
                sucesos.append(Suceso(minuto, "emergencia", j, cantidad - entregable))
    llegadas(hasta)
    for e in estantes:
        e.hasta(hasta)
    return estantes, sucesos
