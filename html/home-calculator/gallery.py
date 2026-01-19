import http.server
import socketserver
import json
import os
import logging
import requests
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
import threading

# Загрузка переменных окружения с правильным преобразованием типов
PORT = int(os.environ.get('GALLERY_PORT', '8000'))
LOG_DIR = os.environ.get('GALLERY_LOG_DIR', '/var/log/gallery')
IMAGES_DIR = os.environ.get('GALLERY_IMAGES_DIR', 'images')

os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger('gallery')
logger.setLevel(logging.DEBUG)

# Единый обработчик логирования для всех сообщений
log_file = os.path.join(LOG_DIR, 'gallery.log')
file_handler = logging.FileHandler(log_file)
file_handler.setLevel(logging.DEBUG)

# Форматтер для всех типов сообщений
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.propagate = False

# Проверяем существование директории с изображениями
if not os.path.exists(IMAGES_DIR):
    os.makedirs(IMAGES_DIR, exist_ok=True)
    logger.warning(f"Директория {IMAGES_DIR} создана, так как не существовала")

IMAGE_FILES = sorted([
    f for f in os.listdir(IMAGES_DIR) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif'))
])

from cerberus import Validator
schema = {
    'index': {'type': 'integer', 'min': 0, 'max': len(IMAGE_FILES) - 1}
}
v = Validator(schema)

class IPResolver:
    """Класс для определения публичного IP-адреса"""
    
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=3)
        self.ip_cache = {}  # Кэш для хранения определенных IP
        self.cache_lock = threading.Lock()
        
        # Список сервисов для определения публичного IP
        self.ip_services = [
            'https://api.ipify.org',
            'https://ident.me',
            'https://checkip.amazonaws.com',
            'https://ipinfo.io/ip',
            'https://ifconfig.me/ip'
        ]
    
    def get_public_ip(self, client_ip):
        """
        Определяет публичный IP-адрес для клиента
        Возвращает кортеж (public_ip, source)
        """
        # Если это уже публичный IP, возвращаем его
        if self._is_public_ip(client_ip):
            return client_ip, "direct"
        
        # Проверяем кэш
        with self.cache_lock:
            if client_ip in self.ip_cache:
                return self.ip_cache[client_ip]
        
        # Пытаемся определить публичный IP через внешние сервисы
        public_ip = self._resolve_public_ip(client_ip)
        
        # Сохраняем в кэш
        with self.cache_lock:
            self.ip_cache[client_ip] = (public_ip, "external_service")
        
        return public_ip, "external_service"
    
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
        def try_service(service_url):
            try:
                response = requests.get(service_url, timeout=3)
                if response.status_code == 200:
                    ip = response.text.strip()
                    if self._is_valid_ip(ip):
                        return ip
            except:
                pass
            return None
        
        # Пробуем сервисы параллельно
        futures = [self.executor.submit(try_service, service) for service in self.ip_services]
        
        for future in futures:
            result = future.result(timeout=5)
            if result:
                return result
        
        # Если не удалось определить, возвращаем исходный IP
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
    
    def cleanup(self):
        """Очищает ресурсы"""
        self.executor.shutdown()

# Глобальный экземпляр резолвера
ip_resolver = IPResolver()

class MyHandler(http.server.SimpleHTTPRequestHandler):
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
        public_ip, source = ip_resolver.get_public_ip(client_ip)
        
        # Логируем процесс определения IP
        if public_ip != client_ip:
            logger.debug(f"Определен публичный IP: {client_ip} -> {public_ip} (источник: {source})")
        
        return public_ip
    
    def _set_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_cors_headers()
        self.end_headers()

    def do_GET(self):
        public_ip = self._get_public_ip()  # Используем метод для получения публичного IP
        parsed_path = urlparse(self.path)
        
        # Логируем все запросы с публичным IP
        logger.info(f"Запрос от {public_ip}: {self.command} {self.path}")
        
        if parsed_path.path == '/api/images':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self._set_cors_headers()
            self.end_headers()
            data = {'images': IMAGE_FILES}
            self.wfile.write(json.dumps(data).encode('utf-8'))
            logger.info(f"{public_ip} - запрос к /api/images успешно")
            
        elif parsed_path.path.startswith('/api/image/'):
            self.send_response(200)
            self._set_cors_headers()

            try:
                index_str = parsed_path.path.split('/')[-1]
                index = int(index_str)
            except (IndexError, ValueError):
                self.send_error(400, 'Invalid index')
                logger.error(f"{public_ip} - неправильный индекс: {parsed_path.path}")
                return

            if not v.validate({'index': index}):
                self.send_error(400, 'Index out of bounds')
                logger.error(f"{public_ip} - индекс вне диапазона: {index}")
                return

            file_name = IMAGE_FILES[index]
            file_path = os.path.join(IMAGES_DIR, file_name)

            if os.path.exists(file_path):
                ext = os.path.splitext(file_path)[1].lower()
                content_type = 'application/octet-stream'
                if ext in ['.jpg', '.jpeg']:
                    content_type = 'image/jpeg'
                elif ext == '.png':
                    content_type = 'image/png'
                elif ext == '.gif':
                    content_type = 'image/gif'
                    
                self.send_header('Content-type', content_type)
                self.end_headers()
                with open(file_path, 'rb') as f:
                    self.wfile.write(f.read())
                logger.info(f"{public_ip} - открыто изображение: {file_name}")
            else:
                self.send_error(404, 'Image not found')
                logger.error(f"{public_ip} - изображение не найдено: {file_name}")
        else:
            # Логирование для прямого доступа к изображениям
            file_path = self.translate_path(self.path)
            if os.path.isfile(file_path) and any(file_path.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif']):
                file_name = os.path.basename(file_path)
                logger.info(f"{public_ip} - прямое открытие изображения: {file_name}")
            
            super().do_GET()
    
    def log_message(self, format, *args):
        """Переопределяем стандартное логирование сервера"""
        public_ip = self._get_public_ip()
        logger.info(f"{public_ip} - {format % args}")

def run(server_class=http.server.HTTPServer, handler_class=MyHandler, port=PORT):
    os.chdir('.')
    print(f"Запуск сервера на порту {port}")
    print(f"Директория логов: {LOG_DIR}")
    print(f"Директория изображений: {IMAGES_DIR}")
    print(f"Найдено изображений: {len(IMAGE_FILES)}")
    
    try:
        with socketserver.TCPServer(("", port), handler_class) as httpd:
            print(f"HTTP сервер запущен на порту {port}")
            logger.info(f"Сервер запущен на порту {port}")
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка сервера...")
        ip_resolver.cleanup()
        logger.info("Сервер остановлен")

if __name__ == '__main__':
    run()
