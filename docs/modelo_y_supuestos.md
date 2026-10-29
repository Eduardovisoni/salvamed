# Modelo, variables y supuestos

SALVAMED aplica el **modelo estocástico de inventario con revisión continua** de la clase 12 a la farmacia interna
del Sanatorio El Roble: la demanda diaria es variable (aproximadamente normal) y el tiempo de entrega es constante.
La demanda de cada medicamento es lo que la farmacia **despacha a los servicios** del sanatorio. Las mismas funciones
sirven para la operación y para la simulación.

## El sanatorio

- Es un hospital privado en Jutiapa que atiende **las 24 horas** («Siempre abierto» en Facebook; «24 Hrs.» en TuSalud).
- Servicios publicados: atención de emergencias, hospitalización, cuidados intensivos, cirugía general, sistema de
  ambulancias y especialista en traumatología. Con eso, la lista inicial de servicios que reciben medicamentos es:
  Emergencia, Encamamiento, Cuidados Intensivos, Sala de operaciones, Traumatología y Ambulancia. Se edita en
  Configuración y conviene revisarla con la farmacia.
- SALVAMED no registra pacientes, cobros ni facturas: solo qué servicio recibió qué medicamentos y cuándo.

## Variables (todas en base diaria)

| Símbolo | Qué es | De dónde sale en SALVAMED |
| --- | --- | --- |
| μD | Consumo promedio por día | Promedio de lo despachado a todos los servicios en los últimos 30 días **completos** (configurable) |
| σD | Desviación estándar muestral del consumo diario | Desviación de esos mismos días |
| A | Días de operación por año | 365: el sanatorio opera todos los días (fuentes arriba) |
| D | Demanda anual estimada = μD · A | Calculada; supone que el consumo reciente es representativo del año |
| S | Costo de gestionar un pedido (Q por pedido) | Escenario base: Q35, **solo el envío** (referencia aportada por el grupo, no verificada en el sanatorio) |
| H | Costo de mantener una unidad un año (Q por unidad al año) | H documentado, o costo de compra × tasa anual. Tasa académica por defecto: 25 % (supuesto editable) |
| L | Plazo de entrega constante, en días | Del medicamento, del proveedor o general. Escenario base: 3 días |
| p | Probabilidad de servicio objetivo | Configuración general (95 %) con excepción por medicamento (por ejemplo, 99 % para medicamentos críticos) |
| Z | Valor de la normal estándar para p | Calculado |

S y H **no salen del consumo**. S es lo que cuesta pedir (aquí, el envío); H es lo que cuesta tener una unidad guardada
un año (capital inmovilizado, espacio, cadena de frío, vencimientos…). Por eso se configuran aparte.

## Fórmulas (incisos de la clase)

| Inciso | Fórmula |
| --- | --- |
| a) Cantidad económica de pedido | Q = √(2DS / H) |
| b) Punto de reorden | σL = √(L · σD²) = σD · √L;  R = μD · L + Z · σL |
| c) Número de pedidos por año | N = D / Q |
| d) Tiempo de reabastecimiento | T = Q / D × A: días **entre pedidos**. No es L, que es lo que tarda el proveedor |
| e) Costo total del inventario | CT = (D / Q) · S + (Q / 2) · H |
| f) Inventario de seguridad | IS = Z · σL = R − L · μD |
| g) Costo de mantenimiento del IS | H_IS = H · IS |
| h) Política | Cuando la posición de inventario baje a R, pedir Q (redondeada a paquetes) |

- CT es el costo de **pedir y mantener el inventario de ciclo** según la fórmula de la clase. No es el gasto total de
  la farmacia y no incluye la compra de los medicamentos ni salarios. H_IS se muestra aparte.
- Los cálculos se guardan sin redondear. Para pedir se sugiere la EOQ redondeada hacia arriba a paquetes completos y se
  muestra la diferencia; la cantidad redondeada ya no es exactamente la óptima.
- El ejemplo del televisor Sony de la clase (Q 540.37, R 339.27, CT 270.19, IS 59.27) está en `tests/test_modelo.py`
  con los datos originales.

## Cómo se aplica

- **Despacho**: la farmacia elige el servicio y los medicamentos; la existencia baja al confirmar y queda el costo de lo
  consumido (costo promedio ponderado). Un despacho equivocado se anula con un movimiento, no se borra.
- **Consumo por servicio**: el modelo usa el total del sanatorio; el detalle de cada medicamento muestra cuánto pidió cada
  servicio, solo para explicar de dónde viene el consumo.
- **Posición de inventario** = existencia + lo pedido y aún no recibido (pedidos marcados como realizados). Un aviso que
  el encargado no ha confirmado no cuenta como mercancía comprada.
- Cuando la posición llega a R se crea el pedido (pendiente de gestionar) y se avisa por Telegram. Si ya hay un pedido
  abierto para ese medicamento no se crea otro; sí se avisa una vez si el pedido en camino ya no alcanza o si se atrasó.
- Si varios medicamentos del mismo proveedor llegan a R en la misma revisión, van en un solo pedido: el envío se cuenta
  una vez. Para la EOQ de cada artículo, S se reparte entre los artículos que suelen ir juntos (dato del proveedor, 1 por
  defecto). Es un criterio aproximado, no una optimización conjunta.
- **Estimación actualizada**: cada despacho cambia la existencia al instante; μD, σD y por tanto R, IS y Q cambian al
  terminar cada día, porque solo cuentan días completos. Lo de hoy se muestra aparte.
- **Aumento de consumo**: se compara el promedio de los últimos 7 días completos con el de los anteriores (se necesitan
  al menos 14). Se avisa si es 1.5 veces mayor o más (configurable) **y** la diferencia es de al menos 3 errores
  estándar, z = (reciente − anterior) / (s · √(1/7 + 1/n)). El aviso sale una vez por semana como máximo, con el consumo
  previsto en L, la proyección anual y los nuevos R, IS y Q. Es una prueba simple: con poco consumo puede dar falsas
  alarmas o tardar en notar un cambio, no diagnostica enfermedades y no elimina picos reales.

## La simulación

- Seis servicios piden ocho medicamentos a la farmacia interna, cada uno con su horario: rondas de encamamiento a las 6,
  14 y 22 horas; cirugías programadas de 7 a 17 horas y menos el fin de semana; más emergencias por la tarde y la noche.
  Hay días tranquilos y días con muchos ingresos.
- Los medicamentos, cantidades, horarios y reparto entre servicios son **simulados**. Solo los nombres de los servicios
  vienen de lo que publica el sanatorio.
- A ×1, 1 segundo real son 20 minutos simulados (un día ≈ 72 s). La velocidad no cambia los resultados: con la misma
  semilla las solicitudes son las mismas.
- La corrida arranca **sin historia**: el plan aparece con 2 días completos y es provisional mientras haya pocos días.
- La pausa al llegar a R es un recurso de exposición. La respuesta del encargado se supone inmediata en tiempo virtual,
  salvo que se elija una demora de gestión para comparar corridas.

## Datos de la demostración

- Solo los **nombres** de los medicamentos están contextualizados en el sanatorio. Existencias, plazos, totales
  semanales y costos son **simulados**. El sanatorio no entregó costos ni historia de consumo.
- `SALVAMED en orden.csv` trae `precio_unitario_q` sin decir qué es. La demostración lo toma como costo de compra
  simulado (el archivo lo usa para valorar el inventario y presupuestar pedidos). Es un supuesto.
- Sus columnas `sem_1`…`sem_8` son totales semanales. Mientras no haya 7 días diarios, μD y σD se derivan de ellos:
  μD = μS / 7 y σD = σS / √7, suponiendo días independientes. No se convierten en consumos diarios observados.
- Las columnas que el archivo ya trae calculadas (punto de reorden, stock de seguridad, pedido sugerido…) no se importan:
  SALVAMED las recalcula con sus propios parámetros, así que los números pueden diferir.

## Límites

- El modelo supone demanda diaria independiente, aproximadamente normal, y plazo constante. No garantiza que nunca
  falte: p es una probabilidad de no agotarse durante L, **no** el porcentaje exacto de pacientes atendidos.
- Con pocos días la estimación es provisional (se indica cuántos días la sustentan). Ningún número de días garantiza
  confiabilidad.
- Lo que un servicio necesitó y no se le pudo despachar por falta de existencia no queda registrado en la operación,
  así que la demanda observada puede subestimar la real. En la simulación sí se cuenta como «unidades que no se pudieron
  despachar».
- Recalcular estadísticos con datos nuevos es una estimación actualizada, no aprendizaje automático ni inteligencia
  artificial.
- Los resultados de la simulación son escenarios: no son ahorros reales del sanatorio.
