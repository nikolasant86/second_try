import http.server
import socketserver
import json
import os
import logging
from urllib.parse import urlparse

# Загрузка переменных окружения с правильным преобразованием типов
PORT = int(os.environ.get('GALLERY_PORT', '8000'))  # Преобразуем строку в int
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

# Добавляем только один обработчик
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

class MyHandler(http.server.SimpleHTTPRequestHandler):
    def _set_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_cors_headers()
        self.end_headers()

    def do_GET(self):
        client_ip = self.client_address[0]
        parsed_path = urlparse(self.path)
        
        if parsed_path.path == '/api/images':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self._set_cors_headers()
            self.end_headers()
            data = {'images': IMAGE_FILES}
            self.wfile.write(json.dumps(data).encode('utf-8'))
            logger.info(f"{client_ip} запрос к /api/images успешно")
            
        elif parsed_path.path.startswith('/api/image/'):
            self.send_response(200)
            self._set_cors_headers()

            try:
                index_str = parsed_path.path.split('/')[-1]
                index = int(index_str)
            except (IndexError, ValueError):
                self.send_error(400, 'Invalid index')
                logger.error(f"{client_ip} неправильный индекс: {parsed_path.path}")
                return

            if not v.validate({'index': index}):
                self.send_error(400, 'Index out of bounds')
                logger.error(f"{client_ip} индекс вне диапазона: {index}")
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
                logger.info(f"{client_ip} открыто изображение: {file_name}")
            else:
                self.send_error(404, 'Image not found')
                logger.error(f"{client_ip} изображение не найдено: {file_name}")
        else:
            # Логирование для прямого доступа к изображениям
            file_path = self.translate_path(self.path)
            if os.path.isfile(file_path) and any(file_path.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif']):
                file_name = os.path.basename(file_path)
                logger.info(f"{client_ip} открыто изображение: {file_name}")
            
            super().do_GET()

def run(server_class=http.server.HTTPServer, handler_class=MyHandler, port=PORT):
    os.chdir('.')
    print(f"Запуск сервера на порту {port}")
    print(f"Директория логов: {LOG_DIR}")
    print(f"Директория изображений: {IMAGES_DIR}")
    print(f"Найдено изображений: {len(IMAGE_FILES)}")
    
    with socketserver.TCPServer(("", port), handler_class) as httpd:
        print(f"HTTP сервер запущен на порту {port}")
        logger.info(f"Сервер запущен на порту {port}")
        httpd.serve_forever()

if __name__ == '__main__':
    run()
