# Simulación · «El Roble hoy» (empírico) contra «El Roble con SALVAMED»

Escrito el 30/10/2026. La simulación lleva dos inventarios que reciben **exactamente las mismas
solicitudes** de los servicios (misma semilla). Lo único que cambia es cómo se decide qué pedir.

## Los dos lados

| | El Roble hoy | El Roble con SALVAMED |
|---|---|---|
| Cuándo pide | Cuando alguien nota que **ya se va a acabar** (queda menos de la cuarta parte de «lo de siempre»), avisa de boca en boca: bodega → jefe de turno → doctor → gerente. El gerente pide **al día siguiente**. | Cuando la posición de inventario llega al **punto de reorden R** de la clase 12 (avisa por Telegram y espera «Pedido realizado»). |
| Cuánto pide | **«Lo de siempre»**: una cantidad por costumbre, entre 10 y 24 días de consumo según el producto. No sale de un cálculo. | La **EOQ** de la clase 12 (Q), redondeada a paquetes. |
| Cómo llega | Mensajería: **Q 35 por envío**, **2 a 3 días** según el proveedor. Si se avisan varios productos del mismo proveedor, van en un solo envío. | Igual: Q 35 por envío y el mismo plazo. |
| Si ya no hay | Si lo necesita Emergencia, UCI o Sala de operaciones: **compra de emergencia** automática (llega en 2 h, +50 % sobre el costo). Si lo pide otro servicio: se cuenta como faltante. | Exactamente la misma regla. El aviso por Telegram es solo informativo: no se pregunta nada. |

## Supuestos declarados (no son datos del Roble)

| Supuesto | Valor |
|---|---|
| Costo de un envío por mensajería | Q 35 (Configuración → Costo por pedido) |
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

## Resultados de prueba (63 días, se confirma cada aviso de SALVAMED al momento)

| Escenario · semilla | Roble hoy | Con SALVAMED |
|---|---|---|
| Mes normal · 2026 | Q 1,615 (26 envíos, 4 emergencias, 6 faltantes) | Q 1,256 (14 envíos, 2 emergencias, 9 faltantes) |
| Mes normal · 7 | Q 2,034 (25, 9, 10) | Q 1,617 (14, 4, 7) |
| Mes normal · 11 | Q 1,765 (24, 7, 2) | Q 1,028 (14, 0, 0) |
| Aumento · 7 | Q 2,560 (27, 14, 22) | Q 1,658 (15, 4, 7) |
| Aumento · 2026 | Q 2,140 (27, 9, 26) | Q 1,308 (15, 2, 11) |

Lectura: SALVAMED hace menos envíos y menos emergencias porque pide la cantidad económica (EOQ) en
el momento que marca R. A cambio guarda más inventario (más dinero parado y más costo de mantener),
que es justo el equilibrio de la clase 11: costo de pedir contra costo de mantener.

## Animación

Cada pedido que llega entra en un camión al lado que lo recibe (gris: Roble hoy; verde: SALVAMED;
rojo: emergencia), se detiene en cada estante y deja una caja con «+N». Mientras reparte, el reloj
se detiene y luego sigue solo.
