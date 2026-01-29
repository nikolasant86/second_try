import os
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from urllib.request import urlopen, Request
from urllib.error import URLError
import socket
from urllib.parse import urlparse
from cerberus import Validator
from datetime import datetime
import uuid

# --- Загрузка переменных окружения ---
PORT = int(os.getenv("CALCULATOR_PORT", 5000))
LOG_DIR = os.getenv("CALCULATOR_LOG_DIR", "/var/log/calculator")

# Создание директории логов
os.makedirs(LOG_DIR, exist_ok=True)

# --- Логирование: JSON-формат, файл, без дублей ---
logger = logging.getLogger("calculator")
logger.setLevel(logging.INFO)
logger.handlers.clear()  # Убираем возможные дубликаты

log_file = os.path.join(LOG_DIR, "calculator.log")
file_handler = logging.FileHandler(log_file, encoding="utf-8")
file_handler.setLevel(logging.INFO)


# --- JSON-форматтер ---
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcfromtimestamp(record.created).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),  # ← Это поле формируется из msg, а не из extra
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
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
            ):
                if key not in log_entry:
                    log_entry[key] = value

        return json.dumps(log_entry, ensure_ascii=False)


file_handler.setFormatter(JSONFormatter())
logger.addHandler(file_handler)
logger.propagate = False


# --- Фильтр для автодобавления полей ---
class CalculatorContextFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "request_id"):
            record.request_id = str(uuid.uuid4())
        if not hasattr(record, "client_ip"):
            record.client_ip = "UNKNOWN"
        if not hasattr(record, "public_ip"):
            record.public_ip = "UNKNOWN"
        if not hasattr(record, "response_status"):
            record.response_status = "000"
        return True


logger.addFilter(CalculatorContextFilter())

# --- Валидация данных ---
schema = {
    "quantity": {"type": "float", "required": True, "min": 0},
    "costPerUnit": {"type": "float", "required": True, "min": 0},
}
validator = Validator(schema)


class IPResolver:
    def __init__(self):
        self.ip_services = [
            "https://api.ipify.org",
            "https://ident.me",
            "https://checkip.amazonaws.com",
            "http://ipinfo.io/ip",
            "http://ifconfig.me/ip",
        ]

    def get_public_ip(self, client_ip):
        if self._is_public_ip(client_ip):
            logger.info(
                "Клиент — публичный IP (прямой)",
                extra={
                    "request": "IP_RESOLVE",
                    "client_ip": client_ip,
                    "public_ip": client_ip,
                    "response_status": "200",
                },
            )
            return client_ip
        else:
            public_ip = self._resolve_public_ip(client_ip)
            logger.info(
                "Клиент — определён через внешний сервис",
                extra={
                    "request": "IP_RESOLVE",
                    "client_ip": client_ip,
                    "public_ip": public_ip,
                    "response_status": "200",
                },
            )
            return public_ip

    def _is_public_ip(self, ip):
        if ip in ["127.0.0.1", "localhost"]:
            logger.info(
                "IP — локальный",
                extra={
                    "request": "IP_CHECK",
                    "client_ip": ip,
                    "public_ip": "N/A",
                    "response_status": "200",
                },
            )
            return False

        private_prefixes = [
            "10.",
            "192.168.",
            "169.254.",
        ]
        if ip.startswith("172."):
            try:
                parts = ip.split(".")
                second = int(parts[1])
                if 16 <= second <= 31:
                    logger.info(
                        "IP — частный (172.16–31.x)",
                        extra={
                            "request": "IP_CHECK",
                            "client_ip": ip,
                            "public_ip": "N/A",
                            "response_status": "200",
                            "range": "172.16-31",
                        },
                    )
                    return False
            except Exception:
                logger.warning(
                    "Ошибка при парсинге IP",
                    extra={
                        "request": "IP_CHECK",
                        "client_ip": ip,
                        "public_ip": "INVALID",
                        "response_status": "400",
                    },
                )
                return False

        for prefix in private_prefixes:
            if ip.startswith(prefix):
                logger.info(
                    "IP — частный",
                    extra={
                        "request": "IP_CHECK",
                        "client_ip": ip,
                        "public_ip": "N/A",
                        "response_status": "200",
                        "prefix": prefix,
                    },
                )
                return False

        logger.info(
            "IP — публичный",
            extra={
                "request": "IP_CHECK",
                "client_ip": ip,
                "public_ip": ip,
                "response_status": "200",
            },
        )
        return True

    def _resolve_public_ip(self, client_ip):
        for service_url in self.ip_services:
            try:
                req = Request(
                    service_url, headers={"User-Agent": "Calculator-Server/1.0"}
                )
                with urlopen(req, timeout=3) as response:
                    if response.status == 200:
                        ip = response.read().decode("utf-8").strip()
                        if self._is_valid_ip(ip):
                            logger.info(
                                "Успешно получено публичный IP",
                                extra={
                                    "request": "IP_RESOLVE",
                                    "client_ip": client_ip,
                                    "public_ip": ip,
                                    "response_status": "200",
                                    "service": urlparse(service_url).netloc,
                                },
                            )
                            return ip
                        else:
                            logger.warning(
                                "Невалидный IP от сервиса",
                                extra={
                                    "request": "IP_RESOLVE",
                                    "client_ip": client_ip,
                                    "public_ip": ip,
                                    "response_status": "500",
                                    "service": urlparse(service_url).netloc,
                                },
                            )
            except (URLError, socket.timeout, Exception) as e:
                logger.warning(
                    "Ошибка при определении IP через сервис",
                    extra={
                        "request": "IP_RESOLVE",
                        "client_ip": client_ip,
                        "public_ip": "ERROR",
                        "response_status": "500",
                        "service": urlparse(service_url).netloc,
                        "error": str(e),
                    },
                )
                continue

        logger.warning(
            "Не удалось определить публичный IP — возвращаем клиентский IP",
            extra={
                "request": "IP_RESOLVE",
                "client_ip": client_ip,
                "public_ip": client_ip,
                "response_status": "500",
            },
        )
        return client_ip

    def _is_valid_ip(self, ip):
        parts = ip.split(".")
        if len(parts) != 4:
            logger.warning(
                "Невалидный формат IP",
                extra={
                    "request": "IP_VALIDATE",
                    "client_ip": "N/A",
                    "public_ip": ip,
                    "response_status": "400",
                },
            )
            return False
        for part in parts:
            try:
                num = int(part)
                if num < 0 or num > 255:
                    logger.warning(
                        "Невалидный октет IP",
                        extra={
                            "request": "IP_VALIDATE",
                            "client_ip": "N/A",
                            "public_ip": ip,
                            "response_status": "400",
                            "octet": part,
                        },
                    )
                    return False
            except ValueError:
                logger.warning(
                    "Октет не число",
                    extra={
                        "request": "IP_VALIDATE",
                        "client_ip": "N/A",
                        "public_ip": ip,
                        "response_status": "400",
                        "octet": part,
                    },
                )
                return False
        logger.info(
            "IP — валиден",
            extra={
                "request": "IP_VALIDATE",
                "client_ip": "N/A",
                "public_ip": ip,
                "response_status": "200",
            },
        )
        return True


ip_resolver = IPResolver()


class RequestHandler(BaseHTTPRequestHandler):
    def _get_client_ip(self):
        ip_headers = [
            "X-Real-IP",
            "X-Forwarded-For",
            "CF-Connecting-IP",
            "True-Client-IP",
            "X-Cluster-Client-IP",
        ]

        for header in ip_headers:
            ip = self.headers.get(header)
            if ip:
                if header == "X-Forwarded-For":
                    ip = ip.split(",")[0].strip()
                if ip:
                    logger.info(
                        "Получен клиентский IP из заголовка",
                        extra={
                            "request": "HEADER_IP",
                            "client_ip": ip,
                            "public_ip": "UNKNOWN",
                            "response_status": "200",
                            "header": header,
                        },
                    )
                    return ip

        client_ip = self.client_address[0]
        logger.info(
            "Клиентский IP из socket",
            extra={
                "request": "SOCKET_IP",
                "client_ip": client_ip,
                "public_ip": "UNKNOWN",
                "response_status": "200",
            },
        )
        return client_ip

    def _get_public_ip(self):
        cached = getattr(self, "_resolved_public_ip", None)
        if cached:
            return cached
        client_ip = self._get_client_ip()
        public_ip = ip_resolver.get_public_ip(client_ip)
        self._resolved_public_ip = public_ip
        logger.info(
            "Клиент → публичный IP",
            extra={
                "request": "PUBLIC_IP_RESOLVED",
                "client_ip": client_ip,
                "public_ip": public_ip,
                "response_status": "200",
            },
        )
        return public_ip

    def _set_headers(self, status=200):
        self.send_response(status)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        logger.info(
            "CORS и Content-Type заголовки установлены",
            extra={
                "request": "HEADERS_SET",
                "client_ip": self.client_address[0],
                "public_ip": self._get_public_ip(),
                "response_status": status,
            },
        )

    def do_OPTIONS(self):
        logger.info(
            "OPTIONS запрос",
            extra={
                "request": "OPTIONS",
                "client_ip": self.client_address[0],
                "public_ip": self._get_public_ip(),
                "response_status": "204",
            },
        )
        self._set_headers(204)

    def do_POST(self):
        client_ip = self._get_client_ip()
        public_ip = self._get_public_ip()

        logger.info(
            "Начало обработки POST-запроса",
            extra={
                "request": "POST_START",
                "client_ip": client_ip,
                "public_ip": public_ip,
                "response_status": "000",
            },
        )

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)

            if not validator.validate(data):
                self._set_headers(400)
                response_data = {
                    "error": "Некорректные данные",
                    "details": validator.errors,
                }
                self.wfile.write(json.dumps(response_data).encode("utf-8"))
                logger.warning(
                    "Некорректные данные",
                    extra={
                        "request": "VALIDATION_ERROR",
                        "client_ip": client_ip,
                        "public_ip": public_ip,
                        "response_status": "400",
                        "input_data": data,
                        "errors": validator.errors,
                    },
                )
                return

            quantity = float(data["quantity"])
            cost_per_unit = float(data["costPerUnit"])
            total_cost = quantity * cost_per_unit

            self._set_headers()
            response_data = {"totalCost": total_cost}
            self.wfile.write(json.dumps(response_data).encode("utf-8"))

            logger.info(
                "Успешный расчет стоимости",
                extra={
                    "request": "CALCULATION_SUCCESS",
                    "client_ip": client_ip,
                    "public_ip": public_ip,
                    "response_status": "200",
                    "quantity": quantity,
                    "costPerUnit": cost_per_unit,
                    "totalCost": total_cost,
                },
            )

        except json.JSONDecodeError:
            self._set_headers(400)
            response_data = {"error": "Некорректный JSON"}
            self.wfile.write(json.dumps(response_data).encode("utf-8"))
            logger.error(
                "Ошибка декодирования JSON",
                extra={
                    "request": "JSON_DECODE_ERROR",
                    "client_ip": client_ip,
                    "public_ip": public_ip,
                    "response_status": "400",
                },
            )
        except Exception as e:
            self._set_headers(500)
            response_data = {"error": "Внутренняя ошибка", "details": str(e)}
            self.wfile.write(json.dumps(response_data).encode("utf-8"))
            logger.error(
                "Внутренняя ошибка",
                extra={
                    "request": "INTERNAL_ERROR",
                    "client_ip": client_ip,
                    "public_ip": public_ip,
                    "response_status": "500",
                    "error": str(e),
                },
            )

    def log_message(self, format, *args):
        """Переопределяем стандартное логирование — теперь всё через logger"""
        if getattr(self, "command", "") == "OPTIONS":
            return

        message = format % args
        public_ip = getattr(self, "_resolved_public_ip", None)
        if not public_ip:
            client_ip = self._get_client_ip()
            public_ip = ip_resolver.get_public_ip(client_ip)
            self._resolved_public_ip = public_ip

        # ✅ ИСПРАВЛЕНО: УБРАЛИ 'message' из extra — оно уже есть в msg!
        logger.info(
            f"HTTP {message}",
            extra={
                "request": "ACCESS_LOG",
                "client_ip": self.client_address[0],
                "public_ip": public_ip,
                "response_status": "200",
                "http_request": message,  # ← ВСЁ ВЕРНО: ИСПОЛЬЗУЕМ 'http_request', а не 'message'
            },
        )


def run(port: int = PORT):
    server_address = ("", port)
    httpd = HTTPServer(server_address, RequestHandler)
    logger.info(
        "Calculator server is listening on port",
        extra={
            "request": "SERVER_START",
            "client_ip": "SYSTEM",
            "public_ip": "SYSTEM",
            "response_status": "200",
            "port": port,
        },
    )
    print(f"Calculator server is listening on port {port}")
    print(f"Log directory: {LOG_DIR}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info(
            "Calculator server stopped",
            extra={
                "request": "SERVER_STOP",
                "client_ip": "SYSTEM",
                "public_ip": "SYSTEM",
                "response_status": "200",
            },
        )
        print("\nShutting down server...")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run()
