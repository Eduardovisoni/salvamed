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

## 3. La simulación (7 a 8 min)

Datos de la semilla 2026, escenario «Sanatorio en un mes normal», recargo de emergencia 20 %:

1. **Simulación → Nueva corrida**: escenario «Sanatorio en un mes normal», semilla 2026, demora 0, recargo 20 %. «Es el
   sanatorio simulado; con la misma semilla las solicitudes son las mismas; no toca el inventario real.»
2. Presenta las dos pantallas: «A la izquierda, El Roble hoy: pide cuando se acaba y a ojo. A la derecha, con SALVAMED:
   pide al llegar a R la cantidad Q. Las dos reciben exactamente las mismas solicitudes de los servicios.»
3. Pulsa **▶ Iniciar** y elige **×5**. Muestra cómo cada servicio pide (el contador de cada servicio sube), los
   despachos y cómo bajan las existencias en los dos lados. Al terminar cada día la estimación se actualiza: después de
   dos días completos aparecen μD, σD, R e IS (provisionales).
4. El **día 4 a las 07:04**, la Ceftriaxona llega a su punto de reorden y el reloj se pausa: «esta pausa es para
   explicar; en el sanatorio real los servicios siguen pidiendo». El Roble todavía no hace nada: aún le quedan viales.
5. Lee el cálculo en pantalla: μD, σD, σL = σD·√L, Z, IS = Z·σL, R = μD·L + IS, la posición ≤ R redondeado hacia arriba y
   la EOQ redondeada hacia arriba.
6. Muestra el mensaje en el teléfono: medicamento, existencia, R, cantidad sugerida, plazo y que es una simulación.
7. El expositor pulsa **✅ Pedido realizado**. En los eventos se lee: «el proveedor dice 3 días; esta vez tarda 3.1». El
   plazo no es fijo: cada pedido tarda entre 2 y 4 días.
8. El pedido llega el **día 7** con 7 viales todavía en el estante. En la gráfica, la línea verde es SALVAMED y la morada
   es El Roble con las mismas solicitudes.
9. Sigue con **Siguiente evento**. El **día 8 a las 22:23** a El Roble se le acaba la Ceftriaxona: recién ahí pide 60 a
   ojo, que llegan el día 12. Mientras tanto los servicios siguen pidiendo: son 23 viales de
   emergencia, a precio con recargo. La fila se pinta de rojo y el marcador de Emergencias sube.
10. **Paracetamol (aviso del día 12)**: ese pedido tarda solo 2 días y llega con 17 frascos. A El Roble se le acaba el
    día 16 y compra 40 frascos de emergencia antes de que llegue su pedido.
11. **Salbutamol (aviso del día 15, 23:54)**: el proveedor tarda 3.9 días en vez de 3 y SALVAMED se queda sin
    salbutamol: compra 12 monodosis de emergencia. «Un 95 % de servicio no es 100 %, y el plazo puede atrasarse. Pero
    compara: El Roble compra de emergencia cada vez que pide, porque pide cuando ya no hay.»
12. Abre **Cuenta por medicamento** y lee la fila de Ceftriaxona y la de totales: pedidos (envíos × Q35), mantener (lo que
    estuvo guardado) y emergencias (unidades × costo × 20 %). Arriba, el ahorro: a los 30 días, **Q352.49 (41 %)**, con
    1,716 unidades pedidas por los servicios en los dos lados.
13. Opcional: escenario «Aumento de casos respiratorios (salbutamol)». Desde el día 15 los servicios piden 2.3 veces más
    salbutamol. Falta salbutamol los días 17 a 19 porque R todavía no recoge el aumento, y el **día 22** llega el aviso de
    aumento (de 6.9 a 16.3 por día, z = 4.7) con el nuevo R. Aun así, a los 30 días el ahorro es de Q451.64.

Si preguntan «¿y si la emergencia no costara más?»: con recargo 0 % El Roble sale más barato en pedir y mantener,
porque guarda menos. El ahorro de SALVAMED es justamente no tener que comprar de emergencia lo que se acabó. Se puede
mostrar en vivo creando la corrida con recargo 0.

## 4. Cierre (1 a 2 min)

- **Pedidos**: el pedido aparece «realizado · en camino» y luego recibido. Pedir, pagar y recibir son hechos distintos;
  solo recibir sube la existencia.
- **Costos**: costo de lo consumido por servicio y compras. Los importes son simulados.
- Opcional: corrida con la misma semilla y demora de gestión de 24 h para comparar faltantes y pedidos.
- Recuerda los límites: datos simulados, demanda normal, la fórmula usa el plazo promedio aunque cada pedido tarde
  distinto, la regla del Roble y el recargo de emergencia son supuestos del grupo, y una probabilidad de servicio que no es
  una garantía.
- Si preguntan qué significa el 95 %: «es la probabilidad de que, cada vez que pedimos, el medicamento no se acabe
  mientras llega el pedido; por eso casi todas las solicitudes se atienden, como el 995 de cada 1000 del ejemplo de la
  clase».
