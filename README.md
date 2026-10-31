# SALVAMED

Apoyo para anticipar la reposición de medicamentos en la farmacia interna del **Sanatorio El Roble** (Jutiapa).
Proyecto final de Investigación de Operaciones (Universidad Mariano Gálvez, sección B).

El sanatorio es un hospital privado que atiende las 24 horas. Según lo que publica (ficha de
[TuSalud](https://tusalud.com.gt/centros/sanatorio-el-roble-jutiapa) y su página de Facebook), tiene atención de
emergencias, hospitalización, cuidados intensivos, cirugía general, sistema de ambulancias y especialista en
traumatología. La farmacia interna despacha medicamentos a esos servicios.

Hoy el aviso de que algo se está acabando pasa de persona en persona hasta llegar al encargado de compras.
SALVAMED detecta la necesidad con el **modelo estocástico de inventario de revisión continua (clase 12)**,
manda el aviso **directo por Telegram** al encargado y registra su respuesta y la recepción del pedido.
El encargado sigue comprando por su canal habitual: el botón del bot solo registra que lo hizo.

Una sola aplicación con dos modos separados:

| Modo | Para qué | Datos |
| --- | --- | --- |
| **Operación** | Despacho a servicios (Emergencia, Encamamiento, …), inventario, pedidos, recepción y despachos | Arranca vacía. Se carga con el formulario, la importación o el botón de demostración |
| **Simulación** | El sanatorio en tiempo acelerado: los servicios piden a la farmacia las 24 horas y se ve el flujo completo con Telegram | Corridas con semilla; nunca tocan la operación |

No se registran datos de pacientes, cobros ni facturación: un despacho dice qué servicio recibió qué medicamentos.

Más detalle:
- [docs/modelo_y_supuestos.md](docs/modelo_y_supuestos.md): fórmulas, D, S, H, L, nivel de servicio, supuestos y límites.
- [docs/guion_demostracion.md](docs/guion_demostracion.md): guion corto para la exposición.

## Correrlo en Windows (PowerShell)

Requiere Python 3.13 o más nuevo.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m app
```

Abre http://localhost:8000. En **Configuración** están los servicios que reciben medicamentos (editables). Sin `DATABASE_URL` usa el archivo `salvamed.db` (SQLite) en la carpeta del proyecto.
Si PowerShell no deja activar el entorno, usa `.\.venv\Scripts\python.exe -m app` sin activarlo.

Pruebas:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Para regenerar la plantilla y el ejemplo de importación (`datos/`):

```powershell
.\.venv\Scripts\python.exe -m app.plantilla
```

## Variables de entorno

Ninguna es obligatoria para usarla en la computadora. Ningún secreto va en el código ni en Git.

| Variable | Para qué |
| --- | --- |
| `DATABASE_URL` | Postgres (Railway la pone sola). Sin ella, SQLite local |
| `SALVAMED_CLAVE` | Clave para entrar. **Obligatoria en línea**: sin ella la app en Railway queda en solo lectura |
| `SALVAMED_URL_PUBLICA` | `https://…` de la app; se usa para el webhook y el botón «Ver detalle» |
| `TELEGRAM_TOKEN` | Token del bot (de @BotFather) |
| `TELEGRAM_MODO` | `polling` (en la computadora), `webhook` (en Railway) o `simulado`. Sin token siempre es `simulado` |
| `TELEGRAM_CHATS` | IDs de chat que reciben los avisos de operación, separados por coma |
| `TELEGRAM_CHATS_SIMULACION` | Chats para los avisos de la simulación (si no, usa `TELEGRAM_CHATS`) |
| `TELEGRAM_USUARIOS` | IDs de usuario que pueden pulsar los botones (vacío: cualquiera del chat autorizado) |
| `TELEGRAM_SECRETO_WEBHOOK` | Texto secreto que Telegram envía en cada llamada al webhook |
| `SALVAMED_SEGUNDOS_POR_DIA` | Solo para demostrar. Cuántos segundos reales dura un «día» del plazo de entrega (normal: `86400`). Con `10`, un plazo de 2 días se cumple en 20 s, el pedido se recibe solo y avisa «📦 recibido» |

En PowerShell las variables se ponen así, solo para esa ventana:

```powershell
$env:SALVAMED_CLAVE = "una-clave-larga"
```

## Telegram

Sin token, los avisos quedan guardados con «transporte simulado» y se responden desde la web (en la pantalla del
pedido o en la simulación). Eso basta para probar todo el flujo sin internet, pero **no** demuestra el bot real.

### Probar el bot real en la computadora (long polling)

Usa un **bot de pruebas**, distinto del de Railway. Si los dos usan el mismo bot, se quitan las actualizaciones
entre sí (SALVAMED no hace polling si detecta que el bot tiene un webhook activo).

1. En Telegram, habla con @BotFather, envía `/newbot` y guarda el token.
2. Arranca la app con el bot:
   ```powershell
   $env:TELEGRAM_TOKEN = "<token del bot de pruebas>"
   $env:TELEGRAM_MODO = "polling"
   python -m app
   ```
3. Escríbele `/start` al bot (o agrégalo a un grupo y escribe `/start` ahí). Te responde con el id del chat.
4. Detén la app (Ctrl+C), define el chat y vuelve a arrancar:
   ```powershell
   $env:TELEGRAM_CHATS = "<id del chat>"
   python -m app
   ```
5. En **Simulación**, empieza una corrida y dale ▶. Cuando un medicamento llegue a su punto de reorden, el aviso llega
   al chat. Al pulsar **✅ Pedido realizado** la corrida se reanuda sola.

### En Railway (webhook)

1. Crea otro bot para producción (o usa el de pruebas solo si ya no lo usas en local).
2. En las variables del servicio: `TELEGRAM_TOKEN`, `TELEGRAM_MODO=webhook`, `TELEGRAM_SECRETO_WEBHOOK` (un texto
   aleatorio), `SALVAMED_URL_PUBLICA=https://salvamed-production.up.railway.app`, `TELEGRAM_CHATS` y `SALVAMED_CLAVE`.
3. Al arrancar, la app registra el webhook `…/telegram/webhook` con el secreto. Cada botón valida chat, usuario,
   pedido y corrida, y se procesa una sola vez.

Si el envío falla, el despacho o el pedido no se deshacen: el mensaje queda con su error y se reintenta con espera
creciente (hasta 5 veces; después se puede reintentar desde la pantalla del pedido).

## Railway y la base de datos

El arranque (`python -m app`) y la revisión de salud (`/salud`) están en `railway.json`. Railway puede desplegar
solo al recibir cambios en la rama conectada: esta versión **no se ha subido ni desplegado**.

Las tablas de la versión anterior (`medicamentos`, `movimientos`) **no se modifican ni se borran**: la versión 2 crea
tablas nuevas con otros nombres (ver [app/migraciones.py](app/migraciones.py)) y la operación arranca vacía. Antes de
desplegar conviene sacar un respaldo:

```powershell
pg_dump "$env:DATABASE_URL" > respaldo.sql
```

## Importar datos

**Configuración → Importar datos** acepta `.xlsx`, `.csv` y `.tsv`. Primero se ve una vista previa con el mapeo de
columnas, los errores y la procedencia (archivo, hoja y fila); nada se guarda hasta confirmar. Reimportar no duplica
medicamentos ni historia, y no cambia existencias salvo que se marque registrar un ajuste.

- `datos/plantilla_salvamed.xlsx`: plantilla vacía (catálogo, historial diario y configuración).
- `datos/ejemplo_simulado.xlsx`: 8 medicamentos con 45 días de consumo **generado** (semilla 2026). Todo es simulado.
- `datos/SALVAMED en orden.csv`: el archivo del grupo. Es texto separado por tabulaciones, en cp1252. Sus `sem_1`…`sem_8`
  se guardan como totales semanales; no se convierten en consumos diarios.

## Estructura

| Archivo | Qué hace |
| --- | --- |
| `app/modelo.py` | Fórmulas de la clase 12 |
| `app/consumo.py` | μD y σD con días completos; aumento de consumo |
| `app/planificacion.py` | Parámetros con su fuente, plan y estado de cada medicamento |
| `app/inventario.py` | Alta, despacho a servicios, anulación, ajuste y recepción (siempre con movimientos) |
| `app/pedidos.py` | Pedidos, posición de inventario y avisos de reposición |
| `app/telegram.py`, `app/tareas.py` | Bot, bandeja de salida y tareas de fondo |
| `app/simulacion.py` | Reloj virtual, servicios y escenarios, pausa de exposición y recepción simulada |
| `app/importacion.py`, `app/plantilla.py` | Importación y plantillas |
| `app/seguridad.py` | Clave de acceso |
