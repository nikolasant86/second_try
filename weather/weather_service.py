import requests
import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import aiohttp
import asyncio

# Логирование
logging.basicConfig(filename='/var/log/weather_service/app.log', level=logging.INFO)

API_KEY = "f7c9a34a9334a866f09255980d8e0ef0"

async def fetch_weather(city_name):
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "q": city_name,
        "appid": API_KEY,
        "units": "metric",
        "lang": "ru"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logging.error(f"Ошибка API погоды: {await response.text()}")
    except Exception as e:
        logging.error(f"Ошибка при запросе погоды: {e}")
    return None

class WeatherHandler(BaseHTTPRequestHandler):
    async def do_GET(self):
        parsed_path = urlparse(self.path)
        query = parse_qs(parsed_path.query)
        city = query.get("city")
        if city:
            city_name = city[0]
            weather_data = await fetch_weather(city_name)
            if weather_data:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "description": weather_data["weather"][0]["description"],
                    "temp": weather_data["main"]["temp"]
                }).encode('utf-8'))
            else:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Ошибка API погоды"}).encode('utf-8'))
        else:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Не указан город"}).encode('utf-8'))

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8002), WeatherHandler)
    logging.info("Weather server started at port 8002")
    server.serve_forever()
