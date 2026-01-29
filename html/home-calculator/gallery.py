import http.server
import socketserver
import json
import os
import logging
import requests
from urllib.parse import urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor
import threading
from PIL import Image
import io
from datetime import datetime
import uuid
from cerberus import Validator

# --- Загрузка переменных окружения ---
PORT = int(os.environ.get("GALLERY_PORT", "8000"))
LOG_DIR = os.environ.get("GALLERY_LOG_DIR", "/var/log/gallery")
IMAGES_DIR = os.environ.get("GALLERY_IMAGES_DIR", "images")

# Создаём директории
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(IMAGES_DIR, exist_ok=True)

# --- Логирование: JSON-формат, файл, без дублей ---
logger = logging.getLogger("gallery")
logger.setLevel(logging.INFO)
logger.handlers.clear()  # Убираем возможные дубликаты от других модулей

log_file = os.path.join(LOG_DIR, "gallery.log")
file_handler = logging.FileHandler(log_file, encoding="utf-8")
file_handler.setLevel(logging.INFO)


# --- JSON-форматтер (ключевой компонент) ---
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcfromtimestamp(record.created).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
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


# Применяем форматтер
file_handler.setFormatter(JSONFormatter())
logger.addHandler(file_handler)
logger.propagate = False  # Не наследуем от root


# --- Фильтр для автодобавления полей (если не заданы) ---
class GalleryContextFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "request"):
            record.request = "UNKNOWN"
        if not hasattr(record, "client_ip"):
            record.client_ip = "UNKNOWN"
        if not hasattr(record, "public_ip"):
            record.public_ip = "UNKNOWN"
        if not hasattr(record, "response_status"):
            record.response_status = "000"
        if not hasattr(record, "request_id"):
            record.request_id = str(uuid.uuid4())
        return True


logger.addFilter(GalleryContextFilter())

# --- Проверка изображений ---
IMAGE_FILES = sorted(
    [
        f
        for f in os.listdir(IMAGES_DIR)
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif"))
    ]
)

# --- Валидатор для индекса ---
schema = {"index": {"type": "integer", "min": 0, "max": len(IMAGE_FILES) - 1}}
v = Validator(schema)


# --- IP Resolver ---
class IPResolver:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=3)
        self.ip_cache = {}
        self.cache_lock = threading.Lock()
        self.ip_services = [
            "https://api.ipify.org",
            "https://ident.me",
            "https://checkip.amazonaws.com",
            "https://ipinfo.io/ip",
            "https://ifconfig.me/ip",
        ]

    def get_public_ip(self, client_ip):
        logger.info(
            "Получен клиентский IP",
            extra={
                "request": "IP_RESOLVE",
                "client_ip": client_ip,
                "public_ip": "UNKNOWN",
                "response_status": "200",
            },
        )

        if self._is_public_ip(client_ip):
            public_ip = client_ip
            source = "direct"
            logger.info(
                "Клиент — публичный IP (прямой)",
                extra={
                    "request": "IP_RESOLVE",
                    "client_ip": client_ip,
                    "public_ip": public_ip,
                    "response_status": "200",
                    "source": source,
                },
            )
        else:
            with self.cache_lock:
                if client_ip in self.ip_cache:
                    public_ip, source = self.ip_cache[client_ip]
                    logger.info(
                        "Клиент — найден в кэше",
                        extra={
                            "request": "IP_RESOLVE",
                            "client_ip": client_ip,
                            "public_ip": public_ip,
                            "response_status": "200",
                            "source": source,
                        },
                    )
                    return public_ip, source

            public_ip = self._resolve_public_ip(client_ip)
            source = "external_service"
            logger.info(
                "Клиент — определён через внешний сервис",
                extra={
                    "request": "IP_RESOLVE",
                    "client_ip": client_ip,
                    "public_ip": public_ip,
                    "response_status": "200",
                    "source": source,
                },
            )

            with self.cache_lock:
                self.ip_cache[client_ip] = (public_ip, source)

        logger.info(
            "Итог: клиент → публичный IP",
            extra={
                "request": "IP_RESOLVE",
                "client_ip": client_ip,
                "public_ip": public_ip,
                "response_status": "200",
                "source": source,
            },
        )
        return public_ip, source

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

        private_ranges = [
            ("10.", True),
            ("172.16.", True),
            ("172.17.", True),
            ("172.18.", True),
            ("172.19.", True),
            ("172.20.", True),
            ("172.21.", True),
            ("172.22.", True),
            ("172.23.", True),
            ("172.24.", True),
            ("172.25.", True),
            ("172.26.", True),
            ("172.27.", True),
            ("172.28.", True),
            ("172.29.", True),
            ("172.30.", True),
            ("172.31.", True),
            ("192.168.", True),
            ("169.254.", True),
        ]

        for prefix, is_private in private_ranges:
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
        def try_service(service_url):
            try:
                logger.info(
                    "Попытка определить публичный IP через сервис",
                    extra={
                        "request": "IP_RESOLVE",
                        "client_ip": client_ip,
                        "public_ip": "UNKNOWN",
                        "response_status": "200",
                        "service": service_url,
                    },
                )
                response = requests.get(service_url, timeout=3)
                if response.status_code == 200:
                    ip = response.text.strip()
                    if self._is_valid_ip(ip):
                        logger.info(
                            "Успешно получено публичный IP",
                            extra={
                                "request": "IP_RESOLVE",
                                "client_ip": client_ip,
                                "public_ip": ip,
                                "response_status": "200",
                                "service": service_url,
                            },
                        )
                        return ip
                    else:
                        logger.warning(
                            "Невалидный IP от сервиса",
                            extra={
                                "request": "IP_RESOLVE",
                                "client_ip": client_ip,
                                "public_ip": "INVALID",
                                "response_status": "500",
                                "service": service_url,
                            },
                        )
            except Exception as e:
                logger.warning(
                    "Ошибка при запросе сервиса",
                    extra={
                        "request": "IP_RESOLVE",
                        "client_ip": client_ip,
                        "public_ip": "ERROR",
                        "response_status": "500",
                        "service": service_url,
                        "error": str(e),
                    },
                )
            return None

        futures = [
            self.executor.submit(try_service, service) for service in self.ip_services
        ]

        for future in futures:
            result = future.result(timeout=5)
            if result:
                return result

        logger.warning(
            "Все сервисы не ответили — возвращаем клиентский IP",
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

    def cleanup(self):
        self.executor.shutdown()


ip_resolver = IPResolver()

# --- КЭШ МАСШТАБИРОВАННЫХ ИЗОБРАЖЕНИЙ ---
scale_cache = {}
cache_lock = threading.Lock()


class MyHandler(http.server.SimpleHTTPRequestHandler):
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
        client_ip = self._get_client_ip()
        public_ip, source = ip_resolver.get_public_ip(client_ip)
        logger.info(
            "Клиент → публичный IP",
            extra={
                "request": "PUBLIC_IP_RESOLVED",
                "client_ip": client_ip,
                "public_ip": public_ip,
                "response_status": "200",
                "source": source,
            },
        )
        return public_ip

    def _set_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        logger.info(
            "CORS заголовки установлены",
            extra={
                "request": "CORS_SET",
                "client_ip": self.client_address[0],
                "public_ip": self._get_public_ip(),
                "response_status": "200",
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
        self.send_response(204)
        self._set_cors_headers()
        self.end_headers()

    def do_GET(self):
        logger.info(
            "Начало обработки GET-запроса",
            extra={
                "request": f"GET {self.path}",
                "client_ip": self.client_address[0],
                "public_ip": "UNKNOWN",
                "response_status": "000",
            },
        )

        public_ip = self._get_public_ip()
        parsed_path = urlparse(self.path)
        query_params = parse_qs(parsed_path.query)
        scale_str = query_params.get("scale", [None])[0]
        scale = None

        if scale_str is not None:
            try:
                scale = float(scale_str)
                if scale < 0.1 or scale > 3.0:
                    raise ValueError("Scale must be between 0.1 and 3.0")
                logger.info(
                    "Запрошено масштабирование",
                    extra={
                        "request": f"GET {self.path} scale={scale}",
                        "client_ip": self.client_address[0],
                        "public_ip": public_ip,
                        "response_status": "200",
                        "scale": scale,
                    },
                )
            except ValueError:
                logger.warning(
                    "Невалидный параметр scale",
                    extra={
                        "request": f"GET {self.path} scale={scale_str}",
                        "client_ip": self.client_address[0],
                        "public_ip": public_ip,
                        "response_status": "400",
                        "scale": scale_str,
                    },
                )
                self.send_error(
                    400, "Invalid scale parameter. Must be float between 0.1 and 3.0"
                )
                return

        self.status_code = 200

        if parsed_path.path == "/api/images":
            logger.info(
                "Запрос к /api/images",
                extra={
                    "request": "GET /api/images",
                    "client_ip": self.client_address[0],
                    "public_ip": public_ip,
                    "response_status": "200",
                },
            )
            self.send_response(200)
            self._set_cors_headers()
            self.send_header("Content-type", "application/json")
            self.end_headers()
            data = {"images": IMAGE_FILES}
            self.wfile.write(json.dumps(data).encode("utf-8"))
            logger.info(
                "Отправлен ответ: изображения",
                extra={
                    "request": "GET /api/images",
                    "client_ip": self.client_address[0],
                    "public_ip": public_ip,
                    "response_status": "200",
                    "count": len(IMAGE_FILES),
                },
            )
            return

        elif parsed_path.path.startswith("/api/image/"):
            try:
                index_str = parsed_path.path.split("/")[-1]
                index = int(index_str)
                logger.info(
                    "Извлечён индекс",
                    extra={
                        "request": f"GET /api/image/{index}",
                        "client_ip": self.client_address[0],
                        "public_ip": public_ip,
                        "response_status": "200",
                        "index": index,
                    },
                )
            except (IndexError, ValueError):
                logger.error(
                    "Невалидный индекс в пути",
                    extra={
                        "request": f"GET {parsed_path.path}",
                        "client_ip": self.client_address[0],
                        "public_ip": public_ip,
                        "response_status": "400",
                    },
                )
                self.send_error(400, "Invalid index")
                return

            if not v.validate({"index": index}):
                logger.error(
                    "Индекс выходит за границы",
                    extra={
                        "request": f"GET /api/image/{index}",
                        "client_ip": self.client_address[0],
                        "public_ip": public_ip,
                        "response_status": "400",
                        "index": index,
                        "max_index": len(IMAGE_FILES) - 1,
                    },
                )
                self.send_error(400, "Index out of bounds")
                return

            file_name = IMAGE_FILES[index]
            file_path = os.path.join(IMAGES_DIR, file_name)
            logger.info(
                "Запрошен файл",
                extra={
                    "request": f"GET /api/image/{index}",
                    "client_ip": self.client_address[0],
                    "public_ip": public_ip,
                    "response_status": "200",
                    "file": file_name,
                    "path": file_path,
                },
            )

            if os.path.exists(file_path):
                ext = os.path.splitext(file_path)[1].lower()
                content_type = "application/octet-stream"
                if ext in [".jpg", ".jpeg"]:
                    content_type = "image/jpeg"
                elif ext == ".png":
                    content_type = "image/png"
                elif ext == ".gif":
                    content_type = "image/gif"

                logger.info(
                    "Файл найден. Content-Type",
                    extra={
                        "request": f"GET /api/image/{index}",
                        "client_ip": self.client_address[0],
                        "public_ip": public_ip,
                        "response_status": "200",
                        "file": file_name,
                        "content_type": content_type,
                    },
                )

                if scale is not None:
                    cache_key = (file_path, scale)
                    with cache_lock:
                        if cache_key in scale_cache:
                            image_data, ct = scale_cache[cache_key]
                            logger.info(
                                "Кэшированное изображение — отдаём из кэша",
                                extra={
                                    "request": f"GET /api/image/{index} scale={scale}",
                                    "client_ip": self.client_address[0],
                                    "public_ip": public_ip,
                                    "response_status": "200",
                                    "file": file_name,
                                    "scale": scale,
                                },
                            )
                        else:
                            try:
                                with Image.open(file_path) as img:
                                    original_size = img.size
                                    new_size = (
                                        int(original_size[0] * scale),
                                        int(original_size[1] * scale),
                                    )
                                    logger.info(
                                        "Масштабирование изображения",
                                        extra={
                                            "request": f"GET /api/image/{index} scale={scale}",
                                            "client_ip": self.client_address[0],
                                            "public_ip": public_ip,
                                            "response_status": "200",
                                            "file": file_name,
                                            "scale": scale,
                                            "original_size": original_size,
                                            "new_size": new_size,
                                        },
                                    )
                                    img_resized = img.resize(
                                        new_size, Image.Resampling.LANCZOS
                                    )
                                    buffer = io.BytesIO()
                                    if ext in [".jpg", ".jpeg"]:
                                        img_resized.save(
                                            buffer, format="JPEG", quality=85
                                        )
                                    elif ext == ".png":
                                        img_resized.save(
                                            buffer, format="PNG", optimize=True
                                        )
                                    elif ext == ".gif":
                                        img_resized.save(
                                            buffer, format="GIF", optimize=True
                                        )
                                    image_data = buffer.getvalue()
                                    scale_cache[cache_key] = (image_data, content_type)
                                    logger.info(
                                        "Изображение масштабировано и закэшировано",
                                        extra={
                                            "request": f"GET /api/image/{index} scale={scale}",
                                            "client_ip": self.client_address[0],
                                            "public_ip": public_ip,
                                            "response_status": "200",
                                            "file": file_name,
                                            "scale": scale,
                                        },
                                    )
                            except Exception as e:
                                logger.error(
                                    "Ошибка при масштабировании",
                                    extra={
                                        "request": f"GET /api/image/{index} scale={scale}",
                                        "client_ip": self.client_address[0],
                                        "public_ip": public_ip,
                                        "response_status": "500",
                                        "file": file_name,
                                        "scale": scale,
                                        "error": str(e),
                                    },
                                )
                                self.send_error(500, "Failed to scale image")
                                return

                    self.send_response(200)
                    self._set_cors_headers()
                    self.send_header("Content-type", content_type)
                    self.end_headers()
                    self.wfile.write(image_data)
                    logger.info(
                        "Масштабированное изображение успешно отправлено",
                        extra={
                            "request": f"GET /api/image/{index} scale={scale}",
                            "client_ip": self.client_address[0],
                            "public_ip": public_ip,
                            "response_status": "200",
                            "file": file_name,
                            "scale": scale,
                        },
                    )
                    return
                else:
                    self.send_response(200)
                    self._set_cors_headers()
                    self.send_header("Content-type", content_type)
                    self.end_headers()
                    with open(file_path, "rb") as f:
                        self.wfile.write(f.read())
                    logger.info(
                        "Оригинальное изображение успешно отправлено",
                        extra={
                            "request": f"GET /api/image/{index}",
                            "client_ip": self.client_address[0],
                            "public_ip": public_ip,
                            "response_status": "200",
                            "file": file_name,
                        },
                    )
                    return
            else:
                logger.error(
                    "Файл не найден",
                    extra={
                        "request": f"GET /api/image/{index}",
                        "client_ip": self.client_address[0],
                        "public_ip": public_ip,
                        "response_status": "404",
                        "file": file_path,
                    },
                )
                self.send_error(404, "Image not found")
                return

        else:
            file_path = self.translate_path(self.path)
            logger.info(
                "Прямой доступ к файлу",
                extra={
                    "request": f"GET {self.path}",
                    "client_ip": self.client_address[0],
                    "public_ip": public_ip,
                    "response_status": "000",
                    "path": file_path,
                },
            )

            if os.path.isfile(file_path) and any(
                file_path.lower().endswith(ext)
                for ext in [".png", ".jpg", ".jpeg", ".gif"]
            ):
                file_name = os.path.basename(file_path)
                logger.info(
                    "Отправка изображения",
                    extra={
                        "request": f"GET {self.path}",
                        "client_ip": self.client_address[0],
                        "public_ip": public_ip,
                        "response_status": "000",
                        "file": file_name,
                    },
                )

                if scale is not None:
                    cache_key = (file_path, scale)
                    with cache_lock:
                        if cache_key in scale_cache:
                            image_data, ct = scale_cache[cache_key]
                            logger.info(
                                "Кэшированное изображение — отдаём из кэша",
                                extra={
                                    "request": f"GET {self.path} scale={scale}",
                                    "client_ip": self.client_address[0],
                                    "public_ip": public_ip,
                                    "response_status": "200",
                                    "file": file_name,
                                    "scale": scale,
                                },
                            )
                        else:
                            try:
                                with Image.open(file_path) as img:
                                    original_size = img.size
                                    new_size = (
                                        int(original_size[0] * scale),
                                        int(original_size[1] * scale),
                                    )
                                    logger.info(
                                        "Масштабирование изображения",
                                        extra={
                                            "request": f"GET {self.path} scale={scale}",
                                            "client_ip": self.client_address[0],
                                            "public_ip": public_ip,
                                            "response_status": "200",
                                            "file": file_name,
                                            "scale": scale,
                                            "original_size": original_size,
                                            "new_size": new_size,
                                        },
                                    )
                                    img_resized = img.resize(
                                        new_size, Image.Resampling.LANCZOS
                                    )
                                    buffer = io.BytesIO()
                                    ext = os.path.splitext(file_path)[1].lower()
                                    if ext in [".jpg", ".jpeg"]:
                                        img_resized.save(
                                            buffer, format="JPEG", quality=85
                                        )
                                    elif ext == ".png":
                                        img_resized.save(
                                            buffer, format="PNG", optimize=True
                                        )
                                    elif ext == ".gif":
                                        img_resized.save(
                                            buffer, format="GIF", optimize=True
                                        )
                                    image_data = buffer.getvalue()
                                    scale_cache[cache_key] = (
                                        image_data,
                                        (
                                            "image/jpeg"
                                            if ext in [".jpg", ".jpeg"]
                                            else (
                                                "image/png"
                                                if ext == ".png"
                                                else "image/gif"
                                            )
                                        ),
                                    )
                                    logger.info(
                                        "Изображение масштабировано и закэшировано",
                                        extra={
                                            "request": f"GET {self.path} scale={scale}",
                                            "client_ip": self.client_address[0],
                                            "public_ip": public_ip,
                                            "response_status": "200",
                                            "file": file_name,
                                            "scale": scale,
                                        },
                                    )
                            except Exception as e:
                                logger.error(
                                    "Ошибка при масштабировании",
                                    extra={
                                        "request": f"GET {self.path} scale={scale}",
                                        "client_ip": self.client_address[0],
                                        "public_ip": public_ip,
                                        "response_status": "500",
                                        "file": file_name,
                                        "scale": scale,
                                        "error": str(e),
                                    },
                                )
                                self.send_error(500, "Failed to scale image")
                                return

                    self.send_response(200)
                    self._set_cors_headers()
                    self.send_header("Content-type", scale_cache[cache_key][1])
                    self.end_headers()
                    self.wfile.write(image_data)
                    logger.info(
                        "Масштабированное изображение успешно отправлено",
                        extra={
                            "request": f"GET {self.path} scale={scale}",
                            "client_ip": self.client_address[0],
                            "public_ip": public_ip,
                            "response_status": "200",
                            "file": file_name,
                            "scale": scale,
                        },
                    )
                    return
                else:
                    logger.info(
                        "Вызов родительского send_head() — с CORS",
                        extra={
                            "request": f"GET {self.path}",
                            "client_ip": self.client_address[0],
                            "public_ip": public_ip,
                            "response_status": "000",
                        },
                    )
                    super().send_head()
                    self._set_cors_headers()
                    self.end_headers()
                    with open(file_path, "rb") as f:
                        self.wfile.write(f.read())
                    logger.info(
                        "Оригинальное изображение успешно отправлено",
                        extra={
                            "request": f"GET {self.path}",
                            "client_ip": self.client_address[0],
                            "public_ip": public_ip,
                            "response_status": "200",
                            "file": file_name,
                        },
                    )
                    return

            else:
                logger.info(
                    "Запрос не к изображению — передаём родителю",
                    extra={
                        "request": f"GET {self.path}",
                        "client_ip": self.client_address[0],
                        "public_ip": public_ip,
                        "response_status": "000",
                    },
                )
                super().do_GET()
                return

    def send_error(self, code, message=None):
        logger.error(
            f"HTTP {code}: {message or 'Unknown error'}",
            extra={
                "request": f"{self.command} {self.path}",
                "client_ip": self.client_address[0],
                "public_ip": self._get_public_ip(),
                "response_status": code,
            },
        )
        self.status_code = code
        super().send_error(code, message)
        self._set_cors_headers()
        self.end_headers()
        logger.info(
            f"Ответ с ошибкой {code} отправлен с CORS",
            extra={
                "request": f"{self.command} {self.path}",
                "client_ip": self.client_address[0],
                "public_ip": self._get_public_ip(),
                "response_status": code,
            },
        )

    def send_head(self):
        logger.info(
            "Вызов send_head() для прямого доступа к файлу",
            extra={
                "request": f"GET {self.path}",
                "client_ip": self.client_address[0],
                "public_ip": self._get_public_ip(),
                "response_status": "000",
            },
        )
        response = super().send_head()
        self._set_cors_headers()
        return response

    def log_message(self, format, *args):
        """Отключаем стандартное логирование — всё через logger"""
        pass


def run(server_class=http.server.HTTPServer, handler_class=MyHandler, port=PORT):
    os.chdir(".")
    print(f"Запуск сервера на порту {port}")
    print(f"Директория логов: {LOG_DIR}")
    print(f"Директория изображений: {IMAGES_DIR}")
    print(f"Найдено изображений: {len(IMAGE_FILES)}")

    try:
        with socketserver.TCPServer(("", port), handler_class) as httpd:
            print(f"HTTP сервер запущен на порту {port}")
            logger.info(
                "Сервер запущен",
                extra={
                    "request": "SERVER_START",
                    "client_ip": "SYSTEM",
                    "public_ip": "SYSTEM",
                    "response_status": "200",
                    "port": port,
                },
            )
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка сервера...")
        ip_resolver.cleanup()
        logger.info(
            "Сервер остановлен",
            extra={
                "request": "SERVER_STOP",
                "client_ip": "SYSTEM",
                "public_ip": "SYSTEM",
                "response_status": "200",
            },
        )


if __name__ == "__main__":
    run()
