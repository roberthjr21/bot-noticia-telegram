# Plan de implementación — NewsBot v2

**Estado:** preparado para desarrollo, no desplegado.  
**Base auditada:** `main` en `c81912b805937825225baaae1e2243e9464faeab` (10-11-2026).  
**Objetivo:** evolucionar el bot sin interrumpir el envío diario ni perder la configuración SQLite existente.

## 1. Diagnóstico del código actual

| Componente | Ya existe | Trabajo pendiente |
|---|---|---|
| `bot_noticias/news.py` | 9 categorías, RSS de Google News, noticias de hasta 72 h, selección variada y deduplicación exacta por título normalizado | Ranking, equivalencias entre titulares, caché y datos históricos para resúmenes semanales |
| `bot_noticias/database.py` | SQLite: suscriptores, temas, hora, voz activada y huellas de noticias enviadas; migración aditiva previa | Preferencias de audio/alertas/semanal, historial de artículos, entregas y diagnósticos; migraciones versionadas |
| `bot_noticias/voice.py` | `edge-tts`, voz `es-CO-SalomeNeural`, OGG/MP3 y archivos temporales | Voz y longitud por chat, velocidad, control de tamaño/duración y fallos |
| `bot_noticias/bot.py` | Comandos, botones inline, envío diario, `/escuchar`, `/voz`, horario, temas | Menús modulares, comandos nuevos, jobs semanales y alertas, diagnósticos |
| `main.py` / Fedora | Long polling, `systemd --user` ya configurado en el equipo | Mantener una sola instancia; registro estructurado y verificación tras reinicios |
| `tests/` | Pruebas de SQLite, RSS y texto narrado | Pruebas de migración, callbacks, alertas, resúmenes, límites y scheduling |

**Problemas y precauciones encontrados:**
1. `bot.py` concentra demasiadas responsabilidades; conviene separar interfaz, jobs y entrega.
2. La deduplicación actual usa el título normalizado; una noticia redactada de forma distinta puede repetirse.
3. La tabla `delivered` solo conserva huellas, no datos de artículos; así no se puede elaborar un semanal histórico fiable.
4. El audio actual usa una voz global de `.env`; no admite preferencias individuales.
5. Los RSS no garantizan contenido suficiente para narraciones de 3 o 5 minutos: serán **duraciones objetivo/máximas**, nunca se inventará información para completar tiempo.
6. El README menciona scripts de Windows que no figuran en el árbol actual; corregir documentación.
7. Si `ALLOWED_USER_IDS` está vacío, cualquier persona puede suscribirse por chat privado. Recomendar configurar lista de acceso para uso personal.
8. Dos instancias de long polling con el mismo token provocan `telegram.error.Conflict`; no iniciar el script manualmente mientras `bot-noticias.service` esté activo.

## 2. Diseño funcional aprobado para desarrollar

### A. Personalización
- Conservar las 9 categorías existentes y los temas seleccionados de cada chat.
- Añadir prioridades de temas (normal / favorito), manteniendo al menos un tema activo.
- Mostrar preferencias completas en `/estado` y permitir cambios desde botones.

### B. Audio y voces
- Modos: **rápido (hasta ~1 min)**, **normal (hasta ~3 min)** y **detallado (hasta ~5 min)**.
- Voces permitidas inicialmente: `es-CO-SalomeNeural` y `es-CO-GonzaloNeural`.
- Guardar modo, voz y velocidad por chat en SQLite; mantener el interruptor `/voz`.
- No rellenar con datos no verificados; si el RSS aporta poco contenido, el audio será más corto.
- Conservar la entrega de texto si falla `edge-tts` o `ffmpeg`.

### C. Menú interactivo
- Inicio: «Noticias ahora», «Escuchar», «Mis temas», «Audio y voz», «Horario», «Alertas», «Semanal», «Estado y ayuda».
- Submenús con botones «Volver»; `callback_data` cortos (límite de Telegram: 64 bytes).
- Compatibilidad con comandos existentes: `/start`, `/noticias`, `/escuchar`, `/voz`, `/temas`, `/hora`, `/estado`, `/pausar`, `/reanudar`.
- Nuevos comandos previstos: `/audio`, `/alertas`, `/semanal`, `/diagnostico`.

### D. Resúmenes inteligentes y trazables
- Versión base sin pagos: resumen **extractivo**, usando solo título, medio, fecha y fragmento RSS disponible.
- Estructura: «Qué ocurrió» y «Fuente / Leer más». Añadir «Por qué importa» **solo** cuando esté respaldado por información verificable; si no, omitir.
- No confundir descripción RSS con el artículo completo; no atribuir conclusiones inexistentes.
- Proveedor de IA generativa opcional en fase posterior, **desactivado por defecto**, con consentimiento y clave de API local si el usuario lo decide. No se requiere para las otras funciones.

### E. Alertas importantes
- **Desactivadas por defecto** hasta que el usuario las habilite.
- Revisar cada ~30 minutos los temas elegidos, priorizar términos de interés y publicaciones recientes; las alertas son «novedades destacadas», no afirmaciones de última hora verificadas de forma independiente.
- Evitar duplicados por chat/canal; máximo inicial de **2 alertas por día** y silencio de **22:00 a 07:00** (zona `America/Bogota`).
- No alertar cuando el RSS falla, falta fecha verificable o la noticia ya fue enviada.
- Reutilizar resultados RSS entre suscriptores en cada ciclo para evitar consultas excesivas.

### F. Resumen semanal
- **Opt-in**, inicialmente domingo a las **18:00**, horario modificable.
- Seleccionar noticias registradas en los últimos siete días y distribuirlas por categorías.
- Entregar texto; audio opcional si está habilitado.
- Informar cuando el historial sea insuficiente; no reconstruir una semana entera con RSS de solo tres días.
- El historial empieza a acumularse tras desplegar esta versión; no inventar noticias anteriores.

### G. Estabilidad y diagnóstico
- `/diagnostico` para el usuario autorizado: estado del programador, última entrega, fuentes RSS recientes y fallos, sin exponer token ni datos sensibles.
- Tiempos de espera, reintentos moderados, límites de concurrencia, registro de errores y recuperación tras reinicio.
- Envíos idempotentes; controlar `RetryAfter`/bloqueos de Telegram y evitar bucles de alertas.
- Pruebas automatizadas en CI (sin token, sin red); comprobación manual de Telegram/TTS antes del despliegue.

## 3. Arquitectura propuesta

Mantener Python, `python-telegram-bot`, SQLite, Google News RSS y `edge-tts`. Separar progresivamente:

```text
bot_noticias/
  bot.py             # creación de Application y registro de handlers
  handlers.py        # comandos y callbacks
  keyboards.py       # navegación Telegram
  scheduler.py       # diario, semanal y revisión de alertas
  delivery.py        # formato, envío, idempotencia, fallback audio
  news.py            # RSS y selección (existente)
  summaries.py       # resúmenes extractivos, trazabilidad
  alerts.py          # ranking, límites, ventana de silencio
  voice.py           # TTS y modos de duración (existente)
  database.py        # SQLite y migraciones (existente)
  config.py          # configuración (existente)
  diagnostics.py     # estado y métricas operativas
tests/
  test_database.py
  test_news.py
  test_voice.py
  test_alerts.py
  test_summaries.py
  test_handlers.py
  test_scheduler.py
```

No es necesario crear todos los módulos de una vez. Mantener interfaces pequeñas y pruebas por componente.

## 4. Migración SQLite no destructiva

- Conservar `subscribers`, `preferences` y `delivered`; **no borrar ni recrear datos**.
- Añadir columnas de preferencias con `ALTER TABLE` o tabla `subscriber_settings`: `audio_mode`, `voice_name`, `voice_rate`, `weekly_enabled`, `weekly_time`, `alerts_enabled`, `alerts_level`.
- Crear `news_history` para artículos vistos (huella, título, fuente, URL, tema, fecha, fragmento, fecha de captura).
- Crear `delivery_events` para estado por chat, canal (`daily`, `weekly`, `alert`) y momento; unicidad e índices para prevenir reenvíos.
- Crear `service_events` o métricas agregadas para diagnósticos sin registrar secretos.
- Migraciones repetibles y probadas contra un esquema legado con voz activada y horarios personalizados.
- Retención configurable del historial (por ejemplo, 45 días); minimizar datos personales.

## 5. Orden de desarrollo y criterios de aceptación

| Fase | Entregable | Criterio de aceptación |
|---|---|---|
| **0. Protección** | Pruebas base, respaldo y CI; corregir documentación | Las funciones actuales y la configuración persisten |
| **1. Datos + menú** | Migración, preferencias y navegación | Botones y comandos cambian opciones sin perder suscripciones |
| **2. Audio** | Modos, voz por usuario y velocidad | `/escuchar` respeta la selección; fallback a texto |
| **3. Resúmenes** | Extractivos y almacenamiento histórico | Cada afirmación proviene del RSS; enlaces verificables |
| **4. Semanal** | Job de domingo y preferencia | Una entrega por semana, sin datos inventados |
| **5. Alertas** | Ranking, límites, silencio y deduplicación | Máx. 2 alertas/día, opt-in y sin spam |
| **6. Operación** | Diagnósticos, CI y documentación | Tests verdes, logs útiles, reinicio sin conflictos |

## 6. Plan de pruebas

- **SQLite:** migración de DB antigua, idempotencia, persistencia de temas/voz/horario, rollback en errores.
- **RSS:** fechas ausentes, feeds caídos, duplicados, titulares equivalentes, HTML y URLs inválidas.
- **Audio:** selección de voz, modos, límites, TTS fallido, ffmpeg ausente.
- **Telegram:** callbacks válidos/inválidos, permisos, menús, suscripciones y errores.
- **Alertas:** umbral, máximo diario, horario de silencio, reinicios y dos usuarios con preferencias distintas.
- **Semanal:** domingo, cambio de hora, falta de historial, no duplicación.
- **Operación:** pruebas sin red ni token; verificación manual `/noticias`, `/escuchar`, `/estado`, `/diagnostico`.

## 7. Despliegue seguro en Fedora

1. Desarrollar y revisar en la rama `feature/newsbot-v2`; **no modificar `main` ni el servicio activo durante el desarrollo**.
2. Ejecutar pruebas y revisión de cambios; confirmar que `.env` y `data/` siguen excluidos de Git.
3. Antes de actualizar, crear un respaldo consistente de SQLite con la API de backup de SQLite y conservar el commit estable `c81912b`.
4. Integrar cambios aprobados en `main` y ejecutar en Fedora:
   ```bash
   cd ~/Documentos/bot-noticias/bot-noticia-telegram
   git pull --ff-only origin main
   .venv/bin/python -m pip install -r requirements.txt
   .venv/bin/python -m unittest discover -s tests -v
   systemctl --user restart bot-noticias.service
   systemctl --user status bot-noticias.service
   journalctl --user -u bot-noticias.service -n 40 --no-pager
   ```
5. Probar Telegram y confirmar que solo corre **una** instancia de `main.py`.
6. Ante fallos, volver al commit estable y restaurar la copia de SQLite si fuera necesario. Las migraciones deben ser compatibles hacia atrás siempre que sea posible.

## 8. Decisiones de producto y seguridad

- Preferencias nuevas por defecto **no invasivas**: alertas y semanal desactivados; audio existente no se desactiva.
- Mantener la hora diaria y los temas que el usuario ya seleccionó.
- Sin servicios pagos obligatorios; la síntesis `edge-tts` usa un servicio externo de Microsoft.
- No enviar claves ni archivos `.env` a GitHub, ni incluir datos privados en logs.
- Si el bot es personal, configurar `ALLOWED_USER_IDS` con el ID autorizado.
- No prometer que el bot envíe noticias cuando el equipo está apagado; `systemd` solo mantiene el proceso mientras Fedora está funcionando.

## 9. Definición de terminado (v2)

- Todas las siete mejoras están disponibles y documentadas.
- Las funciones anteriores continúan funcionando sin cambios de comando incompatibles.
- Las preferencias y el historial sobreviven a reinicios.
- Las pruebas unitarias y la verificación manual pasan.
- No se pierden suscriptores ni se publican secretos.
- El servicio `bot-noticias.service` permanece estable, sin errores `Conflict`.
