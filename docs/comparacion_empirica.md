# Simulación · «El Roble hoy» (empírico) contra «El Roble con SALVAMED»

Escrito el 30/10/2026. La simulación lleva dos inventarios que reciben **exactamente las mismas
solicitudes** de los servicios (misma semilla). Lo único que cambia es cómo se decide qué pedir.

## Los dos lados

| | El Roble hoy | El Roble con SALVAMED |
|---|---|---|
| Cuándo pide | Cuando alguien nota que **ya se va a acabar** (queda menos de la cuarta parte de «lo de siempre»), avisa de boca en boca: bodega → jefe de turno → doctor → gerente. El gerente pide **al día siguiente**. | Cuando la posición de inventario llega al **punto de reorden R** de la clase 12 (avisa por Telegram y espera «Pedido realizado»). |
| Cuánto pide | **«Lo de siempre»**: una cantidad por costumbre, entre 10 y 24 días de consumo según el producto. No sale de un cálculo. | La **EOQ** de la clase 12 (Q), redondeada a paquetes. |
| Envíos juntos | El gerente pide en una sola llamada todo lo que le avisaron de ese proveedor. | Cuando un medicamento llega a R, en el mismo pedido van los de ese proveedor que ya están en «pedir pronto» (les faltan 7 días o menos para R): el envío se paga una vez. |
| Cómo llega | Mensajería: **Q 35 por envío**, **2 a 3 días** según el proveedor. Si se avisan varios productos del mismo proveedor, van en un solo envío. | Igual: Q 35 por envío y el mismo plazo. |
| Si ya no hay | Si lo necesita Emergencia, UCI o Sala de operaciones: **compra de emergencia** automática (llega en 2 h, +50 % sobre el costo). Si lo pide otro servicio: se cuenta como faltante. | Exactamente la misma regla. El aviso por Telegram es solo informativo: no se pregunta nada. |

## Supuestos declarados (no son datos del Roble)

| Supuesto | Valor |
|---|---|
| Costo de un envío por mensajería | Q 35 (Configuración → Costo por pedido) |
| S de cada medicamento en la EOQ | El envío repartido entre los medicamentos de su proveedor (viajan juntos): A y B, Q 35 ÷ 3 ≈ Q 11.67; C, Q 35 ÷ 2 = Q 17.50 |
| Plazo de entrega | Proveedor A: 2 días; B y C: 3 días |
| «Lo de siempre» del Roble hoy | Ceftriaxona 12 días, paracetamol 20, salina 14, dexametasona 22, metoclopramida 11, salbutamol 18, insulina 24, adrenalina 10 |
| Cuándo avisan en bodega | Cuando queda menos de la cuarta parte de «lo de siempre» |
| Demora del aviso de boca en boca | 1 día hasta que el gerente pide |
| Emergencia | Solo si ya no hay y lo necesita un servicio crítico; llega en 2 h; recargo +50 % |
| Valor de lo que faltó | Costo de compra de esas unidades |
| Costo de mantener | Tasa anual de Configuración (25 %) sobre el dinero parado en bodega |

## Marcador

Envíos (cantidad × Q 35) · emergencias (recargo pagado) · unidades que faltaron (al costo) ·
dinero parado en bodega (promedio) · costo de mantener · total.

## Resultados de prueba (80 días, se confirma cada aviso de SALVAMED al momento)

Con envíos juntos y el costo del envío repartido entre los medicamentos que viajan juntos.

| Escenario · semilla | Roble hoy | Con SALVAMED |
|---|---|---|
| Mes normal · 2026 | Q 2,189 (34 envíos, 6 emergencias, 8 faltantes) | Q 1,247 (17 envíos, 2 emergencias, 3 faltantes) |
| Mes normal · 7 | Q 2,521 (33, 11, 14) | Q 1,102 (16, 1, 5) |
| Mes normal · 11 | Q 2,366 (32, 10, 4) | Q 1,081 (17, 0, 0) |
| Aumento · 7 | Q 3,323 (35, 19, 33) | Q 1,119 (16, 1, 5) |
| Aumento · 2026 | Q 3,017 (35, 14, 42) | Q 1,272 (17, 2, 5) |

Dinero parado en bodega (promedio): Roble hoy Q 6,600 a Q 6,900; SALVAMED Q 7,950 a Q 8,860.

Cómo se llegó aquí (mes normal, semilla 2026):

| Versión de SALVAMED | Envíos | Dinero parado | Total |
|---|---|---|---|
| Cada medicamento pide solo, con S = Q 35 completo | 18 | Q 10,862 | Q 1,520 |
| Junta en el mismo envío lo del proveedor en «pedir pronto» | 12 | Q 12,389 | Q 1,394 |
| Además reparte S entre los que viajan juntos | 17 | Q 8,501 | Q 1,247 |

Por qué la epinefrina pide tanto: es barata (H = Q 7.50 × 25 % = Q 1.88 por ampolla al año) y se
usa poco (≈ 1.2 por día, D ≈ 433 al año). Con S = Q 17.50, Q = √(2 · 433 · 17.5 / 1.88) ≈ 90
ampollas, unos 75 días. Es lo que dice la EOQ de la clase; la clase supone que el producto no vence,
así que si un medicamento vence antes de lo que cubre su pedido hay que ponerle tope (campos
«capacidad máxima» y «vencimiento más próximo» del medicamento).

Lectura: SALVAMED hace menos envíos y emergencias que el Roble hoy porque pide la cantidad económica
en el momento que marca R y junta lo del mismo proveedor. Guarda algo más de inventario: es el
equilibrio de la clase 11 entre costo de pedir y costo de mantener.

## Animación

Cada pedido que llega entra en un camión al lado que lo recibe (gris: Roble hoy; verde: SALVAMED;
rojo: emergencia), se detiene en cada estante y deja una caja con «+N». Mientras reparte, el reloj
se detiene y luego sigue solo.
