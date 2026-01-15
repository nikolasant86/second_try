import os
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from cerberus import Validator

PORT = 5000

# Путь к логам
LOG_DIR = "/var/log/calculator"
os.makedirs(LOG_DIR, exist_ok=True)

# Настройка логирования
logger = logging.getLogger('calculator')
logger.setLevel(logging.INFO)  # Уровень логирования

# Обработчик для access.log
access_handler = logging.FileHandler(os.path.join(LOG_DIR, 'access.log'))
access_handler.setLevel(logging.INFO)
access_formatter = logging.Formatter('%(asctime)s - %(message)s')
access_handler.setFormatter(access_formatter)

# Обработчик для error.log
error_handler = logging.FileHandler(os.path.join(LOG_DIR, 'error.log'))
error_handler.setLevel(logging.ERROR)
error_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
error_handler.setFormatter(error_formatter)

# Добавление обработчиков
logger.addHandler(access_handler)
logger.addHandler(error_handler)

# Валидация данных
schema = {
    'quantity': {'type': 'float', 'required': True, 'min': 0},
    'costPerUnit': {'type': 'float', 'required': True, 'min': 0}
}
validator = Validator(schema)

class RequestHandler(BaseHTTPRequestHandler):
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
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)

            # Валидация
            if not validator.validate(data):
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Некорректные данные', 'details': validator.errors}).encode('utf-8'))
                return
                 # Логирование ошибки в access.log
                logger.info(f"Invalid data from {self.client_address}: {data} Errors: {validator.errors}")
                return

            quantity = float(data['quantity'])
            cost_per_unit = float(data['costPerUnit'])
            total_cost = quantity * cost_per_unit

            self._set_headers()
            self.wfile.write(json.dumps({'totalCost': total_cost}).encode('utf-8'))

            # Логирование успешного запроса
            logger.info(f"Processed request from {self.client_address}: {data} Total cost: {total_cost}")

        except json.JSONDecodeError:
            self._set_headers(400)
            self.wfile.write(json.dumps({'error': 'Некорректный JSON'}).encode('utf-8'))
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({'error': 'Внутренняя ошибка', 'details': str(e)}).encode('utf-8'))
            # Логирование исключения в error.log
            logger.error(f"Внутренняя ошибка при обработке запроса от {self.client_address}: {e}")


if __name__ == '__main__':
    print(f"Запуск сервера на порту {PORT}")
    server = HTTPServer(('', PORT), RequestHandler)
    server.serve_forever()