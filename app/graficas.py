"""Coordenadas de las gráficas SVG del detalle (se dibujan en el servidor)."""

from datetime import date, datetime, time, timedelta

from app.consumo import GUATEMALA, Consumo


def _x(t: datetime, desde: datetime, hasta: datetime, ancho: float) -> float:
    total = max((hasta - desde).total_seconds(), 1)
    return round(min(max((t - desde).total_seconds() / total, 0), 1) * ancho, 2)


def existencias(
    serie: list[tuple[datetime, int]],
    pedidos: list[dict],
    desde: datetime,
    hasta: datetime,
    R: int | None = None,
    Q: int | None = None,
    ancho: int = 720,
    alto: int = 330,
) -> dict:
    """Gráfica del modelo clásico de inventario: nivel en escalones, línea del tamaño de pedido (Q),
    línea del nivel de reorden (R), marcas de hacer/recibir pedido y la llave del tiempo de entrega.

    `pedidos`: {"hecho": datetime, "llegada": datetime | None, "recibido": bool, "texto": str}.
    """
    izq, der, arriba, abajo = 64, 24, 30, 70
    w, h = ancho - izq - der, alto - arriba - abajo
    previo = 0
    puntos = []
    for fecha, existencia in serie:
        if fecha < desde:
            previo = existencia
        elif fecha <= hasta:
            puntos.append((fecha, existencia))
    valores = [previo] + [e for _, e in puntos] + [v for v in (R, Q) if v]
    tope = max(max(valores or [1]) * 1.15, 1)

    def x(t: datetime) -> float:
        return round(izq + _x(t, desde, hasta, w), 2)

    def y(valor: float) -> float:
        return round(arriba + h - valor / tope * h, 2)

    ruta = [f"M{izq} {y(previo)}"]
    for fecha, existencia in puntos:
        ruta.append(f"H{x(fecha)} V{y(existencia)}")
    ruta.append(f"H{izq + w}")

    marcas = []
    llaves = []
    for p in pedidos:
        hecho, llegada = p["hecho"], p.get("llegada")
        if desde <= hecho <= hasta:
            marcas.append({"x": x(hecho), "tipo": "pedido", "texto": p["texto"]})
        if llegada is not None and p.get("recibido") and desde <= llegada <= hasta:
            marcas.append({"x": x(llegada), "tipo": "recepcion", "texto": p["texto"]})
        if llegada is not None and hecho < hasta and llegada > desde:
            llaves.append({
                "x0": x(max(hecho, desde)), "x1": x(min(llegada, hasta)),
                "estimada": not p.get("recibido"), "texto": p["texto"],
            })
    if llaves:
        llaves[-1]["etiqueta"] = True

    # Las etiquetas de R y Q van del lado donde el nivel les deja más espacio.
    inicio_nivel = previo
    fin_nivel = puntos[-1][1] if puntos else previo

    def lado(valor):
        return "izq" if abs(inicio_nivel - valor) >= abs(fin_nivel - valor) else "der"

    return {
        "ancho": ancho,
        "alto": alto,
        "lado_r": lado(R) if R else "der",
        "lado_q": lado(Q) if Q else "izq",
        "izq": izq,
        "der": izq + w,
        "arriba": arriba,
        "base": arriba + h,
        "tope": tope,
        "ruta": " ".join(ruta),
        "y_r": y(R) if R else None,
        "y_q": y(Q) if Q else None,
        "r": R,
        "q": Q,
        "marcas": marcas,
        "llaves": llaves,
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
