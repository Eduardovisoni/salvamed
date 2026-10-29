# Guion de demostración (10 a 12 minutos)

Antes de exponer:

- Arranca la app con el bot de pruebas en `polling` y el chat del expositor en `TELEGRAM_CHATS` (ver README).
  Sin Telegram, el mismo flujo funciona con el botón «Responder como encargado desde aquí».
- Ten Telegram abierto en el teléfono del expositor que hará de encargado de compras.
- En **Configuración** revisa el escenario base: A = 365, p = 95 %, tasa 25 %, S = Q35 y L = 3.

## 1. El problema (1 min)

«Hoy el aviso de que algo se acaba pasa de boca en boca hasta el encargado de compras. SALVAMED lo detecta con el modelo
de la clase 12 y le avisa directo por Telegram.»

## 2. Alta sin historial (2 min)

1. **Inventario → Agregar medicamento**: escribe solo nombre (Ketorolaco), presentación (30 mg/ml), unidad (ampolla) y
   existencia inicial 40. Guarda.
2. El detalle muestra **Datos pendientes**: falta el costo de compra para H y faltan días completos de ventas.
3. **Editar datos**: costo de compra Q4.50 y precio Q6.10. Sigue pendiente solo el consumo: «sin ventas no se puede
   estimar μD ni σD; no inventamos números».
4. En la simulación se verá cómo un medicamento sin historia obtiene su plan día a día.

## 3. La simulación (6 a 7 min)

1. **Simulación → Nueva corrida**: escenario «Farmacia en un mes normal», semilla 2026, demora 0. Explica: «es una
   farmacia simulada; con la misma semilla las ventas son las mismas; no toca el inventario real».
2. Pulsa **▶ Iniciar** y elige **×5**. Muestra los clientes que entran, el ticket de cada venta y cómo bajan las
   existencias. Al cierre de cada día la estimación se actualiza: después de dos días completos aparecen μD, σD, R e IS.
3. Alrededor del día 4, Ceftriaxona llega a su punto de reorden. El reloj se pausa: «esta pausa es para explicar; en una
   farmacia real se sigue vendiendo».
4. Lee el cálculo en pantalla: μD, σD, σL = σD·√L, Z, IS = Z·σL, R = μD·L + IS, la posición ≤ R y la EOQ redondeada.
5. Muestra el mensaje en el teléfono: medicamento, existencia, R, cantidad sugerida, plazo y que es una simulación.
6. El expositor pulsa **✅ Pedido realizado**. La pantalla registra quién confirmó y el reloj se reanuda solo.
7. Mientras corre L la existencia sigue bajando. Con la semilla 2026, la Ceftriaxona llega al día 7 con 15 unidades,
   todavía sobre su inventario de seguridad (10.3).
8. Al cumplirse los 3 días llega el pedido: salto en la gráfica (línea verde) y «🚚 Llegó el pedido» en los eventos.
9. Pulsa **Siguiente evento** hasta el aviso de la insulina (día 9) o de la dexametasona (día 13). En esta semilla las
   dos usan parte de su inventario de seguridad (bajan a 2 de 3.0 y a 10 de 10.2) y el pedido llega a tiempo. Elige el
   medicamento en los estantes para verlo en la gráfica con R e IS de cada día.
10. Opcional: escenario «Aumento de demanda de salbutamol». Desde el día 9 se vende 2.3 veces más: se ve el aviso de
    aumento, cómo suben R e IS al actualizarse la estimación, y que puede haber faltantes.

## 4. Cierre (1 a 2 min)

- **Pedidos**: el pedido aparece «realizado · en camino» y luego recibido. Pedir, pagar y recibir son hechos distintos;
  solo recibir sube la existencia.
- **Dinero**: ventas, costo de lo vendido y margen bruto (no es utilidad). Los importes son simulados.
- Opcional: corrida con la misma semilla y demora de gestión de 24 h para comparar faltantes y pedidos.
- Recuerda los límites: datos simulados, demanda normal, plazo constante, y una probabilidad de servicio que no es una
  garantía.
