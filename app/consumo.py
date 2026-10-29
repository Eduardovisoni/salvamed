"""μD y σD a partir de lo que realmente se despachó a los servicios.

Solo cuentan días completos: hoy todavía no termina, así que se muestra aparte
como indicador intradía y no entra en el promedio. Se usan los últimos N días
completos (N configurable, 30 al inicio) en los que se operó (el sanatorio opera todos los días).

Un día cuenta como observado si el medicamento ya estaba en el sistema ese día
(desde `observado_desde`) o si hay historia diaria importada para ese día. Un
día observado sin despachos vale 0; un día sin registro no se convierte en 0.

Con 2 o más días completos ya se puede calcular la desviación muestral, pero
con pocos días la estimación es provisional. Si todavía hay menos de 7 días
diarios y existe historia semanal importada, se usa la base semanal convertida
a días: μD = μS / d y σD = σS / √d, donde d son los días abiertos por semana
(supone días independientes entre sí).

Lo que un servicio necesitó y no se le pudo despachar por falta de existencia
no queda registrado: la demanda observada puede subestimar la demanda real.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import sqrt
from statistics import mean, stdev

GUATEMALA = timezone(timedelta(hours=-6))
DIAS_PARA_PREFERIR_DIARIO = 7


@dataclass(frozen=True)
class Consumo:
    mu_d: float | None
    sigma_d: float | None
    dias: int
    calidad: str  # "sin_datos", "insuficiente", "provisional" o "completa"
    base: str  # "diaria", "semanal" o ""
    por_dia: tuple[tuple[date, int], ...]
    hoy: int
    semanas: int = 0

    @property
    def disponible(self) -> bool:
        return self.mu_d is not None and self.sigma_d is not None

    @property
    def descripcion(self) -> str:
        if self.base == "semanal":
            return f"Derivado de {self.semanas} totales semanales importados (supuesto: días independientes)"
        if self.calidad == "sin_datos":
            return "Todavía no hay días completos con despachos registrados"
        if self.calidad == "insuficiente":
            return "Solo hay 1 día completo: se necesitan al menos 2 para la desviación"
        dias = f"{self.dias} {'día completo' if self.dias == 1 else 'días completos'}"
        if self.calidad == "provisional":
            return f"Provisional: {dias} de despachos"
        return f"Estimación actualizada con {dias} de despachos"


def a_utc(fecha: datetime) -> datetime:
    if fecha.tzinfo is None:
        return fecha.replace(tzinfo=timezone.utc)
    return fecha.astimezone(timezone.utc)


def dia_local(fecha: datetime) -> date:
    return a_utc(fecha).astimezone(GUATEMALA).date()


def hora_local(fecha: datetime) -> datetime:
    return a_utc(fecha).astimezone(GUATEMALA)


def abre(dia: date, dias_semana: str) -> bool:
    """dias_semana: 7 caracteres de lunes a domingo, '1' si se opera ese día."""
    return dias_semana[dia.weekday()] == "1"


def dias_abiertos_por_semana(dias_semana: str) -> int:
    return max(dias_semana.count("1"), 1)


def dias_de_ventana(hoy: date, ventana: int, dias_semana: str) -> list[date]:
    """Los últimos `ventana` días completos (antes de hoy) en que se operó."""
    dias, dia = [], hoy - timedelta(days=1)
    limite = ventana * 7
    while len(dias) < ventana and limite > 0:
        if abre(dia, dias_semana):
            dias.append(dia)
        dia -= timedelta(days=1)
        limite -= 1
    return sorted(dias)


def consumo_de(
    despachos_por_dia: dict[date, int],
    historico_diario: dict[date, int],
    semanales: list[int],
    observado_desde: date | None,
    hoy: date,
    ventana: int = 30,
    dias_semana: str = "1111111",
) -> Consumo:
    observados = []
    for dia in dias_de_ventana(hoy, ventana, dias_semana):
        if dia in historico_diario:
            observados.append((dia, historico_diario[dia]))
        elif observado_desde is not None and dia >= observado_desde:
            observados.append((dia, despachos_por_dia.get(dia, 0)))
    hoy_total = despachos_por_dia.get(hoy, 0)
    valores = [cantidad for _, cantidad in observados]
    n = len(valores)
    calidad = "completa" if n >= ventana else "provisional"

    if len(semanales) >= 2 and n < DIAS_PARA_PREFERIR_DIARIO:
        d = dias_abiertos_por_semana(dias_semana)
        return Consumo(
            mean(semanales) / d,
            stdev(semanales) / sqrt(d),
            n,
            "provisional",
            "semanal",
            tuple(observados),
            hoy_total,
            len(semanales),
        )
    if n >= 2:
        return Consumo(float(mean(valores)), float(stdev(valores)), n, calidad, "diaria", tuple(observados), hoy_total)
    return Consumo(
        None, None, n, "insuficiente" if n == 1 else "sin_datos", "", tuple(observados), hoy_total
    )


@dataclass(frozen=True)
class Aumento:
    reciente: float
    anterior: float
    razon: float
    z: float
    dias_recientes: int
    dias_anteriores: int


Z_AUMENTO = 3.0


def aumento_de(consumo: Consumo, umbral: float = 1.5, recientes: int = 7, minimo_anterior: int = 14) -> Aumento | None:
    """Compara el promedio de los últimos 7 días completos con el de los días anteriores.

    Avisa si el promedio reciente es al menos `umbral` veces el anterior y la
    diferencia es de 3 errores estándar o más: z = (reciente − anterior) /
    (s_anterior · √(1/7 + 1/n)). Es una prueba simple y explicable, no un
    diagnóstico: con menos de 14 días anteriores no se evalúa, con poco consumo
    puede haber falsas alarmas, y un pico genuino no se elimina.
    """
    if consumo.base != "diaria":
        return None
    valores = [cantidad for _, cantidad in consumo.por_dia]
    if len(valores) < recientes + minimo_anterior:
        return None
    reciente = mean(valores[-recientes:])
    previos = valores[:-recientes]
    anterior = mean(previos)
    if anterior <= 0:
        return None
    razon = reciente / anterior
    desviacion = stdev(previos)
    z = (reciente - anterior) / (desviacion * sqrt(1 / recientes + 1 / len(previos))) if desviacion > 0 else float("inf")
    if razon < umbral or z < Z_AUMENTO:
        return None
    return Aumento(reciente, anterior, razon, z, recientes, len(previos))
