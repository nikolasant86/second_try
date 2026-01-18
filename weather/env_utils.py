import os
import logging

class EnvironmentConfig:
    """Утилита для работы с переменными окружения"""
    
    @staticmethod
    def get(key, default=None, required=False):
        """Получить переменную окружения"""
        value = os.environ.get(key, default)
        
        if required and value is None:
            raise ValueError(f"Обязательная переменная окружения {key} не установлена")
            
        return value
    
    @staticmethod
    def get_int(key, default=0, required=False):
        """Получить целочисленную переменную"""
        value = EnvironmentConfig.get(key, default, required)
        try:
            return int(value)
        except (TypeError, ValueError):
            if required:
                raise ValueError(f"Переменная {key} должна быть целым числом")
            return default
    
    @staticmethod
    def get_bool(key, default=False):
        """Получить булеву переменную"""
        value = EnvironmentConfig.get(key, str(default)).lower()
        return value in ('true', '1', 'yes', 'on')
    
    @staticmethod
    def validate_required(required_keys):
        """Проверить наличие обязательных переменных"""
        missing = []
        for key in required_keys:
            if not os.environ.get(key):
                missing.append(key)
        
        if missing:
            raise ValueError(f"Отсутствуют обязательные переменные: {', '.join(missing)}")

# Пример использования в приложениях
if __name__ == "__main__":
    # Проверка работы утилиты
    port = EnvironmentConfig.get_int('PORT', 8080)
    log_level = EnvironmentConfig.get('LOG_LEVEL', 'INFO')
    print(f"Port: {port}, Log Level: {log_level}")
