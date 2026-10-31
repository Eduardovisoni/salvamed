# Simulación · «El Roble hoy» (empírico) contra «El Roble con SALVAMED»

Escrito el 30/10/2026. La simulación lleva dos inventarios que reciben **exactamente las mismas
solicitudes** de los servicios (misma semilla). Lo único que cambia es cómo se decide qué pedir.

## Los dos lados

| | El Roble hoy | El Roble con SALVAMED |
|---|---|---|
| Cuándo pide | Cuando alguien nota que **ya se va a acabar** (queda menos de la cuarta parte de «lo de siempre»), avisa de boca en boca: bodega → jefe de turno → doctor → gerente. El gerente pide **al día siguiente**. | Cuando la posición de inventario llega al **punto de reorden R** de la clase 12 (avisa por Telegram y espera «Pedido realizado»). |
| Cuánto pide | **«Lo de siempre»**: una cantidad por costumbre, entre 10 y 24 días de consumo según el producto. No sale de un cálculo. | La **EOQ** de la clase 12 (Q), redondeada a paquetes. |
| Envíos juntos | El gerente pide en una sola llamada todo lo que le avisaron de ese proveedor. | Cuando un medicamento llega a R, en el mismo pedido van los de ese proveedor que ya están en «pedir pronto» (les faltan 7 días o menos para R): el envío se paga una vez. |
| Cómo llega | Mensajería, **2 a 3 días** según el proveedor. Cada pedido cuesta **Q 150**: unos Q 100 de trabajo interno y unos Q 50 de flete. Si se avisan varios productos del mismo proveedor, van en un solo envío. | Igual: Q 150 por pedido y el mismo plazo. |
| Si ya no hay | Si lo necesita Emergencia, UCI o Sala de operaciones: **compra de emergencia** automática (llega en 2 h, +50 % sobre el costo). Si lo pide otro servicio: se cuenta como faltante. | Exactamente la misma regla. El aviso por Telegram es solo informativo: no se pregunta nada. |

## Supuestos declarados (no son datos del Roble)

| Supuesto | Valor |
|---|---|
| Costo por pedido (trabajo interno más flete) | Q 150 (Configuración → Costo por pedido) |
| S de cada medicamento en la EOQ | El envío repartido entre los medicamentos de su proveedor (viajan juntos): A y B, Q 150 ÷ 3 = Q 50; C, Q 150 ÷ 2 = Q 75 |
| Plazo de entrega | Proveedor A: 2 días; B y C: 3 días |
| «Lo de siempre» del Roble hoy | Ceftriaxona 12 días, paracetamol 20, salina 14, dexametasona 22, metoclopramida 11, salbutamol 18, insulina 24, adrenalina 10 |
| Cuándo avisan en bodega | Cuando queda menos de la cuarta parte de «lo de siempre» |
| Demora del aviso de boca en boca | 1 día hasta que el gerente pide |
| Emergencia | Solo si ya no hay y lo necesita un servicio crítico; llega en 2 h; recargo +50 % |
| Valor de lo que faltó | Costo de compra de esas unidades |
| Costo de mantener | Tasa anual de Configuración (25 %) sobre el dinero parado en bodega |

## Marcador

Envíos (cantidad × Q 150) · emergencias (recargo pagado) · unidades que faltaron (al costo) ·
dinero parado en bodega (promedio) · costo de mantener · total.

## Resultados de prueba (80 días, se confirma cada aviso de SALVAMED al momento)

Recalculados el 31/10/2026 con S = Q 150 y el código de `main`. Con envíos juntos y el costo por pedido
repartido entre los medicamentos que viajan juntos.

| Escenario · semilla | Roble hoy | Con SALVAMED |
|---|---|---|
| Mes normal · 2026 | Q 6,051 (34 envíos, 6 emergencias, 8 faltantes) | Q 2,610 (11 envíos, 2 emergencias, 1 faltante) |
| Mes normal · 7 | Q 6,217 (33, 11, 14) | Q 2,375 (10, 1, 5) |
| Mes normal · 11 | Q 5,944 (32, 10, 4) | Q 2,520 (11, 0, 0) |
| Aumento · 7 | Q 7,170 (35, 19, 33) | Q 2,532 (11, 1, 5) |
| Aumento · 2026 | Q 6,854 (35, 14, 42) | Q 2,703 (12, 1, 3) |

Dinero parado en bodega (promedio): Roble hoy Q 6,630 a Q 6,850; SALVAMED Q 14,470 a Q 15,880.

Con S = Q 35 y el mismo código, SALVAMED hacía 16 o 17 envíos y gastaba entre 35 % y 57 % de lo del Roble hoy.
Con S = Q 150 hace 10 a 12 envíos, guarda más inventario y gasta entre 35 % y 43 %: cada envío que se ahorra vale más.
Los envíos, emergencias y faltantes del Roble hoy no cambian, porque su cantidad no depende de S.

Cómo se llegó aquí (mes normal, semilla 2026):

| Versión de SALVAMED (con S = Q 35, cuando se escribió) | Envíos | Dinero parado | Total |
|---|---|---|---|
| Cada medicamento pide solo, con S = Q 35 completo | 18 | Q 10,862 | Q 1,520 |
| Junta en el mismo envío lo del proveedor en «pedir pronto» | 12 | Q 12,389 | Q 1,394 |
| Además reparte S entre los que viajan juntos | 17 | Q 8,501 | Q 1,247 |

Por qué la epinefrina pide tanto: es barata (H = Q 7.50 × 25 % = Q 1.88 por ampolla al año) y se
usa poco (≈ 1.2 por día, D ≈ 433 al año). Con S = Q 75, Q = √(2 · 433 · 75 / 1.88) ≈ 186
ampollas, unos 155 días. Es lo que dice la EOQ de la clase; la clase supone que el producto no vence,
así que si un medicamento vence antes de lo que cubre su pedido hay que ponerle tope (campos
«capacidad máxima» y «vencimiento más próximo» del medicamento).

Lectura: SALVAMED hace menos envíos y emergencias que el Roble hoy porque pide la cantidad económica
en el momento que marca R y junta lo del mismo proveedor. Guarda más del doble de inventario: es el
equilibrio de la clase 11 entre costo de pedir y costo de mantener.

## Animación

Cada pedido que llega entra en un camión al lado que lo recibe (gris: Roble hoy; verde: SALVAMED;
rojo: emergencia), se detiene en cada estante y deja una caja con «+N». Mientras reparte, el reloj
se detiene y luego sigue solo.
