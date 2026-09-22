#!/usr/bin/env python3
"""
Утилита для диагностики подключения к Ollama и проверки моделей Whisper.
Запускается из терминала: python ollama_whisper_check.py
"""

import sys
import socket
import urllib.request
import urllib.error
import json
from pathlib import Path

# Цвета для вывода в консоль
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def print_header(text):
    print(f"\n{Colors.BOLD}{Colors.HEADER}=== {text} ==={Colors.ENDC}\n")

def print_success(text):
    print(f"{Colors.OKGREEN}✓ {text}{Colors.ENDC}")

def print_error(text):
    print(f"{Colors.FAIL}✗ {text}{Colors.ENDC}")

def print_warning(text):
    print(f"{Colors.WARNING}⚠ {text}{Colors.ENDC}")

def print_info(text):
    print(f"{Colors.OKCYAN}ℹ {text}{Colors.ENDC}")

def check_ollama_connection(hosts=None, port=11434, timeout=5):
    """Проверяет подключение к Ollama по разным адресам."""
    if hosts is None:
        hosts = ['localhost', '127.0.0.1', '0.0.0.0']
    
    print_header("Диагностика подключения к Ollama")
    
    working_url = None
    
    for host in hosts:
        url = f"http://{host}:{port}"
        try:
            req = urllib.request.Request(f"{url}/api/tags", method='GET')
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    print_success(f"Подключение успешно: {url}")
                    
                    models = data.get('models', [])
                    if models:
                        print_info(f"Найдено моделей: {len(models)}")
                        for model in models[:5]: # Показываем первые 5
                            name = model.get('name', 'unknown')
                            print(f"   - {name}")
                        if len(models) > 5:
                            print(f"   ... и ещё {len(models) - 5}")
                    else:
                        print_warning("Список моделей пуст. Запустите 'ollama pull <model_name>'")
                    
                    working_url = url
                    break
        except (urllib.error.URLError, socket.timeout, ConnectionRefusedError):
            print_error(f"Не удалось подключиться: {url}")
        except Exception as e:
            print_error(f"Ошибка при подключении к {url}: {e}")

    if not working_url:
        print_error("Ollama не запущен или недоступен ни по одному из адресов.")
        print_info("Попробуйте запустить команду: ollama serve")
        return False
    
    return True

def check_whisper_models():
    """Проверяет наличие скачанных моделей Whisper."""
    print_header("Статус моделей Whisper")
    
    cache_dir = Path.home() / ".cache" / "whisper"
    official_models = [
        'tiny', 'tiny.en',
        'base', 'base.en',
        'small', 'small.en',
        'medium', 'medium.en',
        'large', 'large-v1', 'large-v2', 'large-v3', 'turbo'
    ]
    
    if not cache_dir.exists():
        print_warning(f"Директория кэша не найдена: {cache_dir}")
        print_info("Модели будут загружены при первом использовании.")
        return

    try:
        files = list(cache_dir.glob("*.pt"))
    except PermissionError:
        print_error(f"Нет прав доступа к директории: {cache_dir}")
        return

    if not files:
        print_warning("Директория кэша пуста. Модели не скачаны.")
        return

    found_models = set()
    
    print_info(f"Сканирование директории: {cache_dir}")
    for file_path in files:
        filename = file_path.name
        model_key = filename[:-3] # Убираем .pt
        
        # Проверяем точное совпадение
        if model_key in official_models:
            found_models.add(model_key)
            # Для large версий добавляем общий ключ
            if model_key.startswith('large'):
                found_models.add('large')
    
    # Фильтруем только базовые имена для красивого вывода
    base_names = ['tiny', 'base', 'small', 'medium', 'large', 'turbo']
    
    for name in base_names:
        if name in found_models:
            print_success(f"Модель '{name}' найдена ✅")
        elif name == 'large' and any(k.startswith('large') for k in found_models):
             print_success(f"Модель '{name}' (вариация) найдена ✅")
        else:
            print_error(f"Модель '{name}' не найдена ☁️")

def main():
    print(f"{Colors.BOLD}NordScribe Diagnostic Utility{Colors.ENDC}")
    
    # 1. Проверка Ollama
    ollama_ok = check_ollama_connection()
    
    # 2. Проверка Whisper
    check_whisper_models()
    
    print_header("Резюме")
    if ollama_ok:
        print_success("Ollama работает корректно.")
    else:
        print_error("Ollama не доступен. Проверьте запуск сервиса.")
        
    print_info("Запустите основное приложение для использования функций.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем.")
        sys.exit(0)
    except Exception as e:
        print_error(f"Критическая ошибка: {e}")
        sys.exit(1)
