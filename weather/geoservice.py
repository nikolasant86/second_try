from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import requests
import logging
import os
import re
import socket

# Настройки логирования
LOG_DIR = "/var/log/geoservice"
LOG_FILE = os.path.join(LOG_DIR, "geo_service.log")
LOG_LEVEL = logging.INFO

# Настройки DaData
DADATA_API_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/iplocate/address"
DADATA_TOKEN = "8cad94649ead406020a534c2030d0e5248202934"  # ← Замените на реальный API-ключ DaData

# Создаем директорию для логов, если ее нет
if not os.path.exists(LOG_DIR):
    try:
        os.makedirs(LOG_DIR)
    except OSError as e:
        print(f"Ошибка создания директории логов: {e}")
        print("Пожалуйста, убедитесь, что у вас есть права на создание директории /var/log/geoservice")
        exit(1)

# Настраиваем логирование
logging.basicConfig(
    filename=LOG_FILE,
    level=LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Список локальных/внутренних IP, которые нужно заменить
LOCAL_IP_PATTERNS = [
    r'^127\.',           # localhost
    r'^10\.',             # 10.0.0.0/8
    r'^172\.(1[6-9]|2[0-9]|3[0-1])\.',  # 172.16.0.0/12
    r'^192\.168\.',       # 192.168.0.0/16
    r'^169\.254\.',       # link-local
    r'^::1$',             # IPv6 localhost
]

def is_local_ip(ip: str) -> bool:
    """Проверяет, является ли IP локальным или внутренним"""
    if not ip:
        return True
    for pattern in LOCAL_IP_PATTERNS:
        if re.match(pattern, ip):
            return True
    return False

def get_public_ip() -> str:
    """Получает публичный IP через api.ipify.org"""
    try:
        response = requests.get("https://api.ipify.org", timeout=3)
        response.raise_for_status()
        return response.text.strip()
    except Exception as e:
        logger.warning(f"Не удалось получить публичный IP: {e}")
        return "8.8.8.8"  # fallback — Google DNS

def get_city_by_ip(ip: str) -> str:
    """Запрашивает город через DaData"""
    try:
        response = requests.get(
            DADATA_API_URL,
            headers={
                "Authorization": f"Token {DADATA_TOKEN}",
                "Content-Type": "application/json"
            },
            params={"ip": ip},
            timeout=5
        )
        response.raise_for_status()
        data = response.json()

        if not data or not isinstance(data, dict):
            logger.warning("DaData вернула пустой или некорректный ответ")
            return "Неизвестно (ответ пуст)"

        location = data.get("location")
        if not location:
            logger.warning("DaData вернула None для ключа 'location'")
            return "Неизвестно (location is None)"

        location_data = location.get("data", {})
        city = location_data.get("city") or location_data.get("region") or "Неизвестно"
        return city

    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка при запросе к DaData: {e}")
        return "Сервис временно недоступен"
    except Exception as e:
        logger.exception("Неожиданная ошибка при обработке DaData")
        return "Неизвестно (ошибка обработки)"

class CityHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/get_city":
            # Получаем IP из заголовков (если есть)
            client_ip = None

            # Пытаемся получить IP из X-Real-IP (обычно от Nginx)
            x_real_ip = self.headers.get("X-Real-IP")
            if x_real_ip:
                client_ip = x_real_ip.strip()

            # Если X-Real-IP нет — пробуем X-Forwarded-For (может быть список)
            if not client_ip:
                x_forwarded_for = self.headers.get("X-Forwarded-For")
                if x_forwarded_for:
                    # Берём первый IP из списка (клиентский)
                    client_ip = x_forwarded_for.split(",")[0].strip()

            # Если ни один заголовок не дал IP — используем client_address
            if not client_ip:
                client_ip = self.client_address[0]

            logger.info(f"Исходный IP из запроса: {client_ip}")

            # Если IP локальный — заменяем на реальный публичный
            if is_local_ip(client_ip):
                logger.info(f"Обнаружен локальный IP: {client_ip}. Запрашиваем публичный IP...")
                client_ip = get_public_ip()
                logger.info(f"Заменён на публичный IP: {client_ip}")

            logger.info(f"Окончательный IP для DaData: {client_ip}")

            try:
                city = get_city_by_ip(client_ip)
                logger.info(f"Определённый город: {city}")

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                response_body = json.dumps({"data": city}, ensure_ascii=False, indent=2)
                self.wfile.write(response_body.encode('utf-8'))
                logger.debug(f"Отправлен ответ: {response_body}")

            except Exception as e:
                logger.exception("Ошибка при обработке запроса")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                response_body = json.dumps({"error": "Внутренняя ошибка сервера"}, ensure_ascii=False)
                self.wfile.write(response_body.encode('utf-8'))

        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error": "Not Found"}')
            logger.warning(f"Запрошен несуществующий путь: {self.path}")

    def do_POST(self):
        self.send_response(405)
        self.end_headers()
        self.wfile.write(b'{"error": "Method Not Allowed"}')
        logger.warning(f"Попытка POST-запроса на запрещенный эндпоинт: {self.path}")


# Запуск сервера
if __name__ == "__main__":
    # Проверка токена
    if DADATA_TOKEN == "8cad94649ead406020a534c2030d0e5248202934":
        print("⚠️  ВНИМАНИЕ: Вы используете тестовый токен DaData. Замените его на реальный!")
        print("Получите токен: https://dadata.ru/profile/#/api")
        logger.warning("Используется тестовый токен DaData — замените на реальный!")

    server_address = ('', 7999)
    httpd = HTTPServer(server_address, CityHandler)
    print("Сервер запущен на порту 7999...")
    print("Эндпоинт: GET /api/get_city")
    logger.info("Сервер запущен на порту 7999...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Сервер остановлен пользователем")
        httpd.server_close()
