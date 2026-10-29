"""μD y σD a partir de las salidas registradas.

Se suma lo que salió cada día (hora de Guatemala) en los últimos 30 días,
contando hoy y contando como 0 los días sin salidas. μD es el promedio de esos
totales diarios y σD su desviación estándar muestral.

Los días se cuentan desde que el medicamento entró al sistema. Mientras tenga
menos de 7 días (o ninguna salida en la ventana) se usan los valores estimados
que se escribieron en el formulario.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from statistics import mean, stdev

GUATEMALA = timezone(timedelta(hours=-6))
VENTANA_DIAS = 30
DIAS_MINIMOS = 7


@dataclass(frozen=True)
class Consumo:
    mu_d: float
    sigma_d: float
    del_historial: bool
    dias: int
    por_dia: tuple[int, ...]


def a_utc(fecha: datetime) -> datetime:
    if fecha.tzinfo is None:
        return fecha.replace(tzinfo=timezone.utc)
    return fecha.astimezone(timezone.utc)


def dia_local(fecha: datetime) -> date:
    return a_utc(fecha).astimezone(GUATEMALA).date()


def hora_local(fecha: datetime) -> datetime:
    return a_utc(fecha).astimezone(GUATEMALA)


def inicio_de_ventana(ahora: datetime) -> datetime:
    primer_dia = dia_local(ahora) - timedelta(days=VENTANA_DIAS - 1)
    return datetime.combine(primer_dia, time(), GUATEMALA).astimezone(timezone.utc)


def consumo_de(
    salidas: list[tuple[datetime, int]],
    desde: datetime,
    ahora: datetime,
    mu_estimado: float,
    sigma_estimado: float,
) -> Consumo:
    hoy = dia_local(ahora)
    dias = max(1, min(VENTANA_DIAS, (hoy - dia_local(desde)).days + 1))
    por_dia = [0] * dias
    for fecha, cantidad in salidas:
        atras = (hoy - dia_local(fecha)).days
        if 0 <= atras < dias:
            por_dia[dias - 1 - atras] += cantidad
    if dias >= DIAS_MINIMOS and sum(por_dia) > 0:
        return Consumo(float(mean(por_dia)), float(stdev(por_dia)), True, dias, tuple(por_dia))
    return Consumo(mu_estimado, sigma_estimado, False, dias, tuple(por_dia))
