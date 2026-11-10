# 🗞 NewsBot — tu bot de noticias en Telegram

Bot personalizable para recibir en Telegram noticias recientes sobre inteligencia artificial, programación y desarrollo web, tecnología, videojuegos (incluye PlayStation y GTA), ciberseguridad, Colombia, mundo, ciencia y economía.

**Sin API de noticias de pago, sin base de datos externa y sin servidor web.** Funciona con Python y obtiene titulares y enlaces mediante búsquedas RSS de Google News. No inventa noticias ni crea resúmenes con IA: incluye fragmentos del RSS cuando están disponibles. **El bot solo puede entregar noticias cuando el programa está encendido y tiene internet.**

## ✨ Funciones

- Resumen automático de **hasta 5 noticias nuevas al día** (por defecto, a las **08:00, hora Colombia**).
- `/noticias` para solicitar noticias al instante.
- Botones para activar/desactivar temas e indicar la hora del envío.
- `/categoria videojuegos` para consultar noticias de una sola categoría.
- `/hora 07:30` para elegir cualquier hora en formato de 24 horas.
- `/pausar` y `/reanudar` para suspender o reactivar las entregas.
- SQLite conserva usuarios, horarios, temas y enlaces ya enviados entre reinicios.
- Evita duplicados de titulares entre categorías y evita reenviar un titular ya entregado en resúmenes diarios.
- Funcionamiento mediante *long polling*: no necesitas abrir puertos, comprar un dominio ni configurar webhook.
- Compatible con **Windows 11** y **Linux**.

## 1. Crea el bot desde Telegram

1. Abre Telegram y entra en **[@BotFather](https://t.me/BotFather)** (cuenta oficial, con verificación).
2. Envía `/newbot`.
3. Define el nombre que quieras, por ejemplo `NewsBot Robert`.
4. Define un usuario único que termine en `bot`, por ejemplo `mi_noticiero_personal_bot` si está disponible.
5. BotFather te entregará un **token privado**. No lo envíes por chats, no lo publiques y no lo subas a GitHub.
6. Busca tu bot por su usuario y pulsa **Iniciar** (`/start`) una vez que el programa esté ejecutándose.

> Importante: el código no puede crear automáticamente tu bot en la cuenta de BotFather. Ese paso debe realizarlo el propietario de la cuenta en Telegram.

## 2A. Instalar en Windows 11

Necesitas **Python 3.11 o superior** instalado y disponible en el equipo.

1. Extrae el ZIP.
2. Dentro de la carpeta, haz doble clic en `scripts\instalar_windows.bat`.
3. Se creará el archivo `.env` en la carpeta principal del proyecto.
4. Ábrelo con Bloc de notas, reemplaza `PON_AQUI_TU_TOKEN` por tu token real de BotFather y guarda.
5. Haz doble clic en `scripts\iniciar_windows.bat` para arrancar el bot.
6. Abre Telegram, busca tu bot y escribe `/start`.

No es necesario cambiar la política de ejecución de PowerShell porque los `.bat` ejecutan el `python.exe` directamente, sin `Activate.ps1` ni `npm.ps1`.

### Instalar manualmente desde PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
.\.venv\Scripts\python.exe main.py
```

Si `.env` ya existe, omite el comando de copia para conservar tu token.

## 2B. Instalar en Linux (Fedora, Ubuntu, Debian y otros)

Instala Python 3.11+, `python3-pip` y el soporte `venv` de tu distribución. Desde la carpeta extraída:

```bash
bash scripts/instalar_linux.sh
nano .env  # pega tu TELEGRAM_BOT_TOKEN
bash scripts/iniciar_linux.sh
```

O manualmente:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
nano .env
.venv/bin/python main.py
```

**Para que lleguen las noticias diariamente, deja el equipo encendido, con internet, y el proceso `main.py` ejecutándose.** Si el computador está apagado a la hora programada, el bot no enviará en ese momento ni recuperará automáticamente todos los resúmenes perdidos. Si más adelante quieres que funcione 24/7, podrás desplegarlo en un servidor que permita procesos persistentes.

## 3. Variables de configuración

Edita el archivo `.env` (NO `.env.example`):

```dotenv
TELEGRAM_BOT_TOKEN=PON_AQUI_TU_TOKEN
BOT_TZ=America/Bogota
BOT_DAILY_TIME=08:00
ALLOWED_USER_IDS=
DATA_DIR=data
```

| Variable | Significado |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token privado creado por BotFather, obligatorio. |
| `BOT_TZ` | Zona horaria, por defecto `America/Bogota`. |
| `BOT_DAILY_TIME` | Hora inicial para suscriptores nuevos, por defecto `08:00`. |
| `ALLOWED_USER_IDS` | Opcional: IDs numéricos separados por comas. Si está vacío, cualquier persona puede usarlo en chats privados. |
| `DATA_DIR` | Carpeta para guardar SQLite, por defecto `data`. |

**Para uso personal:** inicia el bot, envía `/miid` y copia el número que te devuelve. Ponlo en `ALLOWED_USER_IDS=123456789` y reinicia el bot. Así nadie más puede suscribirse. Si varias personas usan el bot, pueden indicarse distintos IDs separados por comas.

## 4. Comandos de Telegram

| Comando | Acción |
|---|---|
| `/start` | Registro e interfaz principal. |
| `/noticias` o `/ahora` | Mostrar hasta 5 noticias recientes según tus temas. |
| `/categoria ia` | Mostrar noticias de IA. También: `programacion`, `tecnologia`, `videojuegos`, `ciberseguridad`, `colombia`, `mundo`, `ciencia`, `economia`. |
| `/temas` | Elegir categorías mediante botones. |
| `/hora 08:00` | Establecer hora diaria. |
| `/hora` | Mostrar botones con horarios sugeridos. |
| `/estado` | Ver preferencias y estado de la suscripción. |
| `/pausar` | Dejar de recibir noticias automáticas. |
| `/reanudar` | Reactivar noticias automáticas. |
| `/fuentes` | Conocer el origen de las noticias. |
| `/miid` | Obtener tu ID de Telegram. |
| `/ayuda` | Ver lista de comandos. |

El bot intenta seleccionar **una noticia por tema por ronda** para mejorar la variedad. Los resultados y su frescura dependen de Google News RSS; cuando hay noticias sin fecha verificable o más antiguas de 72 horas, el bot las descarta. Los fragmentos no son resúmenes originales y pueden estar vacíos.

## 🔊 Escuchar noticias en Telegram

- Envía `/escuchar` para recibir ahora las noticias en texto y audio.
- Envía `/voz` para activar o desactivar el audio **automático** junto al resumen diario. Se guarda en SQLite, incluso tras reiniciar el bot.
- También puedes usar los botones **🎧 Escuchar noticias** y **🔊 Activar / desactivar voz**.
- El bot narra hasta cinco titulares y los fragmentos disponibles del RSS, **no el contenido completo de los artículos**.
- Por defecto utiliza la voz colombiana `es-CO-SalomeNeural`. Para cambiarla a una voz masculina, añade `TTS_VOICE=es-CO-GonzaloNeural` a tu archivo privado `.env`.
- La síntesis se realiza en línea con **edge-tts** (servicio de voz de Microsoft), por lo que el texto narrado se envía a ese servicio y requiere internet. No necesita clave de API.
- Con `ffmpeg` instalado se envían notas de voz OGG/Opus; sin `ffmpeg`, el bot envía un audio MP3 reproducible en Telegram.
- Si falla la voz, las noticias siguen llegando en texto. La función de voz está desactivada por defecto.

**Actualizar en Fedora Linux:**

```bash
cd ~/Documentos/bot-noticias/bot-noticia-telegram
git pull --ff-only origin main
.venv/bin/python -m pip install -r requirements.txt
sudo dnf install -y ffmpeg-free  # Opcional: nota de voz OGG/Opus; MP3 funciona sin ffmpeg
bash scripts/iniciar_linux.sh
```

Si ya tienes un servicio systemd configurado, reinícialo en lugar de iniciar una segunda copia:

```bash
systemctl --user restart bot-noticias.service
```

## 5. Subir el código a tu repositorio GitHub

Repositorio destino: [roberthjr21/bot-noticia-telegram](https://github.com/roberthjr21/bot-noticia-telegram)

Puedes subir los archivos que están **dentro** de la carpeta `bot-noticia-telegram` al repositorio usando la interfaz de GitHub (`Add file` → `Upload files`) o Git:

```bash
git clone https://github.com/roberthjr21/bot-noticia-telegram.git
# Copia el contenido del proyecto descargado al directorio clonado.
cd bot-noticia-telegram
git add .
git commit -m "Crear bot personalizado de noticias para Telegram"
git push origin main
```

**No subas `.env`, la base de datos `data/` ni tu token.** `.gitignore` ya está configurado. GitHub almacena el código, pero por sí solo **no ejecuta** el bot 24/7.

## 6. Pruebas locales

Desde la carpeta del proyecto:

```bash
python -m unittest discover -s tests -v
python -m compileall -q bot_noticias main.py
```

Puedes comprobar la conexión real enviando `/noticias` desde Telegram cuando `main.py` esté ejecutándose. Las pruebas unitarias no requieren internet ni token; los RSS reales y Telegram necesitan acceso a internet.

## 📁 Organización

```text
bot-noticia-telegram/
├── main.py                  # punto de entrada
├── bot_noticias/
│   ├── __init__.py
│   ├── bot.py                # botones, comandos, programación diaria
│   ├── config.py             # variables de entorno
│   ├── database.py           # SQLite
│   └── news.py               # consultas RSS y selección de noticias
├── scripts/
│   ├── instalar_windows.bat
│   ├── iniciar_windows.bat
│   ├── instalar_linux.sh
│   └── iniciar_linux.sh
├── tests/
│   ├── test_news.py
│   └── test_database.py
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## ⚠️ Límites conocidos

- Necesita una conexión a internet disponible, tanto para obtener noticias como para comunicarse con Telegram.
- Google News RSS puede fallar temporalmente, limitar consultas o proporcionar menos noticias de las esperadas.
- El bot envía titulares, fuentes, fragmentos disponibles y enlaces, **no análisis periodísticos generados por IA**.
- Los titulares duplicados por texto se eliminan; noticias equivalentes redactadas de forma muy distinta pueden seguir apareciendo.
- No hay envío de mensajes si el equipo está apagado. Para producción 24/7, conviene usar un servidor o VPS.
- No ejecutar dos copias del mismo bot con el mismo token por *long polling*, porque pueden competir por las actualizaciones.

Licencia de ejemplo: MIT (ver `LICENSE`).


## 🎧 NewsBot v2 — etapa 2: modos de audio y voces

Esta actualización añade preferencias de narración **por chat**, sin perder la hora, los temas ni el estado de voz que ya estaban guardados en SQLite.

1. Envía `/audio` o pulsa **🎙 Audio y voces** en el menú principal.
2. Selecciona un modo: **⚡ Rápido** (hasta ~1 minuto; hasta 3 titulares), **🎙 Normal** (hasta ~3 minutos; hasta 5 titulares y fragmentos) o **📖 Detallado** (hasta ~5 minutos; incluye medio y fecha cuando están disponibles).
3. Selecciona la voz: **Predeterminada** (respeta `TTS_VOICE` del `.env`), **Salomé** (`es-CO-SalomeNeural`) o **Gonzalo** (`es-CO-GonzaloNeural`).
4. Selecciona velocidad **Lenta** (-15 %), **Normal** o **Rápida** (+15 %).
5. Pulsa **🎧 Probar narración** o envía `/escuchar`.

La duración es un **máximo aproximado**, no una duración garantizada: el bot no inventa contenido ni rellena audios cuando el RSS tiene poca información. Los modos detallados tampoco leen el artículo completo. La narración usa el servicio en línea de Microsoft mediante `edge-tts`.

La preferencia `/voz` sigue controlando si el resumen diario incluye audio. El nuevo menú permite activar o desactivar el audio diario sin cambiar las demás opciones. Los datos se guardan en la tabla `audio_preferences`, creada automáticamente sin borrar los datos anteriores.

### Actualizar con `systemd` en Fedora (después de integrar la rama de desarrollo)

**No ejecutes una segunda instancia con `bash scripts/iniciar_linux.sh` si el servicio ya está activo.** Antes de actualizar, realiza una copia de seguridad de `data/news_bot.sqlite3` con el mecanismo de backup de SQLite. Luego:

```bash
cd ~/Documentos/bot-noticias/bot-noticia-telegram
git pull --ff-only origin main
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
systemctl --user restart bot-noticias.service
systemctl --user status bot-noticias.service
journalctl --user -u bot-noticias.service -n 30 --no-pager
```

**Nota:** el desarrollo de esta etapa se hace en una rama aparte; no actualices `main` ni reinicies el servicio hasta aprobar la integración.
