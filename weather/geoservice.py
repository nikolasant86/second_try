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

# --- Конфигурация ---
LOG_DIR = "/var/log/geoservice"
LOG_FILE = os.path.join(LOG_DIR, "geo_service.log")
LOG_LEVEL = logging.INFO

# DaData
DADATA_API_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/iplocate/address"
DADATA_TOKEN = "8cad94649ead406020a534c2030d0e5248202934"

# Weather Service (внутренний)
WEATHER_SERVICE_HOST = "weather_service"
WEATHER_SERVICE_PORT = 80
WEATHER_SERVICE_PATH = "/api/weather"

# Локальные IP-шаблоны
LOCAL_IP_PATTERNS = [
    r'^127\.',
    r'^10\.',
    r'^172\.(1[6-9]|2[0-9]|3[0-1])\.',
    r'^192\.168\.',
    r'^169\.254\.',
    r'^::1'  # Исправлено: убран пробел после ::1
]

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
    format='%(asctime)s | %(levelname)-8s | %(client_ip)-15s | %(action)-20s | %(city)-15s | %(status)-3s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

logger = logging.getLogger(__name__)

class ContextFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, 'client_ip'):
            record.client_ip = 'unknown'
        if not hasattr(record, 'action'):
            record.action = 'unknown'
        if not hasattr(record, 'city'):
            record.city = 'unknown'
        if not hasattr(record, 'status'):
            record.status = '000'
        return True

logger.addFilter(ContextFilter())

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
            return response.read().decode('utf-8').strip()
    except Exception as e:
        logger.warning(f"Не удалось получить публичный IP: {e}", extra={'action': 'get_public_ip'})
        return "8.8.8.8"

def get_city_by_ip(ip: str) -> str:
    try:
        data = json.dumps({"ip": ip}).encode('utf-8')
        req = urllib.request.Request(
            url=DADATA_API_URL,
            data=data,
            headers={
                "Authorization": f"Token {DADATA_TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            result = json.loads(response.read().decode('utf-8'))
            location = result.get("location", {})
            data = location.get("data", {})
            city = data.get("city") or data.get("region") or "Неизвестно"
            logger.info(f"DaData вернул город: {city}", extra={'action': 'dadata_success', 'city': city})
            return city
    except Exception as e:
        logger.error(f"Ошибка DaData: {e}", extra={'action': 'dadata_error'})
        return "Сервис временно недоступен"

def send_city_to_weather_service(city: str) -> dict:
    """
    Отправляет город в POST-запросе на weather_service (без requests)
    Возвращает ответ в виде dict
    """
    try:
        payload = json.dumps({"city": city}).encode('utf-8')
        conn = HTTPConnection(WEATHER_SERVICE_HOST, WEATHER_SERVICE_PORT, timeout=5)
        conn.request("POST", WEATHER_SERVICE_PATH, body=payload, headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
            "User-Agent": "geoservice"
        })
        response = conn.getresponse()
        response_data = response.read().decode('utf-8')
        conn.close()

        if response.status != 200:
            logger.error(f"weather-service вернул {response.status}: {response_data}",
                         extra={'action': 'weather_service_error', 'status': response.status})
            return {"error": f"weather-service: HTTP {response.status}"}

        weather_result = json.loads(response_data)
        logger.info(f"Погода получена от weather-service: {weather_result}",
                    extra={'action': 'weather_service_success', 'city': city})
        return weather_result

    except Exception as e:
        logger.exception(f"Ошибка при отправке на weather-service: {e}",
                         extra={'action': 'weather_service_exception'})
        return {"error": "Не удалось связаться с погодным сервисом"}

class CityHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            client_ip = self.client_address[0]
            logger.info("Получен GET-запрос", extra={'client_ip': client_ip, 'action': 'get_request'})

            # ✅ Сначала проверяем путь — только если это /api/get_city, продолжаем
            if self.path != "/api/get_city":
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Not Found"}')
                logger.warning(f"Неверный путь: {self.path}", extra={'client_ip': client_ip, 'action': 'path_not_found', 'status': 404})
                return  # ← ВЫХОД, если не тот путь!

            # ✅ Теперь только для /api/get_city — продолжаем

            # ✅ CORS-заголовки — ПОСЛЕ send_response, НЕ ДО!
            # Но: send_header() должен быть ПОСЛЕ send_response() — это критично!
            # Поэтому: сначала send_response, потом send_header

            # Определяем IP
            x_real_ip = self.headers.get("X-Real-IP")
            x_forwarded_for = self.headers.get("X-Forwarded-For")

            if x_real_ip:
                client_ip = x_real_ip.strip()
            elif x_forwarded_for:
                client_ip = x_forwarded_for.split(",")[0].strip()

            logger.info(f"Исходный IP: {client_ip}", extra={'client_ip': client_ip, 'action': 'ip_initial'})

            if is_local_ip(client_ip):
                logger.info(f"Локальный IP: {client_ip}. Запрашиваем публичный...", extra={'action': 'ip_local'})
                client_ip = get_public_ip()
                logger.info(f"Заменён на публичный IP: {client_ip}", extra={'client_ip': client_ip, 'action': 'ip_replaced'})

            city = get_city_by_ip(client_ip)
            logger.info(f"Определён город: {city}", extra={'client_ip': client_ip, 'action': 'city_determined', 'city': city})

            weather_response = send_city_to_weather_service(city)

            # ✅ ПРАВИЛЬНЫЙ ПОРЯДОК: send_response → send_header → end_headers
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()  # ← КРИТИЧЕСКИ ВАЖНО!

            if "error" in weather_response:
                response_body = f"Ошибка: {weather_response['error']}"
            else:
                weather_data = weather_response.get("weather", {})
                description = weather_data.get("description", "неизвестно")
                temp = weather_data.get("temp", "неизвестно")
                if isinstance(temp, (int, float)):
                    temp = round(temp, 1)
                response_body = f"{city} {description} {temp}"

            self.wfile.write(response_body.encode('utf-8'))
            logger.info(f"Отправлен ответ браузеру: {response_body}",
                        extra={'client_ip': client_ip, 'action': 'response_sent', 'status': 200})


        def do_OPTIONS(self):
            # ✅ CORS: Разрешаем всем источникам для preflight
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "86400")  # Кэшировать на 24 часа
            self.end_headers()

            self.send_response(200)
            logger.info("CORS preflight ответ", extra={'action': 'cors_preflight'})

        def do_POST(self):
            self.send_response(405)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Method Not Allowed"}')
            logger.warning("Попытка POST на geo-service", extra={'action': 'post_blocked'})

# Запуск сервера
if __name__ == "__main__":
    if DADATA_TOKEN == "8cad94649ead406020a534c2030d0e5248202934":
        print("⚠️  ВНИМАНИЕ: Используется тестовый токен DaData. Замените его на реальный!")
        logger.warning("Используется тестовый токен DaData")

    server_address = ('', 7999)
    httpd = socketserver.TCPServer(server_address, CityHandler)
    print("🌐 geoservice запущен на порту 7999")
    logger.info("Сервер geoservice запущен на порту 7999")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Сервер geoservice остановлен")
        httpd.server_close()
