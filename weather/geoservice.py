import http.server
import socketserver
import json
import logging
import os
import re
import socket
import urllib.request
import urllib.parse
from urllib.parse import urlparse
from http.client import HTTPConnection
from datetime import datetime
import uuid

# --- Конфигурация из переменных окружения ---
LOG_DIR = os.getenv("GEOSERVICE_LOG_DIR", "/var/log/geoservice")
LOG_FILE = os.getenv("GEOSERVICE_LOG_FILE", os.path.join(LOG_DIR, "geo_service.log"))
LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO"))  # По умолчанию INFO

# DaData
DADATA_API_URL = os.getenv(
    "DADATA_API_URL",
    "https://suggestions.dadata.ru/suggestions/api/4_1/rs/iplocate/address",
)
DADATA_TOKEN = os.getenv("DADATA_TOKEN")

# Weather Service (внутренний)
WEATHER_SERVICE_HOST = os.getenv("WEATHER_SERVICE_HOST", "weather_service")
WEATHER_SERVICE_PORT = int(os.getenv("WEATHER_SERVICE_PORT", "8002"))
WEATHER_SERVICE_PATH = os.getenv("WEATHER_SERVICE_PATH", "/api/weather")

# Локальные IP-шаблоны
LOCAL_IP_PATTERNS = [
    r"^127\.",
    r"^10\.",
    r"^172\.(1[6-9]|2[0-9]|3[0-1])\.",
    r"^192\.168\.",
    r"^169\.254\.",
    r"^::1",
]


def validate_environment():
    """Проверка критических переменных окружения"""
    errors = []

    if not DADATA_TOKEN:
        errors.append(
            "⚠️  ВНИМАНИЕ: Используется тестовый токен DaData. Замените его на реальный!"
        )

    if not os.path.exists(LOG_DIR):
        try:
            os.makedirs(LOG_DIR)
        except OSError as e:
            errors.append(f"Ошибка создания директории логов: {e}")

    return errors


# Проверка окружения при старте
env_errors = validate_environment()
for error in env_errors:
    print(error)


# --- JSON-форматтер для логов ---
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcfromtimestamp(record.created).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Добавляем все поля из extra
        for key, value in record.__dict__.items():
            if key not in (
                "asctime",
                "created",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "msg",
                "name",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "thread",
                "threadName",
            ):
                if key not in log_entry:
                    log_entry[key] = value

        return json.dumps(log_entry, ensure_ascii=False)


# --- Настройка логирования (КРИТИЧЕСКИЙ ИСПРАВЛЕННЫЙ БЛОК) ---
logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)  # ✅ УСТАНАВЛИВАЕМ УРОВЕНЬ ЛОГГЕРА ПЕРЕД ОБРАБОТЧИКАМИ!
logger.handlers.clear()  # Убираем возможные дубликаты (например, от других модулей)

# Файл-обработчик с JSON-форматтером
file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setFormatter(JSONFormatter())
logger.addHandler(file_handler)

# Консольный вывод — отключён в продакшене. Включить при отладке:
# console_handler = logging.StreamHandler()
# console_handler.setFormatter(JSONFormatter())
# logger.addHandler(console_handler)

# Не наследовать логи от родительских логгеров (чтобы избежать дублирования)
logger.propagate = False


# --- Фильтр для добавления дефолтных полей ---
class ContextFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "client_ip"):
            record.client_ip = "unknown"
        if not hasattr(record, "action"):
            record.action = "unknown"
        if not hasattr(record, "city"):
            record.city = "unknown"
        if not hasattr(record, "status"):
            record.status = "000"
        return True


logger.addFilter(ContextFilter())


# --- Основной код приложения ---
def is_local_ip(ip: str) -> bool:
    if not ip:
        return True
    for pattern in LOCAL_IP_PATTERNS:
        if re.match(pattern, ip):
            return True
    return False


def get_public_ip() -> str:
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=3) as response:
            return response.read().decode("utf-8").strip()
    except Exception as e:
        logger.warning(
            "Не удалось получить публичный IP",
            extra={"action": "get_public_ip", "error": str(e)},
        )
        return "8.8.8.8"


def get_city_by_ip(ip: str) -> str:
    try:
        data = json.dumps({"ip": ip}).encode("utf-8")
        req = urllib.request.Request(
            url=DADATA_API_URL,
            data=data,
            headers={
                "Authorization": f"Token {DADATA_TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))
            location = result.get("location", {})
            data = location.get("data", {})
            city = data.get("city") or data.get("region") or "Неизвестно"
            logger.info(
                "DaData вернул город",
                extra={"action": "dadata_success", "city": city, "ip": ip},
            )
            return city
    except Exception as e:
        logger.error(
            "Ошибка DaData", extra={"action": "dadata_error", "ip": ip, "error": str(e)}
        )
        return "Сервис временно недоступен"


def send_city_to_weather_service(city: str, client_ip: str) -> dict:
    """
    Отправляет город в POST-запросе на weather_service
    Возвращает ответ в виде dict
    """
    try:
        payload = json.dumps({"city": city}).encode("utf-8")
        conn = HTTPConnection(WEATHER_SERVICE_HOST, WEATHER_SERVICE_PORT, timeout=5)
        conn.request(
            "POST",
            WEATHER_SERVICE_PATH,
            body=payload,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
                "User-Agent": "geoservice",
            },
        )
        response = conn.getresponse()
        response_data = response.read().decode("utf-8")
        conn.close()

        if response.status != 200:
            logger.error(
                "weather-service вернул ошибку",
                extra={
                    "action": "weather_service_error",
                    "status": response.status,
                    "response": response_data,
                    "city": city,
                    "client_ip": client_ip,
                },
            )
            return {"error": f"weather-service: HTTP {response.status}"}

        weather_result = json.loads(response_data)
        logger.info(
            "Погода получена от weather-service",
            extra={
                "action": "weather_service_success",
                "city": city,
                "weather": weather_result,
                "client_ip": client_ip,
            },
        )
        return weather_result

    except Exception as e:
        logger.exception(
            "Ошибка при отправке на weather-service",
            extra={
                "action": "weather_service_exception",
                "city": city,
                "client_ip": client_ip,
                "error": str(e),
            },
        )
        return {"error": "Не удалось связаться с погодным сервисом"}


class CityHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        client_ip = self.client_address[0]
        request_id = str(uuid.uuid4())  # Генерируем уникальный ID для трассировки

        logger.info(
            "Получен GET-запрос",
            extra={
                "client_ip": client_ip,
                "action": "get_request",
                "request_id": request_id,
            },
        )

        if self.path != "/api/get_city":
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Not Found"}')
            logger.warning(
                "Неверный путь",
                extra={
                    "client_ip": client_ip,
                    "action": "path_not_found",
                    "status": 404,
                    "path": self.path,
                    "request_id": request_id,
                },
            )
            return

        x_real_ip = self.headers.get("X-Real-IP")
        x_forwarded_for = self.headers.get("X-Forwarded-For")

        if x_real_ip:
            client_ip = x_real_ip.strip()
        elif x_forwarded_for:
            client_ip = x_forwarded_for.split(",")[0].strip()

        logger.info(
            "IP получен из заголовков",
            extra={
                "client_ip": client_ip,
                "action": "ip_initial",
                "request_id": request_id,
            },
        )

        if is_local_ip(client_ip):
            logger.info(
                "Локальный IP, запрашиваем публичный",
                extra={
                    "client_ip": client_ip,
                    "action": "ip_local",
                    "request_id": request_id,
                },
            )
            client_ip = get_public_ip()
            logger.info(
                "IP заменён на публичный",
                extra={
                    "client_ip": client_ip,
                    "action": "ip_replaced",
                    "request_id": request_id,
                },
            )

        city = get_city_by_ip(client_ip)
        logger.info(
            "Город определён",
            extra={
                "client_ip": client_ip,
                "action": "city_determined",
                "city": city,
                "request_id": request_id,
            },
        )

        weather_response = send_city_to_weather_service(city, client_ip)

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

        if "error" in weather_response:
            response_body = f"Ошибка: {weather_response['error']}"
        else:
            weather_data = weather_response.get("weather", {})
            description = weather_data.get("description", "неизвестно")
            temp = weather_data.get("temp", "неизвестно")
            if isinstance(temp, (int, float)):
                temp = round(temp, 1)
            response_body = f"{city} {description} {temp}"

        self.wfile.write(response_body.encode("utf-8"))
        logger.info(
            "Отправлен ответ браузеру",
            extra={
                "client_ip": client_ip,
                "action": "response_sent",
                "status": 200,
                "response": response_body,
                "request_id": request_id,
            },
        )

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()
        logger.info("CORS preflight ответ", extra={"action": "cors_preflight"})

    def do_POST(self):
        self.send_response(405)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error": "Method Not Allowed"}')
        logger.warning("Попытка POST на geo-service", extra={"action": "post_blocked"})


# Запуск сервера
if __name__ == "__main__":
    PORT = int(os.getenv("GEOSERVICE_PORT", "7999"))

    # Логирование настроек при старте
    logger.info(
        "Сервер geoservice запускается с настройками",
        extra={
            "action": "server_start",
            "port": PORT,
            "log_dir": LOG_DIR,
            "log_file": LOG_FILE,
            "log_level": logging.getLevelName(LOG_LEVEL),
            "dadata_url": DADATA_API_URL,
            "weather_host": WEATHER_SERVICE_HOST,
            "weather_port": WEATHER_SERVICE_PORT,
            "weather_path": WEATHER_SERVICE_PATH,
        },
    )

    server_address = ("", PORT)
    httpd = socketserver.TCPServer(server_address, CityHandler)
    print(f"🌐 geoservice запущен на порту {PORT}")
    logger.info(
        "Сервер geoservice запущен", extra={"action": "server_started", "port": PORT}
    )

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Сервер geoservice остановлен", extra={"action": "server_stopped"})
        httpd.server_close()
