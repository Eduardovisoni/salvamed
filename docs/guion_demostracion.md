# Guion de demostración (10 a 12 minutos)

Antes de exponer:

- Arranca la app con el bot de pruebas en `polling` y el chat del expositor en `TELEGRAM_CHATS` (ver README).
  Sin Telegram, el mismo flujo funciona con el botón «Responder como encargado desde aquí».
- Ten Telegram abierto en el teléfono del expositor que hará de encargado de compras.
- En **Configuración** revisa el escenario base: 365 días (el sanatorio opera 24 horas), p = 95 %, tasa 25 %,
  S = Q35, L = 3 y la lista de servicios.

## 1. El problema (1 min)

«En el Sanatorio El Roble, cuando un medicamento se está acabando, el aviso pasa de boca en boca: de Emergencia o
Encamamiento a la farmacia y de ahí al encargado de compras. SALVAMED lo detecta con el modelo de la clase 12 y le avisa
directo por Telegram.»

## 2. Despacho y alta sin historial (2 min)

1. Con el catálogo de demostración cargado (Configuración → Datos), en **Despacho** elige el servicio (por ejemplo, Emergencia), busca «ceftri», presiona Enter, cambia la cantidad y
   confirma. «La farmacia interna registra qué servicio recibió qué medicamento; no hay datos de pacientes.»
2. **Inventario → Agregar medicamento**: solo nombre (Ketorolaco), presentación (30 mg/ml), unidad (ampolla) y
   existencia inicial 40. Guarda.
3. El detalle muestra **Datos pendientes**: falta el costo de compra para H y faltan días completos de consumo.
4. **Editar datos**: costo de compra Q4.50. Sigue pendiente solo el consumo: «sin despachos no se puede estimar μD ni σD;
   no inventamos números».

## 3. La simulación (6 a 7 min)

Datos de la semilla 2026, escenario «Sanatorio en un mes normal»:

1. **Simulación → Nueva corrida**: escenario «Sanatorio en un mes normal», semilla 2026, demora 0. «Es el sanatorio
   simulado; con la misma semilla las solicitudes son las mismas; no toca el inventario real.»
2. Pulsa **▶ Iniciar** y elige **×5**. Muestra cómo cada servicio manda a su personal a la farmacia interna (el contador
   de cada servicio sube), los despachos y cómo bajan las existencias. De noche se ven sobre todo Emergencia y las rondas
   de Encamamiento; la Sala de operaciones pide de día. Al terminar cada día la estimación se actualiza: después de dos
   días completos aparecen μD, σD, R e IS (provisionales).
3. El **día 4 a las 07:04**, la Ceftriaxona llega a su punto de reorden y el reloj se pausa: «esta pausa es para
   explicar; en el sanatorio real los servicios siguen pidiendo».
4. Lee el cálculo en pantalla: μD, σD, σL = σD·√L, Z, IS = Z·σL, R = μD·L + IS, la posición ≤ R redondeado hacia arriba y la EOQ redondeada hacia arriba.
5. Muestra el mensaje en el teléfono: medicamento, existencia, R, cantidad sugerida, plazo y que es una simulación.
6. El expositor pulsa **✅ Pedido realizado**. La pantalla registra quién confirmó y el reloj se reanuda solo.
7. Mientras corre L la existencia sigue bajando. El pedido llega el día 7 con 7 viales todavía en la farmacia.
8. Al llegar, la gráfica muestra el salto (línea verde) y en los eventos aparece «🚚 Llegó el pedido».
9. Pulsa **Siguiente evento** hasta el aviso del **Paracetamol (día 12)**. Durante L usa casi toda su reserva (llega con
   6 frascos de un inventario de seguridad de 20), pero el pedido llega antes de agotarse.
10. Sigue con **Siguiente evento** hasta el **Salbutamol (día 15, 23:54)**: esta vez faltan 4 monodosis antes de que llegue el
    pedido. «Un 95 % de servicio no es 100 %: es una probabilidad, no una garantía.»
11. Opcional: escenario «Aumento de casos respiratorios (salbutamol)». Es igual hasta el día 14; desde el día 15 los
    servicios piden 2.3 veces más salbutamol. Falta salbutamol los días 17 y 18 porque R todavía no recoge el aumento, y
    el **día 22** llega el aviso de aumento (de 6.9 a 18.3 por día, z = 5.7) con el nuevo R.

## 4. Cierre (1 a 2 min)

- **Pedidos**: el pedido aparece «realizado · en camino» y luego recibido. Pedir, pagar y recibir son hechos distintos;
  solo recibir sube la existencia.
- **Despachos**: lista de lo entregado a cada servicio; desde ahí se ve o se anula un despacho.
- Opcional: corrida con la misma semilla y demora de gestión de 24 h para comparar faltantes y pedidos.
- Recuerda los límites: datos simulados, demanda normal, plazo constante, y una probabilidad de servicio que no es una
  garantía.
- Si preguntan qué significa el 95 %: «es la probabilidad de que, cada vez que pedimos, el medicamento no se acabe
  mientras llega el pedido; por eso casi todas las solicitudes se atienden, como el 995 de cada 1000 del ejemplo de la
  clase».
