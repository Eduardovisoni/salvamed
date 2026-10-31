"""Ajustes que vienen de variables de entorno. Ningún secreto vive en el código.

SALVAMED_CLAVE              Clave para entrar. Obligatoria si la app está en línea.
SALVAMED_URL_PUBLICA        https://... de la app (para el webhook y el botón "Ver detalle").
TELEGRAM_TOKEN              Token del bot (de @BotFather).
TELEGRAM_MODO               simulado | polling | webhook. Sin token siempre es simulado.
TELEGRAM_CHATS              IDs de chat que reciben los avisos de operación, separados por coma.
TELEGRAM_CHATS_SIMULACION   IDs de chat para la simulación (si no, usa TELEGRAM_CHATS).
TELEGRAM_USUARIOS           IDs de usuario que pueden pulsar los botones (vacío: cualquiera del chat).
TELEGRAM_SECRETO_WEBHOOK    Texto secreto que Telegram manda en cada llamada al webhook.
SALVAMED_SIN_TAREAS         1 para no arrancar las tareas de fondo (pruebas).
SALVAMED_SEGUNDOS_POR_DIA   Cuántos segundos reales dura un "día" del plazo de entrega en
                            operación real. Normal: 86400. Para una demostración, p. ej. 10:
                            un plazo de 2 días se cumple en 20 s y el pedido se recibe solo.
"""

import os
from dataclasses import dataclass


def _lista(nombre: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in os.environ.get(nombre, "").split(",") if x.strip())


@dataclass(frozen=True)
class Ajustes:
    clave: str
    url_publica: str
    telegram_token: str
    telegram_modo: str
    chats: tuple[str, ...]
    chats_simulacion: tuple[str, ...]
    usuarios: tuple[str, ...]
    secreto_webhook: str
    en_linea: bool
    tareas: bool
    segundos_por_dia: float

    @property
    def demo_rapida(self) -> bool:
        """Plazos acelerados: los pedidos en camino se reciben solos al cumplirse."""
        return self.segundos_por_dia < 86400

    def chats_de(self, corrida_id: int | None) -> tuple[str, ...]:
        if corrida_id is not None and self.chats_simulacion:
            return self.chats_simulacion
        return self.chats


def _segundos_por_dia() -> float:
    try:
        valor = float(os.environ.get("SALVAMED_SEGUNDOS_POR_DIA", "86400"))
    except ValueError:
        return 86400.0
    return valor if valor > 0 else 86400.0


def ajustes() -> Ajustes:
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    modo = os.environ.get("TELEGRAM_MODO", "").strip().lower()
    if not token or modo not in ("polling", "webhook"):
        modo = "simulado"
    return Ajustes(
        clave=os.environ.get("SALVAMED_CLAVE", "").strip(),
        url_publica=os.environ.get("SALVAMED_URL_PUBLICA", "").strip().rstrip("/"),
        telegram_token=token,
        telegram_modo=modo,
        chats=_lista("TELEGRAM_CHATS"),
        chats_simulacion=_lista("TELEGRAM_CHATS_SIMULACION"),
        usuarios=_lista("TELEGRAM_USUARIOS"),
        secreto_webhook=os.environ.get("TELEGRAM_SECRETO_WEBHOOK", "").strip(),
        en_linea=any(os.environ.get(v) for v in ("RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID")),
        tareas=os.environ.get("SALVAMED_SIN_TAREAS", "") != "1",
        segundos_por_dia=_segundos_por_dia(),
    )
