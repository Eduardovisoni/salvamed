"""Acceso mínimo: una sola clave (SALVAMED_CLAVE) para usar la app.

- Con clave: todo pide entrar, salvo /salud, /static y el webhook de Telegram
  (que tiene su propio secreto).
- Sin clave en la computadora: acceso libre, con un aviso arriba.
- Sin clave en línea (Railway): solo lectura; ningún cambio se acepta.
"""

import hashlib
import hmac

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from app.ajustes import ajustes
from app.web import pagina

COOKIE = "salvamed_sesion"
PUBLICAS = ("/salud", "/static/", "/telegram/webhook", "/entrar")
rutas = APIRouter()


def _firma(clave: str) -> str:
    return hmac.new(clave.encode(), b"salvamed-v2", hashlib.sha256).hexdigest()


def autorizado(request: Request) -> bool:
    clave = ajustes().clave
    if not clave:
        return True
    return hmac.compare_digest(request.cookies.get(COOKIE, ""), _firma(clave))


async def guardian(request: Request, siguiente):
    ruta = request.url.path
    if ruta.startswith(PUBLICAS):
        return await siguiente(request)
    a = ajustes()
    if not a.clave and a.en_linea and request.method not in ("GET", "HEAD"):
        return PlainTextResponse(
            "SALVAMED está en línea sin SALVAMED_CLAVE: solo lectura. Configura la clave para registrar cambios.",
            status_code=403,
        )
    if not autorizado(request):
        if request.method in ("GET", "HEAD"):
            return RedirectResponse(f"/entrar?siguiente={ruta}", status_code=303)
        return PlainTextResponse("Necesitas entrar con la clave.", status_code=401)
    return await siguiente(request)


def _siguiente_seguro(valor: str) -> str:
    return valor if valor.startswith("/") and not valor.startswith("//") else "/"


@rutas.get("/entrar")
def entrar(request: Request, siguiente: str = "/"):
    return pagina(request, "entrar.html", {"pestana": "", "siguiente": _siguiente_seguro(siguiente), "error": ""})


@rutas.post("/entrar")
async def validar(request: Request):
    formulario = await request.form()
    siguiente = _siguiente_seguro(str(formulario.get("siguiente", "/")))
    clave = ajustes().clave
    escrita = str(formulario.get("clave", ""))
    if not clave or not hmac.compare_digest(escrita.encode(), clave.encode()):
        return pagina(
            request, "entrar.html", {"pestana": "", "siguiente": siguiente, "error": "Clave incorrecta."}, 401
        )
    respuesta = RedirectResponse(siguiente, status_code=303)
    respuesta.set_cookie(
        COOKIE, _firma(clave), httponly=True, samesite="lax", secure=request.url.scheme == "https", max_age=60 * 60 * 12
    )
    return respuesta


@rutas.post("/salir")
def salir():
    respuesta = RedirectResponse("/entrar", status_code=303)
    respuesta.delete_cookie(COOKIE)
    return respuesta
