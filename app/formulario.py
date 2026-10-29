"""Campos y validación de los formularios (medicamento, configuración y proveedor).

El alta de un medicamento es sencilla: solo nombre y unidad son obligatorios.
Lo demás puede quedar vacío; el plan muestra qué resultados quedan pendientes.
"""

import math
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Campo:
    nombre: str
    etiqueta: str
    simbolo: str = ""
    ayuda: str = ""
    tipo: str = "numero"  # texto, numero, entero, fecha, porcentaje, proveedor
    obligatorio: bool = False
    largo_max: int = 0
    minimo: float | None = None
    minimo_incluido: bool = True
    maximo: float | None = None
    maximo_incluido: bool = True
    marcador: str = ""


@dataclass(frozen=True)
class Grupo:
    titulo: str
    descripcion: str
    campos: tuple[Campo, ...]
    solo_alta: bool = False


GRUPOS_MEDICAMENTO = (
    Grupo(
        "Medicamento",
        "Lo que se busca al despachar. La presentación distingue productos con el mismo nombre.",
        (
            Campo("nombre", "Nombre", tipo="texto", obligatorio=True, largo_max=120, marcador="Ej. Ceftriaxona"),
            Campo("presentacion", "Presentación", tipo="texto", largo_max=80, marcador="Ej. 1 g, 500 mg/5 ml"),
            Campo("unidad", "Se cuenta en", tipo="texto", obligatorio=True, largo_max=40, marcador="vial, ampolla, frasco…"),
            Campo("sku", "Código interno", tipo="texto", largo_max=40, ayuda="Opcional. Sirve para un lector de códigos."),
            Campo("clasificacion", "Clasificación", tipo="texto", largo_max=40, marcador="Regular, Controlado…"),
            Campo("proveedor_id", "Proveedor", tipo="proveedor"),
        ),
    ),
    Grupo(
        "Costo y paquete",
        "Costo de compra: lo que paga el sanatorio al proveedor por unidad. Sirve para H y para valorizar lo consumido.",
        (
            Campo("costo_compra", "Costo de compra por unidad (Q)", minimo=0, minimo_incluido=False),
            Campo("unidades_paquete", "Unidades por paquete", tipo="entero", minimo=1, ayuda="Para redondear el pedido a paquetes completos."),
        ),
    ),
    Grupo(
        "Existencia inicial",
        "Se registra como movimiento de apertura. Después, la existencia solo cambia con despachos, recepciones y ajustes.",
        (Campo("existencia_inicial", "Existencia al darlo de alta", tipo="entero", minimo=0),),
        solo_alta=True,
    ),
    Grupo(
        "Excepciones (opcional)",
        "Vacío = usar la configuración general. Solo para medicamentos que necesiten otro valor.",
        (
            Campo("nivel_servicio", "Probabilidad de servicio (%)", simbolo="p", minimo=50, maximo=100, maximo_incluido=False),
            Campo("tasa_mantenimiento", "Tasa anual de mantenimiento (%)", tipo="porcentaje", minimo=0, minimo_incluido=False, maximo=500),
            Campo("costo_mantener", "H documentado (Q por unidad al año)", simbolo="H", minimo=0, minimo_incluido=False,
                  ayuda="Si lo llenas, se usa en lugar de costo × tasa."),
            Campo("costo_pedido", "Costo por pedido (Q)", simbolo="S", minimo=0, minimo_incluido=False),
            Campo("tiempo_entrega", "Plazo de entrega (días de operación)", simbolo="L", minimo=0, minimo_incluido=False),
            Campo("dias_operacion_anio", "Días de operación al año", simbolo="A", tipo="entero", minimo=1, maximo=366),
            Campo("capacidad_maxima", "Capacidad de almacenamiento (unidades)", tipo="entero", minimo=1),
            Campo("vencimiento_proximo", "Vencimiento más próximo", tipo="fecha"),
        ),
    ),
)

GRUPOS_CONFIGURACION = (
    Grupo(
        "Calendario",
        "Le dice al sistema cuándo trabaja la farmacia. Con esto pasa la demanda de un día a la de un año "
        "(D = μD × A) y sabe qué días contar al sacar el promedio de consumo. El sanatorio atiende las 24 horas, "
        "todos los días.",
        (
            Campo("dias_operacion_anio", "Días de operación al año", simbolo="A", tipo="entero", obligatorio=True, minimo=1, maximo=366,
                  ayuda="Cuántos días al año se despacha. Se usa para la demanda anual (D = μD × A) y para T, los días entre un pedido y otro."),
            Campo("hora_apertura", "Inicio de la jornada (hora)", tipo="entero", obligatorio=True, minimo=0, maximo=23,
                  ayuda="Hora en que empieza el día de trabajo (0 = medianoche). Si un medicamento se da de alta después de esta hora, su consumo se empieza a contar al día siguiente."),
            Campo("hora_cierre", "Fin de la jornada (hora)", tipo="entero", obligatorio=True, minimo=1, maximo=24,
                  ayuda="Hora en que termina (24 = medianoche). La simulación reparte los pedidos de los servicios dentro de este horario."),
        ),
    ),
    Grupo(
        "Política",
        "Valores generales; cada medicamento puede tener su excepción.",
        (
            Campo("nivel_servicio", "Probabilidad de servicio (%)", simbolo="p", obligatorio=True, minimo=50, maximo=100, maximo_incluido=False,
                  ayuda="Probabilidad de que, cada vez que se pide, el medicamento no se acabe mientras llega el pedido (durante L). Por eso casi todas las solicitudes lo encuentran."),
            Campo("tasa_mantenimiento", "Tasa anual de mantenimiento (%)", tipo="porcentaje", obligatorio=True, minimo=0, minimo_incluido=False, maximo=500,
                  ayuda="Supuesto académico: H = costo de compra × tasa. No es un estándar ni un dato medido."),
            Campo("costo_pedido", "Costo por pedido (Q)", simbolo="S", obligatorio=True, minimo=0, minimo_incluido=False,
                  ayuda="Escenario base: Q35, solo el envío."),
            Campo("tiempo_entrega", "Plazo de entrega (días)", simbolo="L", obligatorio=True, minimo=0, minimo_incluido=False,
                  ayuda="Escenario base: 3 días, constante."),
        ),
    ),
    Grupo(
        "Estimación",
        "Aquí se decide cómo el sistema saca μD (cuánto se consume en promedio por día) y σD (cuánto varía de un "
        "día a otro) a partir de lo que se despacha, y cuándo avisa. Con esos dos datos se calculan R, IS y Q de la clase 12.",
        (
            Campo("ventana_dias", "Ventana de días completos", tipo="entero", obligatorio=True, minimo=2, maximo=365,
                  ayuda="Cuántos días hacia atrás se miran para sacar μD y σD. Hoy no cuenta porque el día no ha terminado. Más días dan un promedio más estable; menos días reaccionan más rápido a los cambios."),
            Campo("dias_aviso", "Avisar «pedir pronto» con días de anticipación", tipo="entero", obligatorio=True, minimo=0, maximo=60,
                  ayuda="Cuántos días antes de llegar al punto de reorden (R) el medicamento pasa a «Pedir pronto», para preparar el pedido con tiempo."),
            Campo("aumento_umbral", "Aviso de aumento si el consumo reciente es × veces el anterior", obligatorio=True, minimo=1, minimo_incluido=False, maximo=10,
                  ayuda="Avisa si en los últimos 7 días se consumió esta cantidad de veces más que antes (1.5 = 50 % más) y el cambio es claro, no casualidad. Sirve para notar que μD subió antes de que el medicamento se acabe."),
        ),
    ),
)

CAMPOS_PROVEEDOR = (
    Campo("nombre", "Nombre", tipo="texto", obligatorio=True, largo_max=120),
    Campo("tiempo_entrega", "Plazo de entrega (días)", simbolo="L", minimo=0, minimo_incluido=False),
    Campo("costo_envio", "Costo de envío por pedido (Q)", simbolo="S", minimo=0, minimo_incluido=False),
    Campo("articulos_por_envio", "Artículos que suelen ir juntos", tipo="entero", minimo=1,
          ayuda="Criterio de reparto del envío para la EOQ de cada artículo: S = envío ÷ este número."),
    Campo("contacto", "Contacto", tipo="texto", largo_max=160),
)


def campos(grupos) -> tuple[Campo, ...]:
    return tuple(c for g in grupos for c in g.campos)


def _texto_numero(valor: float) -> str:
    return f"{valor:.6f}".rstrip("0").rstrip(".")


def numero(texto: str) -> float:
    texto = texto.strip().replace(" ", "")
    if "," in texto and "." not in texto:
        texto = texto.replace(",", ".")
    else:
        texto = texto.replace(",", "")
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


def valor_texto(campo: Campo, valor) -> str:
    if valor is None:
        return ""
    if campo.tipo == "porcentaje":
        return _texto_numero(valor * 100)
    if campo.tipo == "fecha":
        return valor.isoformat()
    if campo.tipo in ("texto",):
        return str(valor)
    if campo.tipo == "proveedor":
        return str(valor)
    return _texto_numero(valor)


def valores_de(objeto, lista) -> dict[str, str]:
    return {c.nombre: valor_texto(c, getattr(objeto, c.nombre, None)) for c in lista}


def leer(formulario, lista, proveedores_validos=()) -> tuple[dict[str, str], dict, dict[str, str]]:
    """Devuelve (valores escritos, datos limpios, errores por campo). Vacío opcional → None."""
    valores, datos, errores = {}, {}, {}
    for campo in lista:
        crudo = str(formulario.get(campo.nombre, "")).strip()
        valores[campo.nombre] = crudo
        if not crudo:
            if campo.obligatorio:
                errores[campo.nombre] = "Este dato es obligatorio."
            elif campo.tipo == "texto":
                datos[campo.nombre] = ""
            else:
                datos[campo.nombre] = None
            continue
        if campo.tipo == "texto":
            if len(crudo) > campo.largo_max:
                errores[campo.nombre] = f"Máximo {campo.largo_max} caracteres."
            else:
                datos[campo.nombre] = crudo
            continue
        if campo.tipo == "proveedor":
            if crudo.isdigit() and int(crudo) in proveedores_validos:
                datos[campo.nombre] = int(crudo)
            else:
                errores[campo.nombre] = "Elige un proveedor de la lista."
            continue
        if campo.tipo == "fecha":
            try:
                datos[campo.nombre] = date.fromisoformat(crudo)
            except ValueError:
                errores[campo.nombre] = "Escribe una fecha válida."
            continue
        try:
            valor = numero(crudo)
        except ValueError:
            errores[campo.nombre] = "Escribe un número."
            continue
        if not math.isfinite(valor):
            errores[campo.nombre] = "Escribe un número."
            continue
        if campo.tipo == "entero" and not valor.is_integer():
            errores[campo.nombre] = "Debe ser un número entero."
            continue
        error = _error_de_rango(campo, valor)
        if error:
            errores[campo.nombre] = error
            continue
        if campo.tipo == "entero":
            datos[campo.nombre] = int(valor)
        elif campo.tipo == "porcentaje":
            datos[campo.nombre] = valor / 100
        else:
            datos[campo.nombre] = valor
    return valores, datos, errores
