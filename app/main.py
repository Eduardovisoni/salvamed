from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import rutas_config, rutas_operacion, rutas_simulacion, seguridad, tareas
from app.db import preparar_base
from app.web import CARPETA


@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    preparar_base()
    en_marcha = tareas.arrancar()
    yield
    for tarea in en_marcha:
        tarea.cancel()


app = FastAPI(title="SALVAMED", lifespan=ciclo_de_vida, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=CARPETA / "static"), name="static")
app.middleware("http")(seguridad.guardian)
app.include_router(seguridad.rutas)
app.include_router(rutas_operacion.rutas)
app.include_router(rutas_config.rutas)
app.include_router(rutas_simulacion.rutas)


@app.get("/salud")
def salud():
    return {"ok": True}
