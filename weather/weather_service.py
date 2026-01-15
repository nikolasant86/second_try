import http.server
import socketserver
import json
import logging
import os
import re
import time
import urllib.request
from urllib.parse import urlparse, parse_qs

# --- Конфигурация ---
LOG_DIR = "/var/log/weather_service"
LOG_FILE = os.path.join(LOG_DIR, "app.log")
LOG_LEVEL = logging.INFO

API_KEY = "f7c9a34a9334a866f09255980d8e0ef0"  # ⚠️ ЗАМЕНИТЕ НА СВОЙ РЕАЛЬНЫЙ КЛЮЧ!
OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"

# Создание директории логов
if not os.path.exists(LOG_DIR):
    try:
        os.makedirs(LOG_DIR)
    except OSError as e:
        print(f"Ошибка создания директории логов: {e}")
        exit(1)

# Настройка логирования
logging.basicConfig(
    filename=LOG_FILE,
    level=LOG_LEVEL,
    format='%(asctime)s | %(levelname)-8s | %(client_ip)-15s | %(request_target)-12s | %(requested_city)-15s | %(response_status)-3s | %(response_data)s | %(api_response)s | %(duration_ms)dms',
    datefmt='%Y-%m-%d %H:%M:%S',
)

logger = logging.getLogger(__name__)

class ContextFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, 'client_ip'):
            record.client_ip = 'unknown'
        if not hasattr(record, 'request_target'):
            record.request_target = 'unknown'
        if not hasattr(record, 'requested_city'):
            record.requested_city = 'unknown'
        if not hasattr(record, 'response_status'):
            record.response_status = '000'
        if not hasattr(record, 'response_data'):
            record.response_data = '{}'
        if not hasattr(record, 'api_response'):
            record.api_response = 'none'
        if not hasattr(record, 'duration_ms'):
            record.duration_ms = 0
        return True

logger.addFilter(ContextFilter())

def is_valid_city_name(city: str) -> bool:
    if not city or len(city) > 100:
        return False
    return bool(re.match(r'^[a-zA-Zа-яА-ЯёЁ\s\-\'\.]+$', city))  # Исправлено: убрана лишняя запятая

def fetch_weather(city_name: str) -> dict:
    if not is_valid_city_name(city_name):
        logger.warning("Недопустимое название города", extra={
            'requested_city': city_name,
            'response_status': 400,
            'response_data': '{"error": "Недопустимое название города"}',
            'api_response': 'invalid_input'
        })
        return None

    params = {
        "q": city_name,
        "appid": API_KEY,
        "units": "metric",
        "lang": "ru"
    }

    start_time = time.time()

    try:
        url = OPENWEATHER_URL + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            duration_ms = int((time.time() - start_time) * 1000)

            weather_desc = data["weather"][0]["description"]
            temp = data["main"]["temp"]

            result = {
                "description": weather_desc,
                "temp": round(temp, 1)
            }

            logger.info(f"Успешно получена погода: {weather_desc}, {temp}°C", extra={
                'requested_city': city_name,
                'response_status': 200,
                'response_data': json.dumps({"weather": result}, ensure_ascii=False),
                'api_response': 'success',
                'duration_ms': duration_ms
            })
            return result

    except urllib.error.HTTPError as e:
        duration_ms = int((time.time() - start_time) * 1000)
        error_msg = f"HTTP {e.code}: {e.reason}"
        logger.error(f"OpenWeatherMap вернул {e.code}", extra={
            'requested_city': city_name,
            'response_status': e.code,
            'response_data': '{"error": "Не удалось получить погоду"}',
            'api_response': error_msg,
            'duration_ms': duration_ms
        })
    except urllib.error.URLError as e:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error(f"Ошибка сети: {e.reason}", extra={
            'requested_city': city_name,
            'response_status': 502,
            'response_data': '{"error": "Ошибка сети"}',
            'api_response': f'network_error: {str(e.reason)}',
            'duration_ms': duration_ms
        })
    except json.JSONDecodeError:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error("Некорректный ответ от OpenWeatherMap", extra={
            'requested_city': city_name,
            'response_status': 500,
            'response_data': '{"error": "Некорректный ответ от погодного сервиса"}',
            'api_response': 'invalid_json',
            'duration_ms': duration_ms
        })
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.exception(f"Неожиданная ошибка при обработке погоды", extra={
            'requested_city': city_name,
            'response_status': 500,
            'response_data': '{"error": "Внутренняя ошибка сервера"}',
            'api_response': f'unknown_error: {str(e)}',
            'duration_ms': duration_ms
        })

    return None

class WeatherHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        client_ip = self.client_address[0]
        request_target = self.path
        start_time = time.time()

        logger.info("Получен POST-запрос", extra={
            'client_ip': client_ip,
            'request_target': request_target,
            'requested_city': 'pending',
            'response_status': 0,
            'response_data': 'processing',
            'api_response': 'pending',
            'duration_ms': 0
        })

        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length).decode('utf-8')

        try:
            data = json.loads(post_data)
            city = data.get("city", "").strip()
            if not city:
                raise ValueError("Поле 'city' пустое")
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Неверный JSON или отсутствует city: {e}", extra={
                'client_ip': client_ip,
                'request_target': request_target,
                'requested_city': 'invalid_json',
                'response_status': 400,
                'response_data': '{"error": "Неверный JSON или отсутствует поле city"}',
                'api_response': 'invalid_json',
                'duration_ms': int((time.time() - start_time) * 1000)
            })
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Неверный JSON или отсутствует поле city"}, ensure_ascii=False).encode('utf-8'))
            return

        logger.info(f"Получен город от geo-service: {city}", extra={
            'client_ip': client_ip,
            'request_target': request_target,
            'requested_city': city,
            'response_status': 200,
            'response_data': 'ok',
            'api_response': 'city_received',
            'duration_ms': int((time.time() - start_time) * 1000)
        })

        weather_data = fetch_weather(city)

        if weather_data:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response_body = json.dumps({"weather": weather_data}, ensure_ascii=False)
            self.wfile.write(response_body.encode('utf-8'))
            logger.info("Отправлен ответ с погодой", extra={
                'client_ip': client_ip,
                'request_target': request_target,
                'requested_city': city,
                'response_status': 200,
                'response_data': response_body,
                'api_response': 'success',
                'duration_ms': int((time.time() - start_time) * 1000)
            })
        else:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response_body = json.dumps({"error": "Не удалось получить погоду"}, ensure_ascii=False)
            self.wfile.write(response_body.encode('utf-8'))
            logger.error("Не удалось получить погоду", extra={
                'client_ip': client_ip,
                'request_target': request_target,
                'requested_city': city,
                'response_status': 500,
                'response_data': response_body,
                'api_response': 'failed',
                'duration_ms': int((time.time() - start_time) * 1000)
            })

    def do_GET(self):
        client_ip = self.client_address[0]
        logger.warning("Попытка GET-запроса к weather-service (запрещено)", extra={
            'client_ip': client_ip,
            'request_target': self.path,
            'requested_city': 'blocked_get',
            'response_status': 405,
            'response_data': '{"error": "Метод не поддерживается"}',
            'api_response': 'method_not_allowed',
            'duration_ms': 0
        })
        self.send_response(405)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": "Метод не поддерживается"}, ensure_ascii=False).encode('utf-8'))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "http://localhost:7999")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        logger.info("CORS preflight ответ", extra={
            'client_ip': self.client_address[0],
            'request_target': self.path,
            'requested_city': 'cors',
            'response_status': 200,
            'response_data': '{}',
            'api_response': 'cors_allowed'
        })

# Запуск сервера
if __name__ == "__main__":
    if API_KEY == "f7c9a34a9334a866f09255980d8e0ef0":
        print("⚠️  ВНИМАНИЕ: Используется тестовый API-ключ OpenWeatherMap. Замените его на реальный!")
        logger.warning("Используется тестовый API-ключ OpenWeatherMap")

    server_address = ('', 8002)
    httpd = socketserver.TCPServer(server_address, WeatherHandler)
    print("🌐 weather-service запущен на порту 8002")
    logger.info("Сервер weather-service запущен на порту 8002")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Сервер weather-service остановлен")
        httpd.server_close()
