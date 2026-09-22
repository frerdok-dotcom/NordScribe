import os
from pathlib import Path


def get_downloaded_whisper_models() -> list[str]:
    """
    Проверяет, какие официальные модели OpenAI Whisper уже скачаны локально.
    
    Returns:
        list[str]: Список названий полностью скачанных моделей.
    """
    # Список официальных моделей Whisper (включая .en версии)
    official_models = [
        'tiny', 'tiny.en',
        'base', 'base.en',
        'small', 'small.en',
        'medium', 'medium.en',
        'large', 'large-v1', 'large-v2', 'large-v3',
        'turbo'
    ]
    
    # Дефолтная директория кэша Whisper
    cache_dir = Path.home() / ".cache" / "whisper"
    
    downloaded_models = []
    
    # Если папка кэша не существует, возвращаем пустой список
    if not cache_dir.exists():
        return downloaded_models
    
    # Проверяем каждую модель
    for model_name in official_models:
        # Формируем имя файла модели (формат: {model_name}.pt)
        model_file = cache_dir / f"{model_name}.pt"
        
        # Проверяем существование и полноту загрузки файла
        if model_file.exists() and model_file.is_file():
            # Проверяем, что файл не пустой и имеет разумный размер
            # Минимальный размер для tiny модели ~75MB, проверяем > 1MB
            file_size = model_file.stat().st_size
            if file_size > 1_000_000:  # Более 1 MB
                downloaded_models.append(model_name)
    
    return downloaded_models


if __name__ == "__main__":
    # Пример использования
    models = get_downloaded_whisper_models()
    if models:
        print("Скачанные модели Whisper:")
        for model in models:
            print(f"  - {model}")
    else:
        print("Модели Whisper не найдены или ещё не скачаны.")
