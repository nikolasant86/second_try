import http.server
import socketserver
import json
import logging
import os
import re
import time
import urllib.request
from urllib.parse import urlparse, parse_qs
import uuid

# --- Конфигурация из переменных окружения ---
LOG_DIR = os.getenv("WEATHER_SERVICE_LOG_DIR", "/var/log/weather_service")
LOG_FILE = os.getenv("WEATHER_SERVICE_LOG_FILE", os.path.join(LOG_DIR, "app.log"))
LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

API_KEY = os.getenv("OPENWEATHER_API_KEY")
OPENWEATHER_URL = os.getenv(
    "OPENWEATHER_URL", "https://api.openweathermap.org/data/2.5/weather"
)

PORT = int(os.getenv("WEATHER_SERVICE_PORT", "8002"))


def validate_environment():
    """Проверка критических переменных окружения"""
    errors = []

    if not API_KEY:
        errors.append(
            "⚠️  ВНИМАНИЕ: Используется тестовый API-ключ OpenWeatherMap. Замените его на реальный!"
        )

    if not os.path.exists(LOG_DIR):
        try:
            os.makedirs(LOG_DIR, mode=0o755)
        except OSError as e:
            errors.append(f"Ошибка создания директории логов: {e}")

    # Проверка прав на запись в лог-директорию
    test_log_path = os.path.join(LOG_DIR, ".test_write")
    try:
        with open(test_log_path, "w") as f:
            f.write("test")
        os.remove(test_log_path)
    except OSError:
        errors.append(f"Нет прав на запись в директорию логов: {LOG_DIR}")

    return errors


# Проверка окружения при старте
env_errors = validate_environment()
for error in env_errors:
    print(error)

# Если есть критические ошибки — выходим
if any("Ошибка" in e or "нет прав" in e for e in env_errors):
    print("❌ Критические ошибки окружения. Завершение.")
    exit(1)


# --- JSON-форматтер для логов ---
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": logging.Formatter.converter(record.created),
            "timestamp_iso": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", logging.Formatter.converter(record.created)
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "request_id": getattr(record, "request_id", str(uuid.uuid4())),
        }

        # Добавляем все поля из extra, кроме стандартных
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
                "request_id",
            ):
                if key not in log_entry:
                    log_entry[key] = value

        return json.dumps(log_entry, ensure_ascii=False, default=str)


# --- Настройка логгера ---
logger = logging.getLogger("weather_service")
logger.setLevel(LOG_LEVEL)
logger.handlers.clear()  # Убираем дубликаты

# Создаём файловый хендлер
file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setLevel(LOG_LEVEL)
file_handler.setFormatter(JSONFormatter())

logger.addHandler(file_handler)
logger.propagate = False  # Предотвращает дублирование в root-логгер


# --- Фильтр для автодобавления полей ---
class WeatherContextFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "client_ip"):
            record.client_ip = "unknown"
        if not hasattr(record, "request_target"):
            record.request_target = "unknown"
        if not hasattr(record, "requested_city"):
            record.requested_city = "unknown"
        if not hasattr(record, "response_status"):
            record.response_status = "000"
        if not hasattr(record, "response_data"):
            record.response_data = "{}"
        if not hasattr(record, "api_response"):
            record.api_response = "none"
        if not hasattr(record, "duration_ms"):
            record.duration_ms = 0
        return True


logger.addFilter(WeatherContextFilter())


def is_valid_city_name(city: str) -> bool:
    if not city or len(city) > 100:
        return False
    return bool(
        re.match(r"^[a-zA-Zа-яА-ЯёЁ\s\-\'\.]+$", city)
    )  # ✅ ИСПРАВЛЕНО: лишняя запятая в регулярке


def fetch_weather(city_name: str) -> dict:
    if not is_valid_city_name(city_name):
        logger.warning(
            "Недопустимое название города",
            extra={
                "requested_city": city_name,
                "response_status": 400,
                "response_data": '{"error": "Недопустимое название города"}',
                "api_response": "invalid_input",
            },
        )
        return None

    params = {"q": city_name, "appid": API_KEY, "units": "metric", "lang": "ru"}

    start_time = time.time()

    try:
        url = OPENWEATHER_URL + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            duration_ms = int((time.time() - start_time) * 1000)

            weather_desc = data["weather"][0]["description"]
            temp = data["main"]["temp"]

            result = {"description": weather_desc, "temp": round(temp, 1)}

            logger.info(
                f"Успешно получена погода: {weather_desc}, {temp}°C",
                extra={
                    "requested_city": city_name,
                    "response_status": 200,
                    "response_data": json.dumps(
                        {"weather": result}, ensure_ascii=False
                    ),
                    "api_response": "success",
                    "duration_ms": duration_ms,
                },
            )
            return result

    except urllib.error.HTTPError as e:
        duration_ms = int((time.time() - start_time) * 1000)
        error_msg = f"HTTP {e.code}: {e.reason}"
        logger.error(
            f"OpenWeatherMap вернул {e.code}",
            extra={
                "requested_city": city_name,
                "response_status": e.code,
                "response_data": '{"error": "Не удалось получить погоду"}',
                "api_response": error_msg,
                "duration_ms": duration_ms,
            },
        )
    except urllib.error.URLError as e:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error(
            f"Ошибка сети: {e.reason}",
            extra={
                "requested_city": city_name,
                "response_status": 502,
                "response_data": '{"error": "Ошибка сети"}',
                "api_response": f"network_error: {str(e.reason)}",
                "duration_ms": duration_ms,
            },
        )
    except json.JSONDecodeError:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error(
            "Некорректный ответ от OpenWeatherMap",
            extra={
                "requested_city": city_name,
                "response_status": 500,
                "response_data": '{"error": "Некорректный ответ от погодного сервиса"}',
                "api_response": "invalid_json",
                "duration_ms": duration_ms,
            },
        )
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.exception(
            f"Неожиданная ошибка при обработке погоды",
            extra={
                "requested_city": city_name,
                "response_status": 500,
                "response_data": '{"error": "Внутренняя ошибка сервера"}',
                "api_response": f"unknown_error: {str(e)}",
                "duration_ms": duration_ms,
            },
        )

    return None


class WeatherHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        client_ip = self.client_address[0]
        request_target = self.path
        start_time = time.time()

        # Генерируем request_id для трассировки
        request_id = str(uuid.uuid4())

        logger.info(
            "Получен POST-запрос",
            extra={
                "client_ip": client_ip,
                "request_target": request_target,
                "requested_city": "pending",
                "response_status": 0,
                "response_data": "processing",
                "api_response": "pending",
                "duration_ms": 0,
                "request_id": request_id,  # ✅ Добавляем request_id в extra
            },
        )

        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length).decode("utf-8")

        try:
            data = json.loads(post_data)
            city = data.get("city", "").strip()
            if not city:
                raise ValueError("Поле 'city' пустое")
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(
                f"Неверный JSON или отсутствует city: {e}",
                extra={
                    "client_ip": client_ip,
                    "request_target": request_target,
                    "requested_city": "invalid_json",
                    "response_status": 400,
                    "response_data": '{"error": "Неверный JSON или отсутствует поле city"}',
                    "api_response": "invalid_json",
                    "duration_ms": int((time.time() - start_time) * 1000),
                    "request_id": request_id,
                },
            )
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"error": "Неверный JSON или отсутствует поле city"},
                    ensure_ascii=False,
                ).encode("utf-8")
            )
            return

        logger.info(
            f"Получен город от geo-service: {city}",
            extra={
                "client_ip": client_ip,
                "request_target": request_target,
                "requested_city": city,
                "response_status": 200,
                "response_data": "ok",
                "api_response": "city_received",
                "duration_ms": int((time.time() - start_time) * 1000),
                "request_id": request_id,
            },
        )

        weather_data = fetch_weather(city)

        if weather_data:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response_body = json.dumps({"weather": weather_data}, ensure_ascii=False)
            self.wfile.write(response_body.encode("utf-8"))
            logger.info(
                "Отправлен ответ с погодой",
                extra={
                    "client_ip": client_ip,
                    "request_target": request_target,
                    "requested_city": city,
                    "response_status": 200,
                    "response_data": response_body,
                    "api_response": "success",
                    "duration_ms": int((time.time() - start_time) * 1000),
                    "request_id": request_id,
                },
            )
        else:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response_body = json.dumps(
                {"error": "Не удалось получить погоду"}, ensure_ascii=False
            )
            self.wfile.write(response_body.encode("utf-8"))
            logger.error(
                "Не удалось получить погоду",
                extra={
                    "client_ip": client_ip,
                    "request_target": request_target,
                    "requested_city": city,
                    "response_status": 500,
                    "response_data": response_body,
                    "api_response": "failed",
                    "duration_ms": int((time.time() - start_time) * 1000),
                    "request_id": request_id,
                },
            )

    def do_GET(self):
        client_ip = self.client_address[0]
        logger.warning(
            "Попытка GET-запроса к weather-service (запрещено)",
            extra={
                "client_ip": client_ip,
                "request_target": self.path,
                "requested_city": "blocked_get",
                "response_status": 405,
                "response_data": '{"error": "Метод не поддерживается"}',
                "api_response": "method_not_allowed",
                "duration_ms": 0,
                "request_id": str(uuid.uuid4()),
            },
        )
        self.send_response(405)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps({"error": "Метод не поддерживается"}, ensure_ascii=False).encode(
                "utf-8"
            )
        )

    def do_OPTIONS(self):
        client_ip = self.client_address[0]
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        logger.info(
            "CORS preflight ответ",
            extra={
                "client_ip": client_ip,
                "request_target": self.path,
                "requested_city": "cors",
                "response_status": 200,
                "response_data": "{}",
                "api_response": "cors_allowed",
                "duration_ms": 0,
                "request_id": str(uuid.uuid4()),
            },
        )


# Запуск сервера
if __name__ == "__main__":
    # Логирование настроек при старте — в JSON
    logger.info(
        "Сервер weather-service запускается с настройками:",
        extra={
            "port": PORT,
            "log_dir": LOG_DIR,
            "log_file": LOG_FILE,
            "log_level": logging.getLevelName(LOG_LEVEL),
            "openweather_url": OPENWEATHER_URL,
            "api_key_set": bool(API_KEY),
            "api_key_masked": "*" * len(API_KEY) if API_KEY else "not_set",
        },
    )

    server_address = ("", PORT)
    httpd = socketserver.TCPServer(server_address, WeatherHandler)
    print(f"🌐 weather-service запущен на порту {PORT}")
    logger.info(f"Сервер weather-service запущен на порту {PORT}")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Сервер weather-service остановлен")
        httpd.server_close()
