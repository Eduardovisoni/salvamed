"""Coordenadas de las gráficas SVG del detalle (se dibujan en el servidor)."""

from datetime import date, datetime, time, timedelta

from app.consumo import GUATEMALA, Consumo


def _x(t: datetime, desde: datetime, hasta: datetime, ancho: float) -> float:
    total = max((hasta - desde).total_seconds(), 1)
    return round(min(max((t - desde).total_seconds() / total, 0), 1) * ancho, 2)


def existencias(
    serie: list[tuple[datetime, int]],
    planes: list[tuple[date, float | None, float | None]],
    marcas: list[tuple[datetime, str, str]],
    desde: datetime,
    hasta: datetime,
    ancho: int = 720,
    alto: int = 220,
) -> dict:
    """Existencia en escalones, R e IS vigentes cada día y marcas de pedidos y recepciones."""
    previo = 0
    puntos = []
    for fecha, existencia in serie:
        if fecha < desde:
            previo = existencia
        elif fecha <= hasta:
            puntos.append((fecha, existencia))
    valores = [previo] + [e for _, e in puntos]
    valores += [v for _, r, i in planes for v in (r, i) if v is not None]
    tope = max(max(valores or [1]) * 1.12, 1)

    def y(valor: float) -> float:
        return round(alto - valor / tope * alto, 2)

    ruta = [f"M0 {y(previo)}"]
    for fecha, existencia in puntos:
        ruta.append(f"H{_x(fecha, desde, hasta, ancho)} V{y(existencia)}")
    ruta.append(f"H{ancho}")

    def escalones(indice: int) -> str:
        partes = []
        for dia, *valores_dia in planes:
            valor = valores_dia[indice]
            if valor is None:
                continue
            inicio = datetime.combine(dia, time(), GUATEMALA)
            fin = inicio + timedelta(days=1)
            if fin < desde or inicio > hasta:
                continue
            x0, x1 = _x(max(inicio, desde), desde, hasta, ancho), _x(min(fin, hasta), desde, hasta, ancho)
            partes.append(f"M{x0} {y(valor)} H{x1}")
        return " ".join(partes)

    return {
        "ancho": ancho,
        "alto": alto,
        "tope": tope,
        "ruta": " ".join(ruta),
        "ruta_r": escalones(0),
        "ruta_is": escalones(1),
        "marcas": [
            {"x": _x(fecha, desde, hasta, ancho), "tipo": tipo, "texto": texto}
            for fecha, tipo, texto in marcas
            if desde <= fecha <= hasta
        ],
        "y_medio": y(tope / 2),
        "desde": desde,
        "hasta": hasta,
    }


def consumo_diario(consumo: Consumo, hoy: date, ancho: int = 600, alto: int = 150) -> dict:
    datos = list(consumo.por_dia) + [(hoy, consumo.hoy)]
    mu, sigma = consumo.mu_d or 0, consumo.sigma_d or 0
    tope = max([c for _, c in datos] + [mu + sigma, 1])
    paso = ancho / max(len(datos), 1)
    barras = []
    for i, (dia, cantidad) in enumerate(datos):
        h = cantidad / tope * alto
        barras.append(
            {
                "x": round(i * paso + paso * 0.15, 2),
                "y": round(alto - h, 2),
                "ancho": round(paso * 0.7, 2),
                "alto": round(h, 2),
                "hoy": dia == hoy,
                "etiqueta": f"{dia:%d/%m}: {cantidad}" + (" (hoy, día incompleto)" if dia == hoy else ""),
            }
        )

    def y_de(valor: float) -> float:
        return round(alto - min(valor, tope) / tope * alto, 2)

    return {
        "ancho": ancho,
        "alto": alto,
        "barras": barras,
        "con_media": consumo.disponible,
        "y_mu": y_de(mu),
        "y_arriba": y_de(mu + sigma),
        "y_abajo": y_de(max(mu - sigma, 0)),
        "primer_dia": datos[0][0],
    }
