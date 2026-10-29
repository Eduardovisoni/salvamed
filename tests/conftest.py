import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "prueba.db")
os.environ["SALVAMED_SIN_TAREAS"] = "1"
for variable in ("SALVAMED_CLAVE", "TELEGRAM_TOKEN", "TELEGRAM_MODO", "TELEGRAM_CHATS", "TELEGRAM_CHATS_SIMULACION",
                 "TELEGRAM_USUARIOS", "TELEGRAM_SECRETO_WEBHOOK", "SALVAMED_URL_PUBLICA", "RAILWAY_ENVIRONMENT"):
    os.environ.pop(variable, None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import Base, Configuracion, motor, preparar_base  # noqa: E402
from app.main import app  # noqa: E402

preparar_base()


@pytest.fixture(autouse=True)
def base_limpia():
    """Cada prueba empieza con la base vacía y la configuración por defecto."""
    with motor.begin() as conexion:
        for tabla in reversed(Base.metadata.sorted_tables):
            conexion.execute(tabla.delete())
    with Session(motor) as db:
        db.add(Configuracion(id=1))
        db.commit()
    yield


@pytest.fixture
def db():
    with Session(motor) as sesion:
        yield sesion


@pytest.fixture(scope="session")
def cliente():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def telegram_real(monkeypatch):
    """Ajustes como si hubiera bot real, con un chat y un usuario autorizados (el transporte sigue siendo simulado)."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:prueba")
    monkeypatch.setenv("TELEGRAM_MODO", "polling")
    monkeypatch.setenv("TELEGRAM_CHATS", "555")
    monkeypatch.setenv("TELEGRAM_USUARIOS", "77")
