import os
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from urllib.request import urlopen, Request
from urllib.error import URLError
import socket
from urllib.parse import urlparse
from cerberus import Validator

# Загрузка переменных окружения
PORT = int(os.getenv('CALCULATOR_PORT', 5000))
LOG_DIR = os.getenv('CALCULATOR_LOG_DIR', '/var/log/calculator')

# Создание директории логов
os.makedirs(LOG_DIR, exist_ok=True)

# Настройка логирования - один файл для всех логов
logger = logging.getLogger('calculator')
logger.setLevel(logging.INFO)

# Единый обработчик для всех сообщений
log_handler = logging.FileHandler(os.path.join(LOG_DIR, 'calculator.log'))
log_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_handler.setFormatter(formatter)

logger.addHandler(log_handler)
logger.propagate = False

# Валидация данных
schema = {
    'quantity': {'type': 'float', 'required': True, 'min': 0},
    'costPerUnit': {'type': 'float', 'required': True, 'min': 0}
}
validator = Validator(schema)

class IPResolver:
    """Класс для определения публичного IP-адреса"""
    
    def __init__(self):
        # Список сервисов для определения публичного IP
        self.ip_services = [
            'https://api.ipify.org',
            'https://ident.me',
            'https://checkip.amazonaws.com',
            'http://ipinfo.io/ip',
            'http://ifconfig.me/ip'
        ]
    
    def get_public_ip(self, client_ip):
        """
        Определяет публичный IP-адрес для клиента
        Возвращает публичный IP или исходный, если не удалось определить
        """
        # Если это уже публичный IP, возвращаем его
        if self._is_public_ip(client_ip):
            return client_ip
        
        # Пытаемся определить публичный IP через внешние сервисы
        public_ip = self._resolve_public_ip(client_ip)
        
        return public_ip
    
    def _is_public_ip(self, ip):
        """Проверяет, является ли IP публичным"""
        if ip in ['127.0.0.1', 'localhost']:
            return False
        
        # Проверяем частные диапазоны IP
        private_ranges = [
            ('10.', True),
            ('172.16.', True), ('172.17.', True), ('172.18.', True), ('172.19.', True),
            ('172.20.', True), ('172.21.', True), ('172.22.', True), ('172.23.', True),
            ('172.24.', True), ('172.25.', True), ('172.26.', True), ('172.27.', True),
            ('172.28.', True), ('172.29.', True), ('172.30.', True), ('172.31.', True),
            ('192.168.', True),
            ('169.254.', True)  # Link-local
        ]
        
        for prefix, is_private in private_ranges:
            if ip.startswith(prefix):
                return not is_private
        
        return True
    
    def _resolve_public_ip(self, client_ip):
        """Определяет публичный IP через внешние сервисы"""
        for service_url in self.ip_services:
            try:
                # Устанавливаем таймаут и User-Agent
                req = Request(service_url, headers={'User-Agent': 'Calculator-Server/1.0'})
                with urlopen(req, timeout=3) as response:
                    if response.status == 200:
                        ip = response.read().decode('utf-8').strip()
                        if self._is_valid_ip(ip):
                            logger.info(f"Определен публичный IP: {client_ip} -> {ip} (сервис: {urlparse(service_url).netloc})")
                            return ip
            except (URLError, socket.timeout, Exception) as e:
                logger.debug(f"Ошибка при определении IP через {service_url}: {e}")
                continue
        
        # Если не удалось определить, возвращаем исходный IP
        logger.warning(f"Не удалось определить публичный IP для {client_ip}, используется исходный IP")
        return client_ip
    
    def _is_valid_ip(self, ip):
        """Проверяет валидность IP-адреса"""
        parts = ip.split('.')
        if len(parts) != 4:
            return False
        for part in parts:
            try:
                num = int(part)
                if num < 0 or num > 255:
                    return False
            except ValueError:
                return False
        return True

# Глобальный экземпляр резолвера
ip_resolver = IPResolver()

class RequestHandler(BaseHTTPRequestHandler):
    def _get_client_ip(self):
        """Получает реальный IP-адрес клиента с учетом прокси-заголовков"""
        ip_headers = [
            'X-Real-IP',
            'X-Forwarded-For',
            'CF-Connecting-IP',
            'True-Client-IP',
            'X-Cluster-Client-IP'
        ]
        
        for header in ip_headers:
            ip = self.headers.get(header)
            if ip:
                if header == 'X-Forwarded-For':
                    ip = ip.split(',')[0].strip()
                if ip:
                    return ip
        
        return self.client_address[0]
    
    def _get_public_ip(self):
        """Определяет публичный IP-адрес клиента"""
        client_ip = self._get_client_ip()
        public_ip = ip_resolver.get_public_ip(client_ip)
        
        # Логируем процесс определения IP (только если IP изменился)
        if public_ip != client_ip:
            logger.info(f"Клиент {client_ip} имеет публичный IP: {public_ip}")
        
        return public_ip
    
    def _set_headers(self, status=200):
        self.send_response(status)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers()

    def do_POST(self):
        public_ip = self._get_public_ip()
        
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)

            if not validator.validate(data):
                self._set_headers(400)
                response_data = {'error': 'Некорректные данные', 'details': validator.errors}
                self.wfile.write(json.dumps(response_data).encode('utf-8'))
                logger.warning(f"{public_ip} - некорректные данные: {data} Ошибки: {validator.errors}")
                return

            quantity = float(data['quantity'])
            cost_per_unit = float(data['costPerUnit'])
            total_cost = quantity * cost_per_unit

            self._set_headers()
            response_data = {'totalCost': total_cost}
            self.wfile.write(json.dumps(response_data).encode('utf-8'))
            
            # Логируем успешный запрос
            logger.info(f"{public_ip} - расчет стоимости: quantity={quantity}, costPerUnit={cost_per_unit}, totalCost={total_cost}")

        except json.JSONDecodeError:
            self._set_headers(400)
            response_data = {'error': 'Некорректный JSON'}
            self.wfile.write(json.dumps(response_data).encode('utf-8'))
            logger.error(f"{public_ip} - ошибка декодирования JSON")
            
        except Exception as e:
            self._set_headers(500)
            response_data = {'error': 'Внутренняя ошибка', 'details': str(e)}
            self.wfile.write(json.dumps(response_data).encode('utf-8'))
            logger.error(f"{public_ip} - внутренняя ошибка: {e}")

    def log_message(self, format, *args):
        """Переопределяем стандартное логирование сервера"""
        public_ip = self._get_public_ip()
        logger.info(f"{public_ip} - {format % args}")

if __name__ == '__main__':
    print(f"Запуск сервера калькулятора на порту {PORT}")
    print(f"Логи сохраняются в: {os.path.join(LOG_DIR, 'calculator.log')}")
    
    try:
        server = HTTPServer(('', PORT), RequestHandler)
        logger.info(f"Сервер калькулятора запущен на порту {PORT}")
        print(f"Сервер успешно запущен. Нажмите Ctrl+C для остановки.")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка сервера...")
        logger.info("Сервер калькулятора остановлен")
    except Exception as e:
        logger.error(f"Ошибка при запуске сервера: {e}")
        print(f"Ошибка при запуске сервера: {e}")
