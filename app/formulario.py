import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Campo:
    nombre: str
    etiqueta: str
    simbolo: str = ""
    ayuda: str = ""
    texto: bool = False
    largo_max: int = 0
    entero: bool = False
    minimo: float | None = None
    minimo_incluido: bool = True
    maximo: float | None = None
    maximo_incluido: bool = True
    paso: str = "any"
    marcador: str = ""


@dataclass(frozen=True)
class Grupo:
    titulo: str
    descripcion: str
    campos: tuple[Campo, ...]


GRUPOS = (
    Grupo(
        "Medicamento",
        "Qué es y cuánto hay en bodega ahora.",
        (
            Campo("nombre", "Nombre", texto=True, largo_max=120, marcador="Ej. Ceftriaxona"),
            Campo(
                "unidad",
                "Se cuenta en",
                texto=True,
                largo_max=40,
                marcador="ampollas, viales, frascos…",
            ),
            Campo(
                "existencia",
                "Existencia actual",
                entero=True,
                minimo=0,
                paso="1",
                ayuda="En el día a día cambia sola con las salidas y los pedidos recibidos.",
            ),
        ),
    ),
    Grupo(
        "Demanda estimada",
        "Solo se usa mientras el medicamento tenga menos de 7 días de salidas registradas. "
        "Después, μD y σD se calculan solos con las salidas de los últimos 30 días.",
        (
            Campo(
                "consumo_promedio",
                "Consumo promedio por día",
                simbolo="μD",
                minimo=0,
                minimo_incluido=False,
                ayuda="Si el dato es semanal, divide la media entre 7.",
            ),
            Campo(
                "desviacion",
                "Desviación estándar por día",
                simbolo="σD",
                minimo=0,
                ayuda="Si el dato es semanal, divide la desviación entre √7.",
            ),
        ),
    ),
    Grupo(
        "Proveedor y costos",
        "Costos en quetzales.",
        (
            Campo(
                "tiempo_entrega",
                "Tiempo de entrega (días)",
                simbolo="L",
                minimo=0,
                minimo_incluido=False,
            ),
            Campo(
                "costo_pedido",
                "Costo de hacer un pedido",
                simbolo="S",
                minimo=0,
                minimo_incluido=False,
            ),
            Campo(
                "costo_mantener",
                "Costo de mantener una unidad al año",
                simbolo="H",
                minimo=0,
                minimo_incluido=False,
            ),
        ),
    ),
    Grupo(
        "Política",
        "Qué tan seguro queremos estar de no quedarnos sin el medicamento.",
        (
            Campo(
                "nivel_servicio",
                "Probabilidad de servicio (%)",
                simbolo="Z",
                minimo=50,
                maximo=100,
                maximo_incluido=False,
                ayuda="De 50 a 99.99. Para medicamentos críticos se recomienda 99.",
            ),
            Campo(
                "dias_habiles",
                "Días hábiles por año",
                entero=True,
                minimo=1,
                maximo=366,
                paso="1",
            ),
        ),
    ),
)

CAMPOS = tuple(c for g in GRUPOS for c in g.campos)

VALORES_NUEVO = {"nivel_servicio": "95", "dias_habiles": "365"}


def _texto_numero(valor: float) -> str:
    return f"{valor:.6f}".rstrip("0").rstrip(".")


def valores_de(medicamento) -> dict[str, str]:
    valores = {}
    for campo in CAMPOS:
        valor = getattr(medicamento, campo.nombre)
        valores[campo.nombre] = valor if campo.texto else _texto_numero(valor)
    return valores


def _numero(texto: str) -> float:
    texto = texto.strip().replace(" ", "")
    if "," in texto and "." not in texto:
        texto = texto.replace(",", ".")
    return float(texto)


def _error_de_rango(campo: Campo, valor: float) -> str | None:
    if campo.minimo is not None:
        minimo = _texto_numero(campo.minimo)
        if campo.minimo_incluido and valor < campo.minimo:
            return f"Debe ser {minimo} o más."
        if not campo.minimo_incluido and valor <= campo.minimo:
            return f"Debe ser mayor que {minimo}."
    if campo.maximo is not None:
        maximo = _texto_numero(campo.maximo)
        if campo.maximo_incluido and valor > campo.maximo:
            return f"Debe ser {maximo} o menos."
        if not campo.maximo_incluido and valor >= campo.maximo:
            return f"Debe ser menor que {maximo}."
    return None


def leer_formulario(formulario) -> tuple[dict[str, str], dict, dict[str, str]]:
    """Devuelve (valores escritos, datos limpios, errores por campo)."""
    valores, datos, errores = {}, {}, {}
    for campo in CAMPOS:
        crudo = str(formulario.get(campo.nombre, "")).strip()
        valores[campo.nombre] = crudo
        if not crudo:
            errores[campo.nombre] = "Este dato es obligatorio."
            continue
        if campo.texto:
            if len(crudo) > campo.largo_max:
                errores[campo.nombre] = f"Máximo {campo.largo_max} caracteres."
            else:
                datos[campo.nombre] = crudo
            continue
        try:
            numero = _numero(crudo)
        except ValueError:
            errores[campo.nombre] = "Escribe un número."
            continue
        if not math.isfinite(numero):
            errores[campo.nombre] = "Escribe un número."
            continue
        if campo.entero and not numero.is_integer():
            errores[campo.nombre] = "Debe ser un número entero."
            continue
        error = _error_de_rango(campo, numero)
        if error:
            errores[campo.nombre] = error
            continue
        datos[campo.nombre] = int(numero) if campo.entero else numero
    return valores, datos, errores


NOTA_MAX = 120
CANTIDAD_MAX = 100_000
SALAS = ("Sala de Emergencias", "Sala de traumas")


def leer_movimiento(formulario, con_sala: bool = False) -> tuple[dict[str, str], dict, dict[str, str]]:
    """Cantidad y nota de un movimiento. En las salidas la nota es la sala que lo pidió."""
    valores = {
        clave: str(formulario.get(clave, "")).strip()
        for clave in ("medicamento_id", "cantidad", "nota")
    }
    datos, errores = {}, {}
    try:
        cantidad = _numero(valores["cantidad"]) if valores["cantidad"] else None
    except ValueError:
        cantidad = math.nan
    if cantidad is None:
        errores["cantidad"] = "Escribe cuántas unidades."
    elif not math.isfinite(cantidad):
        errores["cantidad"] = "Escribe un número."
    elif not cantidad.is_integer():
        errores["cantidad"] = "Debe ser un número entero."
    elif cantidad < 1:
        errores["cantidad"] = "Debe ser 1 o más."
    elif cantidad > CANTIDAD_MAX:
        errores["cantidad"] = f"Máximo {CANTIDAD_MAX:,} unidades por movimiento."
    else:
        datos["cantidad"] = int(cantidad)
    if con_sala and valores["nota"] not in SALAS:
        errores["nota"] = "Elige una sala."
    elif len(valores["nota"]) > NOTA_MAX:
        errores["nota"] = f"Máximo {NOTA_MAX} caracteres."
    else:
        datos["nota"] = valores["nota"]
    return valores, datos, errores
