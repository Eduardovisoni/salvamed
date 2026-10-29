# SALVAMED

Sistema de apoyo para prevenir el desabastecimiento de medicamentos en el Centro Médico El Roble.
Proyecto final de Investigación de Operaciones (UMG, sección B).

Para cada medicamento aplica el **modelo estocástico de inventario con revisión continua** (clase 12):
la demanda diaria es variable (distribución normal) y el tiempo de entrega es constante.

| Inciso | Fórmula |
| --- | --- |
| a) Cantidad económica de pedido | Q = √(2DS / H) |
| b) Punto de reorden | σL = √(L · σD²), R = μD · L + Z · σL |
| c) Número de pedidos por año | N = D / Q |
| d) Tiempo de reabastecimiento | T = Q / D × días hábiles |
| e) Costo total del inventario | CT = (D / Q) · S + (Q / 2) · H |
| f) Inventario de seguridad | IS = R − L · μD |
| g) Costo de mantenimiento del IS | H_IS = H · IS |

Con D = μD × días hábiles y Z según la probabilidad de servicio. El ejemplo del televisor Sony de la
clase da los mismos resultados (Q 540.37, R 339.27, CT 270.19, IS 59.27); está en `tests/test_modelo.py`.

## μD y σD salen de las salidas

Cada salida de bodega se registra. Con ellas se suma lo que salió cada día (hora de Guatemala) en los
últimos 30 días, contando hoy y contando como 0 los días sin salidas: μD es el promedio de esos totales
y σD su desviación estándar muestral. Así cada salida baja la existencia y también mueve R, Q e IS.

Un medicamento recién agregado usa el μD y σD estimados del formulario hasta tener 7 días de historial.

## Pantallas

1. **Registrar salida** (pantalla principal): el resumen del semáforo (pedir ya, pedir pronto, abastecidos y
   costo anual) y el formulario para anotar lo que sale de bodega. Muestra el antes y el ahora de la existencia,
   μD, σD, R, Q, IS y el estado. La última salida se puede deshacer.
2. **Medicamentos**: la tabla con el plan de cada uno, buscador y filtros por estado.
3. **Agregar o editar**: los datos de cada medicamento (L, S, H, probabilidad de servicio y el consumo estimado).
4. **Detalle**: registrar salida o recibir pedido, la gráfica del consumo diario, el plan completo inciso por
   inciso, la política del inventario y el historial de movimientos.

Los 30 medicamentos iniciales usan datos simulados (consumo, costos en quetzales y existencias) y arrancan con
30 días de salidas simuladas.

## Correrlo en la computadora

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m app
```

Abre http://localhost:8000. Sin `DATABASE_URL` usa un archivo SQLite (`salvamed.db`).

Pruebas: `.venv/bin/python -m pytest -q`

## Railway

Usa el Postgres de Railway a través de la variable `DATABASE_URL`. El arranque y la revisión de
salud (`/salud`) están en `railway.json`. La tabla y los datos iniciales se crean solos la primera vez.
