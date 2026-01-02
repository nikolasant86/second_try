from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from cerberus import Validator

PORT = 5000

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

            quantity = float(data['quantity'])
            cost_per_unit = float(data['costPerUnit'])
            total_cost = quantity * cost_per_unit

            self._set_headers()
            self.wfile.write(json.dumps({'totalCost': total_cost}).encode('utf-8'))
        except json.JSONDecodeError:
            self._set_headers(400)
            self.wfile.write(json.dumps({'error': 'Некорректный JSON'}).encode('utf-8'))
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({'error': 'Внутренняя ошибка', 'details': str(e)}).encode('utf-8'))

if __name__ == '__main__':
    print(f"Запуск сервера на порту {PORT}")
    server = HTTPServer(('', PORT), RequestHandler)
    server.serve_forever()