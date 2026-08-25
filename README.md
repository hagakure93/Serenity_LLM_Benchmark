# Prueba de carga y observabilidad — Chatbot Academia Prefortia

Panel local (Flask, una sola página) para **dos cosas**, ambas contra el chatbot real:

1. **Prueba de carga / estrés** — simula N usuarios simultáneos enviando mensajes al
   endpoint del chatbot y mide tasa de éxito, latencias (media, p50/p90/p95),
   throughput y errores.
2. **Observabilidad del agente (Serenity Star)** — envía **una sola consulta real**
   por pulsación al agente y mide su **consumo real**: tokens, coste en €, latencia,
   sub-agentes activados y la respuesta. Con banco de preguntas, registro persistente,
   exportación (JSON/CSV) y comparativa por modelo con percentiles.

> ⚠️ **Lanza tráfico real contra producción.** La prueba de carga golpea el endpoint
> del chatbot; la observabilidad ejecuta el agente de verdad y **cada consulta cuesta
> crédito de IA real** (~0,02–0,05 €). Empieza con poco, avisa a quien gestione la
> infraestructura y coordínate para no agotar el crédito ni afectar a alumnos.

---

## Requisitos e instalación

- **Python 3.10+** y las dependencias de `requirements.txt` (Flask y requests).

```bash
pip install -r requirements.txt
```

> **Nota Windows:** si `python` no está en el PATH, usa la ruta completa de tu
> instalación (p. ej. la del Microsoft Store):
> `"%LOCALAPPDATA%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\python.exe"`

---

## Cómo ejecutar la app

**Opción rápida (Windows):** doble clic o desde consola:

```bat
iniciar_app.bat
```

Libera el puerto 5000 si estuviera ocupado, arranca la app en su propia ventana y
abre el navegador en <http://127.0.0.1:5000>.

**Manual (cualquier sistema):**

```bash
python app.py
# -> abre http://127.0.0.1:5000
```

## Cómo pararla

- **Windows:** ejecuta `parar_app.bat` (cierra lo que escuche en el puerto 5000), o
  pulsa **Ctrl+C** en la ventana donde corre, o ciérrala.
- **Manual:** **Ctrl+C** en la terminal donde lanzaste `python app.py`.

> Arranca **una sola** instancia a la vez. Si ves comportamiento raro, para con
> `parar_app.bat` y vuelve a iniciar.

---

## Cómo funciona

### 1) Prueba de carga (panel izquierdo + tabla "Peticiones")
- Configura **Usuarios**, **Mensajes c/u**, **Ramp-up** (0 = todos a la vez = pico
  máximo), **Timeout**, mensaje y `migasJson`. Pulsa **▶ Ejecutar**.
- Lanza N hilos concurrentes con POST al endpoint configurado y mide en vivo:
  completadas, éxitos/errores, latencias (media/mín/máx, p50/p90/p95), req/s.
- Descarga los resultados en JSON al terminar.

### 2) Observabilidad del agente (Serenity Star)
- **1 consulta por pulsación** (sin bucles ni concurrencia). Usa el endpoint real
  `POST /api/v2/agent/{agentCode}/execute` con `stream=false` y lee del resultado:
  `completion_usage` (tokens), `cost` (€), sub-agentes (`action_results`) y la respuesta.
- **Banco de preguntas**: desplegable con escenarios reales, agrupados por el
  sub-agente al que suelen ir (Temario / Test / Soporte). Al elegir, rellena el
  mensaje y la sección.
- **Etiqueta (modelo/agente)**: texto libre para marcar qué estás probando; queda en
  el registro y en los exports.
- **Registro de consultas (persistente)**: cada consulta (éxito o error) se guarda en
  memoria y en `registro_consultas.jsonl`, así sobrevive a reinicios.
- **Comparativa por etiqueta (percentiles)**: agrupa por Etiqueta y calcula
  p50/p90/p95 de latencia, media, tokens medios y coste total. Necesita varias
  consultas por modelo para ser fiable.
- **Exportación**: botones **⤓ JSON** y **⤓ Excel (CSV)** del registro completo.

### API key (seguridad)
La clave del agente se pasa por la cabecera **`X-API-KEY`**. Dónde ponerla:
- En el **campo tipo contraseña** del panel (solo en memoria; no se guarda en disco,
  ni en logs, ni en exports), **o**
- En la variable de entorno **`SERENITY_API_KEY`** (se usa si el campo está vacío).

**Nunca** subas la clave al repositorio ni la pegues en sitios compartidos.

### Robustez
- Ninguna petición sale hasta que pulsas el botón correspondiente.
- Timeouts en el backend y `AbortController` en el navegador: la interfaz no se
  queda colgada aunque el agente tarde o falle.

---

## Endpoints (referencia rápida)

| Ruta | Qué hace |
|------|----------|
| `GET /` | El panel (HTML). |
| `GET /config` | Configuración por defecto de la prueba de carga. |
| `POST /run` · `GET /status` · `POST /stop` · `GET /export` | Motor de la prueba de carga. |
| `POST /agent-execute` | Una consulta real al agente; mide y registra. |
| `GET /probes` · `GET /probes/summary` | Registro y comparativa (percentiles). |
| `GET /probes/export.json` · `GET /probes/export.csv` | Exportar el registro. |
| `GET /presets` | Banco de preguntas para el desplegable. |

---

## Ficheros

- `app.py` — la aplicación (backend + frontend en una sola página).
- `iniciar_app.bat` / `parar_app.bat` — arrancar / parar en Windows.
- `requirements.txt` — dependencias.
- `preguntas_banco.json` — banco de preguntas del desplegable.
- `registro_consultas.jsonl` — registro de consultas (generado en ejecución; ignorado por git).
- `LEEME.md` — nota original de la prueba de carga.
