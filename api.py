"""
NovaCloud API Client — Home Assistant Integration
==================================================
Cliente asíncrono para la plataforma abierta de NovaCloud (VNNOX).

Documentación oficial : https://developer-en.vnnox.com
Base URL de producción : https://open-us.vnnox.com

Modelo de autenticación
-----------------------
Cada request debe incluir cuatro headers calculados en tiempo de ejecución:
  - AppKey   : clave pública de la aplicación registrada en NovaCloud.
  - Nonce    : número aleatorio de 8 bytes en hex, único por request.
  - CurTime  : timestamp Unix actual como string (segundos enteros).
  - CheckSum : SHA-256( AppSecret + Nonce + CurTime ) en hexadecimal.

El servidor valida que la firma sea coherente y que CurTime no esté
demasiado desfasado del tiempo real (ventana de ±5 min aprox.).

Modelo de respuesta
-------------------
La mayoría de los endpoints de control devuelven:
  { "success": ["<playerId>", ...], "fail": ["<playerId>", ...] }

El endpoint RUNNING_STATUS es asíncrono: el 200 sólo confirma que el
comando fue aceptado; los datos reales (brillo, volumen, etc.) llegan
posteriormente a través del webhook configurado en `noticeUrl`.
"""

# ---------------------------------------------------------------------------
# Imports de la biblioteca estándar
# ---------------------------------------------------------------------------
from __future__ import annotations   # Permite usar 'list[str]' en Python 3.8/3.9

import asyncio    # Para capturar asyncio.TimeoutError en el manejo de errores
import hashlib    # SHA-256 para el cálculo del CheckSum
import logging    # Logging estándar de HA; _LOGGER usará el nombre del módulo
import secrets    # Generación de nonce criptográficamente seguro
import time       # Timestamp Unix para CurTime
from dataclasses import dataclass   # Modelo liviano para CommandResult
from typing import Any              # Necesario para type hints genéricos

import aiohttp    # Cliente HTTP asíncrono; dependencia declarada en manifest.json

# ---------------------------------------------------------------------------
# Logger del módulo
# ---------------------------------------------------------------------------
# En Home Assistant, __name__ se resuelve como el nombre completo del módulo,
# p.ej. "custom_components.novacloud.api". Esto permite filtrar logs por
# integración desde el panel de HA (Settings → Logs).
_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes globales
# ---------------------------------------------------------------------------

# URL base de la API para la región US.
# Si VNNOX añade otras regiones (EU, CN), se puede convertir en parámetro.
API_BASE = "https://open-us.vnnox.com"

# Timeout global para todas las requests HTTP.
# `total=15` significa que si la request completa (conexión + lectura) no
# termina en 15 segundos, aiohttp lanza asyncio.TimeoutError.
# Se usa un solo objeto reutilizable para no crear uno por request.
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=15)


# ===========================================================================
# Clase Endpoints
# ===========================================================================
class Endpoints:
    """
    Catálogo centralizado de todos los paths de la API NovaCloud.

    Ventajas de esta estructura frente a strings sueltos o funciones *_url():
      - Un solo lugar para actualizar si el API cambia de versión.
      - Los tests pueden verificar qué endpoint se llamó comparando con
        estas constantes (ej: assertIn(Endpoints.REBOOT, call_url)).
      - El IDE puede autocompletar y detectar typos en tiempo de desarrollo.
    """

    # ── Player Management ──────────────────────────────────────────────────
    # Listado paginado de todos los players de la cuenta.
    PLAYER_LIST             = "/v2/player/list"

    # ── Player Status ──────────────────────────────────────────────────────
    # Estado de conectividad (online/offline), IP, resolución, versión de SO.
    ONLINE_STATUS           = "/v2/player/current/online-status"

    # Solicitud asíncrona de estado de configuración en tiempo real
    # (brillo, volumen, fuente de video, hora). La respuesta llega por webhook.
    RUNNING_STATUS          = "/v2/player/current/running-status"

    # ── Real-Time Control ──────────────────────────────────────────────────
    # Pantalla en negro (CLOSE) o imagen normal (OPEN). No corta alimentación.
    SCREEN_STATUS           = "/v2/player/real-time-control/screen-status"

    # Nivel de brillo del panel LED (0-100).
    BRIGHTNESS              = "/v2/player/real-time-control/brightness"

    # Nivel de volumen del player (0-100).
    VOLUME                  = "/v2/player/real-time-control/volume"

    # Selección de fuente de entrada de video (HDMI, DVI, etc.).
    VIDEO_SOURCE            = "/v2/player/real-time-control/video-source"

    # Reinicio remoto del sistema operativo del player.
    REBOOT                  = "/v2/player/real-time-control/reboot"

    # Encendido/apagado físico del panel (corta alimentación).
    # Diferente de SCREEN_STATUS: éste apaga el hardware, no sólo pone
    # la imagen en negro.
    SCREEN_POWER            = "/v2/player/power/onOrOff"

    # Sincronización de reloj NTP en el player.
    NTP_SYNC                = "/v2/player/real-time-control/ntp"

    # Dispara una captura de pantalla en el player.
    SCREENSHOT              = "/v2/player/real-time-control/screenshot"

    # Reproducción sincronizada entre múltiples players.
    SYNC_PLAYBACK           = "/v2/player/real-time-control/sync-playback"

    # ── Scheduled Control ─────────────────────────────────────────────────
    # Programación horaria de encendido/apagado de pantalla.
    SCHEDULED_SCREEN_STATUS = "/v2/player/scheduled-control/screen-status"

    # Programación horaria de reinicio automático.
    SCHEDULED_REBOOT        = "/v2/player/scheduled-control/reboot"

    # Programación horaria de cambio de volumen.
    SCHEDULED_VOLUME        = "/v2/player/scheduled-control/volume"

    # Programación horaria de cambio de brillo (manual o automático).
    SCHEDULED_BRIGHTNESS    = "/v2/player/scheduled-control/brightness"

    # Programación horaria de cambio de fuente de video.
    SCHEDULED_VIDEO_SOURCE  = "/v2/player/scheduled-control/video-source"

    # ── Logs ───────────────────────────────────────────────────────────────
    # Historial de ejecución de comandos de control enviados a los players.
    CONTROL_LOGS            = "/v2/player/control-log"


# ===========================================================================
# Clase RunningStatusCommand
# ===========================================================================
class RunningStatusCommand:
    """
    Claves de comando válidas para el endpoint RUNNING_STATUS.

    El API acepta una lista de strings en el campo `commands` del payload.
    Estas constantes son los únicos valores documentados oficialmente.

    Uso típico:
        await api.get_status_data(player_id, commands=[
            RunningStatusCommand.VOLUME,
            RunningStatusCommand.BRIGHTNESS,
        ])
    """

    # Solicita el volumen actual del player (0-100).
    VOLUME       = "volumeValue"

    # Solicita el brillo actual del panel (0-100).
    BRIGHTNESS   = "brightnessValue"

    # Solicita el índice de la fuente de video activa.
    VIDEO_SOURCE = "videoSourceValue"

    # Solicita la hora y zona horaria actuales del player.
    TIME         = "timeValue"

    # Shortcut para solicitar todos los comandos en una sola llamada.
    # Se evalúa en tiempo de clase (no de instancia), por eso referencia
    # los atributos directamente como strings literales.
    ALL = [VOLUME, BRIGHTNESS, VIDEO_SOURCE, TIME]


# ===========================================================================
# Clase ScreenStatus
# ===========================================================================
class ScreenStatus:
    """
    Valores válidos para el campo `status` en los endpoints que controlan
    el estado visual de la pantalla (no la alimentación eléctrica).

    OPEN  → la pantalla muestra contenido normalmente.
    CLOSE → la pantalla queda en negro (black screen), pero el player
            sigue encendido y conectado.
    """
    OPEN  = "OPEN"    # Pantalla activa mostrando contenido
    CLOSE = "CLOSE"   # Pantalla en negro (sin cortar corriente)


# ===========================================================================
# Dataclass CommandResult
# ===========================================================================
@dataclass
class CommandResult:
    """
    Modela la respuesta estándar que devuelven la mayoría de los endpoints
    de control de NovaCloud tras ejecutar un comando.

    Estructura de respuesta del API:
        {
            "success": ["<playerId_1>", "<playerId_2>"],
            "fail":    ["<playerId_3>"]
        }

    El API siempre devuelve ambas listas aunque estén vacías, lo que permite
    saber exactamente qué players ejecutaron el comando y cuáles fallaron,
    incluso en llamadas batch con múltiples players.

    Atributos:
        success : IDs de players que ejecutaron el comando exitosamente.
        fail    : IDs de players que fallaron o no pudieron ser alcanzados.
    """
    success: list[str]
    fail: list[str]

    @classmethod
    def from_dict(cls, data: dict) -> CommandResult:
        """
        Constructor alternativo que parsea el dict crudo del API.

        Usa .get(..., []) con default vacío para ser tolerante a
        respuestas parciales o errores donde el dict llegue incompleto
        (ej: body vacío {} cuando hay un error HTTP no esperado).
        """
        return cls(
            success=data.get("success", []),
            fail=data.get("fail", []),
        )

    def ok(self, player_id: str) -> bool:
        """
        Conveniencia: devuelve True si el player_id está en la lista de éxito.

        Uso típico en la integración de HA:
            result = await api.set_brightness(player_id, 80)
            if not result.ok(player_id):
                _LOGGER.warning("Brightness command failed for %s", player_id)
        """
        return player_id in self.success


# ===========================================================================
# Función auxiliar de autenticación (módulo privado)
# ===========================================================================
def _generate_checksum(app_secret: str, nonce: str, cur_time: str) -> str:
    """
    Calcula el CheckSum requerido por el API de NovaCloud.

    Algoritmo (documentado oficialmente):
        CheckSum = SHA256( AppSecret + Nonce + CurTime )

    Los tres valores se concatenan como strings (sin separadores) antes de
    codificar a UTF-8 y calcular el digest hexadecimal en minúsculas.

    Args:
        app_secret : Clave secreta de la aplicación (nunca se envía por red).
        nonce      : Token aleatorio generado para este request.
        cur_time   : Timestamp Unix del momento del request (string).

    Returns:
        String hex de 64 caracteres (SHA-256 = 256 bits = 32 bytes = 64 hex).

    Nota de seguridad:
        app_secret nunca viaja en los headers; sólo se usa localmente
        para calcular el checksum. El servidor verifica la firma
        reconstruyendo el mismo cálculo con el secret almacenado.
    """
    return hashlib.sha256(
        (app_secret + nonce + cur_time).encode("utf-8")
    ).hexdigest()


# ===========================================================================
# Cliente principal NovaCloudAPI
# ===========================================================================
class NovaCloudAPI:
    """
    Cliente HTTP asíncrono para la API abierta de NovaCloud / VNNOX.

    Diseñado para integrarse con el DataUpdateCoordinator de Home Assistant,
    donde una única instancia del cliente vive durante toda la vida útil
    de la integración y es compartida entre entidades (sensor, light, media_player).

    Gestión de sesión
    -----------------
    La sesión de aiohttp se crea de forma lazy (primera llamada) y se
    reutiliza en todas las requests posteriores. Esto es el patrón correcto
    según la documentación de aiohttp; crear una sesión por request es
    considerado un anti-patrón y genera overhead significativo.

    Uso básico:
        api = NovaCloudAPI(app_key, app_secret, webhook_url)
        players = await api.get_players()
        await api.close()   # importante al desmontar la integración

    Uso como context manager (recomendado en tests o scripts):
        async with NovaCloudAPI(app_key, app_secret) as api:
            players = await api.get_players()
        # la sesión se cierra automáticamente al salir del bloque
    """

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        webhook_url: str | None = None,
    ) -> None:
        """
        Args:
            app_key     : Clave pública de la aplicación (header AppKey).
            app_secret  : Clave secreta para firmar requests (no se envía).
            webhook_url : URL del endpoint de HA que recibirá las notificaciones
                          push del API (respuestas de RUNNING_STATUS, cambios
                          de fuente de video, cambios de solución, etc.).
                          Si es None, el campo noticeUrl se enviará como null
                          y el API no intentará notificar.
        """
        self.app_key     = app_key
        self.app_secret  = app_secret
        self.webhook_url = webhook_url

        # La sesión se inicializa a None y se crea lazily en _get_session().
        # El tipo explícito ayuda al type checker a entender que puede ser None.
        self._session: aiohttp.ClientSession | None = None

    # =========================================================================
    # Gestión del ciclo de vida de la sesión HTTP
    # =========================================================================

    async def _get_session(self) -> aiohttp.ClientSession:
        """
        Devuelve la sesión de aiohttp activa, creándola si no existe o si
        fue cerrada previamente (ej: tras un error de transporte grave).

        El flag auto_decompress=False desactiva la descompresión automática
        de respuestas gzip/deflate. Se mantiene para compatibilidad con el
        servidor de NovaCloud, que puede enviar el header Content-Encoding
        de formas no estándar. Sin este flag, aiohttp podría intentar
        descomprimir un body que ya viene sin comprimir y lanzar un error.
        """
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                auto_decompress=False,
                timeout=DEFAULT_TIMEOUT,    # Se aplica a todas las requests de esta sesión
            )
        return self._session

    async def close(self) -> None:
        """
        Cierra la sesión HTTP y libera los recursos de red subyacentes.

        Debe llamarse explícitamente cuando la integración se desmonta
        (en el método async_unload_entry de __init__.py) o al final
        de un script/test para evitar warnings de "unclosed session".

        La guarda doble (if self._session and not self._session.closed)
        hace que llamar a close() múltiples veces sea seguro (idempotente).
        """
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> NovaCloudAPI:
        """
        Soporte para uso como async context manager (async with).
        No necesita hacer nada especial al entrar; la sesión se crea lazy.
        Devuelve self para que el bloque as api: tenga la instancia.
        """
        return self

    async def __aexit__(self, *_: Any) -> None:
        """
        Se llama automáticamente al salir del bloque async with, con o sin
        excepción. *_ ignora los tres argumentos (exc_type, exc_val, exc_tb)
        porque no necesitamos manejarlos diferente; siempre cerramos la sesión.
        """
        await self.close()

    # =========================================================================
    # Construcción de headers de autenticación
    # =========================================================================

    def _auth_headers(self) -> dict[str, str]:
        """
        Construye los headers de autenticación requeridos por el API.

        Este método es síncrono porque no realiza I/O; sólo cálculos en memoria.
        Se llama de forma fresca en cada request para garantizar que Nonce y
        CurTime sean únicos y válidos en el momento exacto del envío.

        Headers generados:
            AppKey          : Identificador público de la app.
            Nonce           : 8 bytes aleatorios en hex (16 caracteres).
                              secrets.token_hex usa os.urandom internamente,
                              que es criptográficamente seguro (CSPRNG).
            CurTime         : Unix timestamp como string de entero de segundos.
                              int() trunca los microsegundos deliberadamente.
            CheckSum        : Firma SHA-256 calculada con _generate_checksum().
            Content-Type    : Requerido por el API para parsear el body JSON.
            Accept-Encoding : "identity" desactiva la negociación de compresión
                              HTTP, consistente con auto_decompress=False en la sesión.

        Returns:
            Dict con exactamente los 6 headers que espera el servidor.
        """
        nonce    = secrets.token_hex(8)          # Ej: "a3f8b2c1d4e5f607"
        cur_time = str(int(time.time()))         # Ej: "1741180800"
        checksum = _generate_checksum(self.app_secret, nonce, cur_time)
        return {
            "AppKey":          self.app_key,
            "Nonce":           nonce,
            "CurTime":         cur_time,
            "CheckSum":        checksum,
            "Content-Type":    "application/json; charset=utf-8",
            "Accept-Encoding": "identity",
        }

    # =========================================================================
    # Métodos HTTP de bajo nivel (privados)
    # =========================================================================

    async def _get(self, path: str, params: dict | None = None) -> dict:
        """
        Realiza una request GET autenticada y devuelve el body parseado como dict.

        Args:
            path   : Path del endpoint (ej: "/v2/player/list").
                     Se concatena con API_BASE para formar la URL completa.
            params : Query string params opcionales (ej: {"count": 100, "start": 0}).

        Returns:
            Dict con la respuesta del API, o {} en caso de error HTTP o de red.
            Nunca lanza excepciones; los errores se loguean y se retorna vacío
            para que el coordinator de HA los trate como "datos no disponibles".

        Manejo de errores:
            - HTTP != 200       : se loguea el status y el body como error.
            - aiohttp.ClientError : cubre errores de conexión, DNS, SSL, etc.
            - asyncio.TimeoutError: la request tardó más de DEFAULT_TIMEOUT.

        Nota sobre content_type=None en resp.json():
            Por defecto, aiohttp valida que el Content-Type sea "application/json".
            Algunos servidores devuelven "application/json; charset=utf-8" u otras
            variantes. Con content_type=None se desactiva esa validación y
            aiohttp parsea el body como JSON independientemente del header.
        """
        url     = f"{API_BASE}{path}"
        session = await self._get_session()
        try:
            async with session.get(url, headers=self._auth_headers(), params=params) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                # Loguear el body completo en errores ayuda a diagnosticar
                # problemas de autenticación o parámetros inválidos.
                text = await resp.text()
                _LOGGER.error("GET %s failed [%s]: %s", path, resp.status, text)
                return {}
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("GET %s error: %s", path, err)
            return {}

    async def _post(self, path: str, payload: dict) -> dict:
        """
        Realiza una request POST autenticada con body JSON y devuelve el response.

        Es el método central de la integración: casi todos los comandos de
        control usan POST con un payload JSON que incluye al menos playerIds.

        Args:
            path    : Path del endpoint (ej: "/v2/player/real-time-control/brightness").
            payload : Dict que se serializa como JSON en el body del request.
                      aiohttp añade automáticamente Content-Type: application/json
                      cuando se usa el kwarg json=, pero también lo incluimos
                      explícitamente en los headers por requerimiento del API.

        Returns:
            Dict con la respuesta del API (generalmente {"success": [...], "fail": [...]}),
            o {} en caso de error. Nunca lanza excepciones.

        La diferencia con _get() es que aquí devolvemos el dict completo en lugar
        de sólo bool, para que los callers puedan inspeccionar qué players
        fallaron y loguear o reintentar selectivamente.
        """
        url     = f"{API_BASE}{path}"
        session = await self._get_session()
        try:
            async with session.post(url, headers=self._auth_headers(), json=payload) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                text = await resp.text()
                _LOGGER.error("POST %s failed [%s]: %s", path, resp.status, text)
                return {}
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("POST %s error: %s", path, err)
            return {}

    # =========================================================================
    # Player Management
    # =========================================================================

    async def get_players(self, count: int = 100, start: int = 0) -> dict:
        """
        Obtiene la lista paginada de players de la cuenta.

        Endpoint: GET /v2/player/list

        El API devuelve hasta `count` players por página, empezando desde
        el índice `start`. Para cuentas con más de 100 players, llamar
        repetidamente incrementando `start` de 100 en 100.

        Args:
            count : Número máximo de players a devolver por página (default: 100).
            start : Índice de inicio para paginación (default: 0).

        Returns:
            Dict con la lista de players y metadata de paginación.
            Estructura típica:
                {
                    "total": 3,
                    "list": [
                        {"id": "...", "name": "...", "sn": "...", ...},
                    ]
                }
        """
        return await self._get(Endpoints.PLAYER_LIST, {"count": count, "start": start})

    # =========================================================================
    # Player Status
    # =========================================================================

    async def get_online_status(self, player_ids: list[str]) -> list[dict]:
        """
        Consulta el estado de conectividad en tiempo real de uno o más players.

        Endpoint: POST /v2/player/current/online-status

        Esta llamada es síncrona: el resultado llega directamente en la respuesta
        HTTP (a diferencia de RUNNING_STATUS que es asíncrono/webhook).

        Campos disponibles en cada item de la respuesta:
            playerId       : ID único del player.
            sn             : Número de serie del hardware.
            onlineStatus   : 1 = online (conectado), 0 = offline.
            lastOnlineTime : Última vez que el player estuvo online (si está offline).

        Args:
            player_ids : Lista de IDs de players a consultar.
                         El API acepta hasta 100 IDs por llamada.

        Returns:
            Lista de dicts con el estado de cada player, o [] si hubo error.
            La guarda isinstance(result, list) protege contra respuestas
            inesperadas donde el API devuelva un dict en lugar de lista.

        Uso típico:
            statuses = await api.get_online_status([player_id])
            is_online = statuses[0]["onlineStatus"] == 1 if statuses else False
        """
        payload = {"playerIds": player_ids}
        result  = await self._post(Endpoints.ONLINE_STATUS, payload)
        # _post() siempre retorna dict, pero este endpoint devuelve una lista.
        # Verificamos el tipo real del body parseado antes de retornar.
        return result if isinstance(result, list) else []

    async def get_status_data(
        self,
        player_id: str,
        commands: list[str] | None = None,
    ) -> CommandResult:
        """
        Solicita el estado de configuración actual del player de forma ASÍNCRONA.

        Endpoint: POST /v2/player/current/running-status

        IMPORTANTE — Modelo asíncrono de este endpoint:
            El HTTP 200 de este endpoint NO contiene los datos solicitados.
            Sólo confirma que el comando fue aceptado y encolado.
            Los datos reales (brillo, volumen, etc.) llegan posteriormente
            como un HTTP POST de NovaCloud a la URL configurada en noticeUrl
            (el webhook de Home Assistant). Por eso este método retorna
            CommandResult sólo con success/fail, sin los valores en sí.

        Args:
            player_id : ID del player al que se le solicita el estado.
            commands  : Lista de RunningStatusCommand a solicitar.
                        Si es None, se solicitan los 4 comandos disponibles:
                        volumeValue, brightnessValue, videoSourceValue, timeValue.

        Returns:
            CommandResult indicando si el player aceptó la solicitud.
            Los datos reales se recibirán en el webhook configurado.

        Payload enviado al API:
            {
                "playerIds":  ["<id>"],
                "commands":   ["volumeValue", "brightnessValue", ...],
                "noticeUrl":  "http://<ha-host>/api/webhook/novacloud"
            }
        """
        payload = {
            "playerIds": [player_id],
            # `commands or RunningStatusCommand.ALL` usa ALL si commands es None o [].
            "commands":  commands or RunningStatusCommand.ALL,
            # Si webhook_url es None, NovaCloud no intentará notificar.
            "noticeUrl": self.webhook_url,
        }
        _LOGGER.debug("Sending running-status request: %s", payload)
        result = await self._post(Endpoints.RUNNING_STATUS, payload)
        return CommandResult.from_dict(result)

    # =========================================================================
    # Real-Time Control
    # =========================================================================

    async def set_screen_status(
        self, player_id: str, status: str = ScreenStatus.OPEN
    ) -> CommandResult:
        """
        Controla el estado visual de la pantalla (negro o activo).

        Endpoint: POST /v2/player/real-time-control/screen-status

        NO corta la alimentación eléctrica del panel; simplemente
        envía una señal al controlador para mostrar contenido o negro.
        Para apagar físicamente, usar set_screen_power().

        Args:
            player_id : ID del player objetivo.
            status    : ScreenStatus.OPEN (imagen normal) o
                        ScreenStatus.CLOSE (pantalla en negro).
                        Default: OPEN.

        Returns:
            CommandResult con los IDs en success o fail.
        """
        result = await self._post(
            Endpoints.SCREEN_STATUS,
            {"playerIds": [player_id], "status": status},
        )
        return CommandResult.from_dict(result)

    async def set_brightness(self, player_id: str, value: int) -> CommandResult:
        """
        Ajusta el nivel de brillo del panel LED en tiempo real.

        Endpoint: POST /v2/player/real-time-control/brightness

        Args:
            player_id : ID del player objetivo.
            value     : Nivel de brillo entre 0 (mínimo) y 100 (máximo).
                        El API no valida el rango; un valor fuera de 0-100
                        puede tener comportamiento indefinido en el hardware.

        Returns:
            CommandResult con los IDs en success o fail.
        """
        result = await self._post(
            Endpoints.BRIGHTNESS,
            {"playerIds": [player_id], "value": value},
        )
        return CommandResult.from_dict(result)

    async def set_volume(self, player_id: str, value: int) -> CommandResult:
        """
        Ajusta el nivel de volumen del player en tiempo real.

        Endpoint: POST /v2/player/real-time-control/volume

        Args:
            player_id : ID del player objetivo.
            value     : Nivel de volumen entre 0 (silencio) y 100 (máximo).

        Returns:
            CommandResult con los IDs en success o fail.
        """
        result = await self._post(
            Endpoints.VOLUME,
            {"playerIds": [player_id], "value": value},
        )
        return CommandResult.from_dict(result)

    async def set_video_source(self, player_id: str, source: int) -> CommandResult:
        """
        Cambia la fuente de entrada de video activa en el player.

        Endpoint: POST /v2/player/real-time-control/video-source

        Las fuentes disponibles dependen del hardware del player
        (HDMI 1, HDMI 2, DVI, etc.) y se identifican por índice numérico.
        Consultar la documentación del modelo específico de player para
        saber qué índice corresponde a cada entrada física.

        Args:
            player_id : ID del player objetivo.
            source    : Índice entero de la fuente de video (empieza en 0 o 1
                        dependiendo del firmware del player).

        Returns:
            CommandResult con los IDs en success o fail.
        """
        result = await self._post(
            Endpoints.VIDEO_SOURCE,
            {"playerIds": [player_id], "source": source},
        )
        return CommandResult.from_dict(result)

    async def reboot_player(self, player_id: str) -> CommandResult:
        """
        Reinicia el sistema operativo del player de forma remota.

        Endpoint: POST /v2/player/real-time-control/reboot

        El player quedará offline durante el proceso de arranque
        (típicamente 60-120 segundos). El coordinator de HA debería
        manejar el período de indisponibilidad marcando la entidad como
        unavailable hasta que get_online_status() devuelva onlineStatus=1.

        Args:
            player_id : ID del player a reiniciar.

        Returns:
            CommandResult con los IDs en success o fail.
        """
        result = await self._post(
            Endpoints.REBOOT,
            {"playerIds": [player_id]},
        )
        return CommandResult.from_dict(result)

    async def set_screen_power(self, player_id: str, power_on: bool) -> CommandResult:
        """
        Controla la alimentación física del panel de la pantalla.

        Endpoint: POST /v2/player/power/onOrOff

        Diferencia clave con set_screen_status():
            - set_screen_status() : pone la imagen en negro pero el hardware
              sigue encendido y el player sigue recibiendo señal.
            - set_screen_power()  : corta/restaura la alimentación del panel
              físico. En algunos modelos, apagar el panel también detiene la
              señal de video. Si se apaga el panel, NO se puede volver a
              encender remotamente en todos los modelos (depende de hardware).

        Args:
            player_id : ID del player objetivo.
            power_on  : True para encender el panel, False para apagarlo.
                        Se convierte internamente a "OPEN" / "CLOSE" según
                        el esquema del API.

        Returns:
            CommandResult con los IDs en success o fail.
        """
        result = await self._post(
            Endpoints.SCREEN_POWER,
            # Traducción explícita bool → string del API para máxima claridad.
            {"playerIds": [player_id], "status": "OPEN" if power_on else "CLOSE"},
        )
        return CommandResult.from_dict(result)

    async def take_screenshot(self, player_id: str) -> CommandResult:
        """
        Dispara una captura de pantalla en el player.

        Endpoint: POST /v2/player/real-time-control/screenshot

        La imagen capturada se almacena en la nube de NovaCloud y puede
        consultarse desde el portal web. Este método sólo dispara la
        captura; no devuelve la imagen directamente.

        Args:
            player_id : ID del player del que se quiere captura.

        Returns:
            CommandResult con los IDs en success o fail.
        """
        result = await self._post(
            Endpoints.SCREENSHOT,
            {"playerIds": [player_id]},
        )
        return CommandResult.from_dict(result)

    # =========================================================================
    # Scheduled Control
    # =========================================================================

    async def set_scheduled_screen_status(
        self,
        player_ids: list[str],
        schedules: list[dict],
    ) -> CommandResult:
        """
        Programa cambios automáticos del estado de pantalla (OPEN/CLOSE).

        Endpoint: POST /v2/player/scheduled-control/screen-status

        Permite definir múltiples franjas horarias en las que la pantalla
        se abrirá o cerrará automáticamente sin intervención manual.
        Ideal para apagar pantallas fuera del horario comercial.

        Límite: máximo 100 players por llamada.

        Args:
            player_ids : Lista de IDs de players a programar.
            schedules  : Lista de items de programación. Cada item:
                {
                    "startDate": "2025-01-01",    # Inicio del rango de fechas
                    "endDate":   "2025-12-31",    # Fin del rango de fechas
                    "weekDays":  [1, 2, 3, 4, 5], # 0=Dom ... 6=Sab; [] = todos
                    "execTime":  "06:30:00",       # Hora de ejecución HH:MM:SS
                    "status":    "OPEN"            # "OPEN" | "CLOSE"
                }

        Returns:
            CommandResult con los IDs en success o fail.
        """
        result = await self._post(
            Endpoints.SCHEDULED_SCREEN_STATUS,
            {"playerIds": player_ids, "schedules": schedules},
        )
        return CommandResult.from_dict(result)

    async def set_scheduled_volume(
        self,
        player_ids: list[str],
        schedules: list[dict],
    ) -> CommandResult:
        """
        Programa cambios automáticos del nivel de volumen.

        Endpoint: POST /v2/player/scheduled-control/volume

        Útil para reducir el volumen en horario nocturno o aumentarlo
        durante eventos específicos de forma automática.

        Límite: máximo 100 players por llamada.

        Args:
            player_ids : Lista de IDs de players a programar.
            schedules  : Lista de items de programación. Cada item:
                {
                    "startDate": "2025-01-01",
                    "endDate":   "2025-12-31",
                    "weekDays":  [0, 5, 6],   # Fin de semana (opcional)
                    "execTime":  "08:30:00",
                    "value":     55           # Volumen objetivo 0-100
                }

        Returns:
            CommandResult con los IDs en success o fail.
        """
        result = await self._post(
            Endpoints.SCHEDULED_VOLUME,
            {"playerIds": player_ids, "schedules": schedules},
        )
        return CommandResult.from_dict(result)

    async def set_scheduled_brightness(
        self,
        player_ids: list[str],
        schedules: list[dict],
        auto_profile: dict | None = None,
    ) -> CommandResult:
        """
        Programa cambios automáticos del nivel de brillo.

        Endpoint: POST /v2/player/scheduled-control/brightness

        Soporta dos modos por schedule item:
          - type=1 (manual)    : brillo fijo al valor especificado en value.
          - type=2 (automático): el player ajusta el brillo según un perfil
                                 de sensores ambientales, definido en auto_profile.

        Límite: máximo 100 players por llamada.

        Args:
            player_ids   : Lista de IDs de players a programar.
            schedules    : Lista de items de programación. Cada item:
                {
                    "startDate": "2025-01-01",
                    "endDate":   "2025-12-31",
                    "weekDays":  [1, 2, 3, 4, 5],
                    "execTime":  "07:30:00",
                    "type":      1,     # 1 = manual, 2 = automatico
                    "value":     40     # Brillo 0-100 (solo cuando type=1)
                }
            auto_profile : Perfil de brillo automático basado en sensores de
                           luz ambiental. Solo aplica cuando algún item tiene
                           type=2. Si es None, no se incluye en el payload
                           (evitamos enviar la clave con valor null al API).

        Returns:
            CommandResult con los IDs en success o fail.
        """
        # Construimos el payload base y añadimos autoProfile sólo si existe,
        # evitando enviar la clave con valor None al API.
        payload: dict[str, Any] = {"playerIds": player_ids, "schedules": schedules}
        if auto_profile:
            payload["autoProfile"] = auto_profile
        result = await self._post(Endpoints.SCHEDULED_BRIGHTNESS, payload)
        return CommandResult.from_dict(result)

    async def set_scheduled_video_source(
        self,
        player_ids: list[str],
        schedules: list[dict],
    ) -> CommandResult:
        """
        Programa cambios automáticos de la fuente de video activa.

        Endpoint: POST /v2/player/scheduled-control/video-source

        Útil para alternar automáticamente entre contenido propio (señal
        interna del player) y señal externa (HDMI) según el horario.

        Límite: máximo 100 players por llamada.

        Args:
            player_ids : Lista de IDs de players a programar.
            schedules  : Lista de items de programación. Cada item debe
                         incluir al menos startDate, endDate, execTime
                         y el índice de fuente de video destino.
                         Consultar documentación oficial para el shape
                         exacto según la versión del firmware del player.

        Returns:
            CommandResult con los IDs en success o fail.
        """
        result = await self._post(
            Endpoints.SCHEDULED_VIDEO_SOURCE,
            {"playerIds": player_ids, "schedules": schedules},
        )
        return CommandResult.from_dict(result)
