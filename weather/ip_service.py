import http.server
import socketserver
import json
import requests
import logging
from urllib.parse import urlparse, parse_qs
import asyncio
import aiohttp

# Логирование
logging.basicConfig(filename='/var/log/ip_service/app.log', level=logging.INFO)

TOKEN_DADATA = "8cad94649ead406020a534c2030d0e5248202934"
IP = "46.226.227.20"  # Или определите динамически

async def get_location():
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Token {TOKEN_DADATA}"
    }
    data = {"ip": IP}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://suggestions.dadata.ru/suggestions/api/4_1/rs/iplocate", headers=headers, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get("location"):
                        return result["location"]["value"]
                else:
                    logging.error("Ошибка получения локации: " + await response.text())
    except Exception as e:
        logging.error(f"Ошибка при запросе к Dadata: {e}")
    return None

class MyHandler(http.server.SimpleHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    async def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == "/api/ip":
            city = await get_location()
            if city:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"city": city}).encode('utf-8'))
            else:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Не удалось определить местоположение"}).encode('utf-8'))
        else:
            super().do_GET()

if __name__ == "__main__":
    with socketserver.TCPServer(("0.0.0.0", 8001), MyHandler) as httpd:
        logging.info("Server started at port 8001")
        httpd.serve_forever()
