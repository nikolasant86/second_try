import http.server
import socketserver
import json
import os
from urllib.parse import urlparse

IMAGES_DIR = 'images'
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
        parsed_path = urlparse(self.path)
        # Устанавливаем ответ и заголовки
        if parsed_path.path == '/api/images':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self._set_cors_headers()
            self.end_headers()
            data = {'images': IMAGE_FILES}
            self.wfile.write(json.dumps(data).encode('utf-8'))

        elif parsed_path.path.startswith('/api/image/'):
            self.send_response(200)
            self._set_cors_headers()

            # Извлекаем индекс
            try:
                index_str = parsed_path.path.split('/')[-1]
                index = int(index_str)
            except (IndexError, ValueError):
                self.send_error(400, 'Invalid index')
                return

            # Валидируем индекс
            if not v.validate({'index': index}):
                self.send_error(400, 'Index out of bounds')
                return

            file_name = IMAGE_FILES[index]
            file_path = os.path.join(IMAGES_DIR, file_name)

            if os.path.exists(file_path):
                ext = os.path.splitext(file_path)[1].lower()
                if ext in ['.jpg', '.jpeg']:
                    content_type = 'image/jpeg'
                elif ext == '.png':
                    content_type = 'image/png'
                elif ext == '.gif':
                    content_type = 'image/gif'
                else:
                    content_type = 'application/octet-stream'
                self.send_header('Content-type', content_type)
                self.end_headers()
                with open(file_path, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, 'Image not found')
        else:
            # Обслуживание статических файлов (например, HTML, CSS)
            super().do_GET()

def run(server_class=http.server.HTTPServer, handler_class=MyHandler, port=8000):
    os.chdir('.')  # текущая папка
    with socketserver.TCPServer(("", port), handler_class) as httpd:
        print(f"Serving HTTP on port {port}")
        httpd.serve_forever()

if __name__ == '__main__':
    run()
