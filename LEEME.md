# Prueba de carga — Chatbot Academia Prefortia

Dashboard local para simular usuarios simultáneos escribiendo en el chatbot.
No necesita acceso al código: envía peticiones POST al endpoint real, igual que la web.

## Instalación (una vez)

```bash
pip install -r requirements.txt
```

## Ejecutar

```bash
python app.py
```

Abre en el navegador: **http://127.0.0.1:5000**

## Cómo usar

1. Revisa la configuración del panel izquierdo (ya viene rellena con tus datos):
   - **Usuarios**: cuántos escriben a la vez (10 por defecto).
   - **Mensajes c/u**: cuántos mensajes envía cada usuario.
   - **Ramp-up**: separación entre el arranque de cada usuario.
     `0` = todos exactamente a la vez (pico máximo de estrés).
     `0.15` = arranque escalonado, más parecido a usuarios reales.
   - **Modo chatId**: "único por usuario" (cada usuario su conversación) o
     "compartido" (todos en el mismo chat).
2. Pulsa **▶ Ejecutar**.
3. Verás en tiempo real: peticiones completadas, éxitos/errores, latencias
   (media, mín, máx, p50/p90/p95), req/s y una respuesta de ejemplo del bot
   para confirmar que realmente contesta (no solo que devuelve 200).
4. Al terminar puedes **descargar los resultados en JSON**.

## Cómo interpretar los resultados

- **Tasa de éxito < 100%**: el servidor empieza a fallar bajo carga.
- **p95 muy por encima de la media**: hay peticiones que se atascan; el sistema
  aguanta de media pero algunos usuarios sufren esperas largas.
- **Latencia que crece al subir usuarios**: prueba con 5, 10, 20, 30… y observa
  a partir de cuántos usuarios se degrada. Ahí está el límite práctico.
- **Errores TIMEOUT**: el bot tarda más que el timeout configurado en responder.

## Escalar la prueba

Cambia "Usuarios" a 20, 30, 50… y repite. Aumenta gradualmente para encontrar
el punto donde el rendimiento se degrada.

## Nota importante

Estás lanzando tráfico real contra el servidor de producción. Empieza con pocos
usuarios, avisa a quien gestione la infraestructura, y no dispares cargas muy
altas de forma sostenida sin permiso: podrías afectar a alumnos reales conectados.
