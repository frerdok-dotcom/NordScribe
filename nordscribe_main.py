import customtkinter as ctk
import threading
import time
import os
import tempfile
import wave
from typing import Callable, Optional
import whisper
from tkinter import filedialog
import numpy as np
import sounddevice as sd
from scipy.io.wavfile import write
import re
try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False
    print("Предупреждение: библиотека ollama не установлена. Функции ИИ-анализа будут работать в режиме симуляции.")

# Настройки внешнего вида
ctk.set_appearance_mode("Dark")  # Темная тема
ctk.set_default_color_theme("blue")

class NordScribeApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("NordScribe - Локальная Транскрибация")
        self.geometry("1100x700")

        # Кэширование модели Whisper (чтобы не загружать каждый раз)
        self.whisper_model: Optional[whisper.Whisper] = None
        
        # Переменные для выбора моделей через UI
        self.whisper_var = ctk.StringVar(value="base")
        self.llm_var = ctk.StringVar(value="qwen2.5:1.5b")
        
        # Переменные для записи с микрофона
        self.is_recording = False
        self.recording_thread = None
        self.audio_data = []
        self.sample_rate = 16000  # Частота дискретизации для Whisper
        self.record_duration = 0  # Длительность текущей записи в секундах

        # Настройка сетки главного окна: 2 колонки и 4 строки
        self.grid_columnconfigure(0, weight=1)  # Левая колонка (Транскрибация)
        self.grid_columnconfigure(1, weight=1)  # Правая колонка (AI Анализ)
        self.grid_rowconfigure(0, weight=0)     # Строка 0: Панель настроек (фиксировано)
        self.grid_rowconfigure(1, weight=0)     # Строка 1: Заголовки (фиксировано)
        self.grid_rowconfigure(2, weight=1)     # Строка 2: Текстовые поля (растягиваются)
        self.grid_rowconfigure(3, weight=0)     # Строка 3: Панели управления (фиксировано)

        # === ВЕРХНЯЯ ПАНЕЛЬ НАСТРОЕК (СТРОКА 0) ===
        self.settings_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.settings_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=15, pady=(10, 5))
        self.settings_frame.grid_columnconfigure((0, 1, 2), weight=1)
        
        # Метка и выбор модели Whisper
        ctk.CTkLabel(
            self.settings_frame,
            text="Модель Whisper:",
            font=ctk.CTkFont(size=13, weight="bold")
        ).grid(row=0, column=0, sticky="e", padx=(0, 5))
        
        self.whisper_menu = ctk.CTkOptionMenu(
            self.settings_frame,
            variable=self.whisper_var,
            values=["tiny", "base", "small", "medium", "large"],
            width=120,
            height=30
        )
        self.whisper_menu.grid(row=0, column=1, sticky="w", padx=(0, 20))
        
        # Метка и выбор модели Ollama
        ctk.CTkLabel(
            self.settings_frame,
            text="Модель Ollama:",
            font=ctk.CTkFont(size=13, weight="bold")
        ).grid(row=0, column=2, sticky="e", padx=(0, 5))
        
        self.llm_menu = ctk.CTkOptionMenu(
            self.settings_frame,
            variable=self.llm_var,
            values=["qwen2.5:1.5b", "qwen2.5:3b", "llama3.2", "mistral", "gemma:2b"],
            width=140,
            height=30
        )
        self.llm_menu.grid(row=0, column=3, sticky="w")
        
        # Запуск проверки установленных моделей Ollama в фоновом потоке
        if OLLAMA_AVAILABLE:
            threading.Thread(target=self.check_local_models, daemon=True).start()

        # === ЛЕВАЯ КОЛОНКА: ТРАНСКРИБАЦИЯ ===
        # Заголовок (строка 1)
        self.lbl_transcript = ctk.CTkLabel(
            self, 
            text="🎙️ Транскрибация (Whisper)", 
            font=ctk.CTkFont(size=18, weight="bold")
        )
        self.lbl_transcript.grid(row=1, column=0, sticky="nw", padx=(15, 7), pady=(5, 10))

        # Текстовое поле вывода (строка 2, растягивается)
        self.txt_transcript = ctk.CTkTextbox(
            self, 
            wrap="word", 
            font=ctk.CTkFont(size=14)
        )
        self.txt_transcript.grid(row=2, column=0, sticky="nsew", padx=(15, 7), pady=(0, 15))

        # --- НИЖНИЙ КОНТЕЙНЕР ЛЕВОЙ КОЛОНКИ (Кнопки + Прогресс) - СТРОКА 3 ---
        self.frame_left_controls = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_left_controls.grid(row=3, column=0, sticky="ew", padx=(15, 7), pady=(0, 15))
        self.frame_left_controls.grid_columnconfigure((0, 1), weight=1)

        # Кнопка загрузки аудио файла (синяя, стандартная)
        self.btn_load_audio = ctk.CTkButton(
            self.frame_left_controls,
            text="📁 Загрузить аудиофайл",
            command=self.start_transcription_thread,
            height=45,
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.btn_load_audio.grid(row=0, column=0, padx=(0, 10), pady=(0, 10), sticky="ew")

        # --- ИСПРАВЛЕННАЯ КНОПКА ЗАПИСИ С МИКРОФОНА ---
        # Красный цвет в покое, темно-красный при наведении, жирный шрифт
        self.btn_record_mic = ctk.CTkButton(
            self.frame_left_controls,
            text="🎤 Записать с микрофона",
            command=self.toggle_recording,
            fg_color="#D32F2F",          # Красный цвет в покое
            hover_color="#B71C1C",       # Темно-красный при наведении
            height=45,
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.btn_record_mic.grid(row=0, column=1, padx=(10, 0), pady=(0, 10), sticky="ew")
        # ---------------------------------------------

        # Индикатор прогресса
        self.progress_bar = ctk.CTkProgressBar(self.frame_left_controls, height=10)
        self.progress_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 10))
        self.progress_bar.set(0)

        self.lbl_status = ctk.CTkLabel(
            self.frame_left_controls, 
            text="Готов к работе", 
            text_color="gray",
            font=ctk.CTkFont(size=13)
        )
        self.lbl_status.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 5))

        # === ПРАВАЯ КОЛОНКА: AI АНАЛИЗ ===
        # Заголовок (строка 1)
        self.lbl_ai = ctk.CTkLabel(
            self, 
            text="🧠 ИИ Анализ (Ollama)", 
            font=ctk.CTkFont(size=18, weight="bold")
        )
        self.lbl_ai.grid(row=1, column=1, sticky="nw", padx=(7, 15), pady=(5, 10))

        # Текстовое поле вывода (строка 2, растягивается)
        self.txt_ai_output = ctk.CTkTextbox(
            self, 
            wrap="word", 
            font=ctk.CTkFont(size=14)
        )
        self.txt_ai_output.grid(row=2, column=1, sticky="nsew", padx=(7, 15), pady=(0, 15))

        # --- НИЖНИЙ КОНТЕЙНЕР ПРАВОЙ КОЛОНКИ (Ввод + Кнопка) - СТРОКА 3 ---
        # Этот фрейм стоит на той же визуальной высоте, что и левый controls
        self.frame_right_controls = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_right_controls.grid(row=3, column=1, sticky="ew", padx=(7, 15), pady=(0, 15))
        self.frame_right_controls.grid_columnconfigure(0, weight=1)

        self.entry_prompt = ctk.CTkEntry(
            self.frame_right_controls,
            placeholder_text="Введите запрос к ИИ...",
            height=45,
            font=ctk.CTkFont(size=14)
        )
        self.entry_prompt.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        self.btn_analyze = ctk.CTkButton(
            self.frame_right_controls,
            text="✨ Анализировать текст",
            command=self.start_ai_thread,
            height=45,
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.btn_analyze.grid(row=1, column=0, sticky="ew", pady=(0, 5))

    # --- ЛОГИКА МНОГОПОТОЧНОСТИ ---

    def load_whisper_model(self):
        """Загружает модель Whisper при первом запуске с выбранной в UI моделью"""
        if self.whisper_model is None:
            # Получаем выбранную модель из переменной UI
            selected_model = self.whisper_var.get()
            self.lbl_status.configure(text=f"Загрузка модели Whisper ({selected_model})...")
            self.update()  # Принудительное обновление UI перед загрузкой
            try:
                # Загрузка модели (кэшируется после первого раза в ~/.cache/whisper/)
                self.whisper_model = whisper.load_model(selected_model)
                self.lbl_status.configure(text="Модель готова к работе")
                return True
            except Exception as e:
                self.lbl_status.configure(text=f"Ошибка загрузки модели: {str(e)}")
                return False
        return True

    def start_transcription_thread(self):
        """Запускает тяжелую задачу Whisper в отдельном потоке"""
        # Сначала загружаем модель, если еще не загружена
        if not self.load_whisper_model():
            return
        
        # Диалог выбора файла
        file_path = filedialog.askopenfilename(
            title="Выберите аудиофайл",
            filetypes=[
                ("Аудио файлы", "*.mp3 *.wav *.ogg *.flac *.m4a"),
                ("Все файлы", "*.*")
            ]
        )
        
        if not file_path:
            self.btn_load_audio.configure(state="normal")
            self.lbl_status.configure(text="Файл не выбран")
            return

        self.btn_load_audio.configure(state="disabled")
        self.lbl_status.configure(text=f"Обработка: {os.path.basename(file_path)}")
        self.progress_bar.set(0)
        
        # Очистка предыдущего результата
        self.txt_transcript.delete("1.0", "end")

        # Запуск транскрибации в отдельном потоке
        thread = threading.Thread(target=self.run_whisper_transcription, args=(file_path,), daemon=True)
        thread.start()
    
    def toggle_recording(self):
        """Переключает состояние записи с микрофона"""
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()
    
    def start_recording(self):
        """Начинает запись с микрофона в отдельном потоке"""
        if not self.load_whisper_model():
            return
        
        self.is_recording = True
        self.audio_data = []
        self.record_duration = 0
        
        # Обновление UI кнопки: меняем текст и цвет на более темный красный
        self.btn_record_mic.configure(
            text="⏹ Остановить запись",
            fg_color="#B71C1C",      # Темнее при записи
            hover_color="#8E0000"
        )
        self.lbl_status.configure(text="🔴 Запись идет... Нажмите 'Остановить запись' когда закончите", text_color="#D32F2F")
        self.txt_transcript.delete("1.0", "end")
        
        # Запуск потока записи
        self.recording_thread = threading.Thread(target=self.record_audio, daemon=True)
        self.recording_thread.start()
    
    def record_audio(self):
        """Функция записи аудио в цикле"""
        try:
            stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype='int16',
                callback=self.audio_callback
            )
            
            with stream:
                while self.is_recording:
                    time.sleep(0.1)
                    self.record_duration += 0.1
                    # Обновляем статус каждую секунду
                    if int(self.record_duration) % 1 == 0:
                        self.update_status(f"Запись идет... {int(self.record_duration)} сек")
                        
        except Exception as e:
            self.update_status(f"Ошибка записи: {str(e)}")
            self.is_recording = False
    
    def audio_callback(self, indata, frames, time_info, status):
        """Коллбэк для захвата аудио данных"""
        if self.is_recording:
            self.audio_data.append(indata.copy())
    
    def stop_recording(self):
        """Останавливает запись и начинает транскрибацию"""
        self.is_recording = False
        
        # Возврат кнопки в исходное красное состояние
        self.btn_record_mic.configure(
            text="🎤 Записать с микрофона",
            fg_color="#D32F2F",
            hover_color="#B71C1C"
        )
        self.lbl_status.configure(text="Обработка записанного аудио...", text_color="gray")
        
        if self.audio_data:
            # Сохраняем записанное аудио во временный файл
            try:
                audio_data = np.concatenate(self.audio_data, axis=0)
                
                # Создаем временный WAV файл
                temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
                temp_path = temp_file.name
                temp_file.close()
                
                # Записываем WAV файл
                write(temp_path, self.sample_rate, audio_data)
                
                # Запускаем транскрибацию временного файла
                thread = threading.Thread(target=self.run_whisper_transcription, args=(temp_path, True), daemon=True)
                thread.start()
                
            except Exception as e:
                self.update_status(f"Ошибка сохранения аудио: {str(e)}")
                self.btn_record_mic.configure(state="normal")
        else:
            self.update_status("Аудио не записано")
            self.btn_record_mic.configure(state="normal")

    def run_whisper_transcription(self, file_path: str, is_temp_file: bool = False):
        """Реальная транскрибация аудио с помощью Whisper"""
        try:
            # Whisper не предоставляет детальный прогресс для коротких файлов,
            # но мы можем обновлять статус во время обработки
            self.update_status("Начинаю распознавание...")
            
            # Выполняем транскрибацию
            # verbose=None показывает прогресс в консоли, но мы используем свой UI
            result = self.whisper_model.transcribe(
                file_path,
                language="ru",  # Можно сделать настраиваемым параметром
                task="transcribe",
                verbose=False
            )
            
            # Получаем текст из результата
            transcribed_text = result.get("text", "").strip()
            
            # Обновляем прогресс до 100%
            self.update_progressbar(1.0)
            
            # Вставляем результат в текстовое поле
            self.update_transcript_result(transcribed_text)
            
            # Удаляем временный файл, если это запись с микрофона
            if is_temp_file and os.path.exists(file_path):
                try:
                    os.unlink(file_path)
                except Exception as e:
                    print(f"Не удалось удалить временный файл: {e}")
            
        except Exception as e:
            self.update_status(f"Ошибка транскрибации: {str(e)}")
        finally:
            self.btn_load_audio.configure(state="normal")
            self.btn_record_mic.configure(state="normal")

    def start_ai_thread(self):
        """Запускает задачу Ollama в отдельном потоке"""
        prompt = self.entry_prompt.get()
        text_to_analyze = self.txt_transcript.get("1.0", "end").strip()

        if not text_to_analyze:
            self.update_status("Сначала загрузите аудио!", side="left")
            return

        self.btn_analyze.configure(state="disabled")
        self.txt_ai_output.delete("1.0", "end")
        self.txt_ai_output.insert("1.0", "Думаю...\n")

        thread = threading.Thread(target=self.run_ollama_simulation, args=(text_to_analyze, prompt), daemon=True)
        thread.start()

    def check_local_models(self):
        """
        Фоновая проверка установленных моделей Ollama.
        Запрашивает список через API, форматирует его с иконками и обновляет меню.
        Выполняется в отдельном потоке при старте.
        """
        if not OLLAMA_AVAILABLE:
            return
            
        try:
            # Получаем список локальных моделей через ollama.list()
            local_models_data = ollama.list()
            # Извлекаем имена установленных моделей
            installed_names = {model['name'] for model in local_models_data.get('models', [])}
            
            # Список всех возможных моделей для отображения
            all_candidates = ["qwen2.5:1.5b", "qwen2.5:3b", "llama3.2", "mistral", "gemma:2b"]
            
            # Формируем список с маркерами статуса
            formatted_list = []
            for model_name in all_candidates:
                if model_name in installed_names:
                    formatted_list.append(f"🟢 {model_name}")  # Установлена
                else:
                    formatted_list.append(f"⚪ {model_name}")  # Не установлена
            
            # Если список пуст, добавляем сообщение об ошибке
            if not formatted_list:
                formatted_list = ["⚪ Нет доступных моделей"]

            # Безопасное обновление UI через after()
            def update_menu():
                self.llm_menu.configure(values=formatted_list)
                # Устанавливаем значение по умолчанию (первая установленная или первая в списке)
                default_val = next((m for m in formatted_list if m.startswith("🟢")), formatted_list[0])
                self.llm_menu.set(default_val)
                
            self.after(0, update_menu)

        except Exception as e:
            print(f"[ОШИБКА Ollama] Не удалось получить список моделей: {e}")
            # Обновляем меню с сообщением об ошибке
            self.after(0, lambda: self.llm_menu.configure(values=["⚪ Ошибка подключения к Ollama"]))

    def run_ollama_simulation(self, text, prompt):
        """
        Отправка текста на анализ в локальную LLM (Ollama).
        Автоматически очищает имя модели от визуальных маркеров (эмодзи).
        """
        try:
            # Получаем выбранную модель из UI (с маркерами)
            selected_model_full = self.llm_var.get()
            
            # Очищаем имя модели от эмодзи и лишних символов, оставляем только системное имя
            clean_model_name = re.sub(r'[^\w.:]', '', selected_model_full).strip()
            
            # Проверка доступности библиотеки
            if not OLLAMA_AVAILABLE:
                time.sleep(2)
                response = f"[Симуляция] AI ({clean_model_name}) ответ на запрос '{prompt}' по тексту: {text[:50]}..."
                self.update_ai_result(response)
                return

            # Обновляем статус
            self.update_status(f"Генерация ответа ({clean_model_name})...", side="right")
            
            # Реальный вызов Ollama API с очищенным именем модели
            response_data = ollama.generate(
                model=clean_model_name,
                prompt=f"{prompt}:\n{text}"
            )
            
            # Извлекаем текст ответа
            result_text = response_data.get('response', 'Нет ответа')
            self.update_ai_result(f"🤖 AI ({clean_model_name}):\n{result_text}")
            
        except Exception as e:
            error_msg = f"Ошибка модели '{clean_model_name}': {str(e)}"
            print(error_msg)
            self.update_ai_result(f"❌ {error_msg}")
            self.update_status("Ошибка генерации", side="right")
        finally:
            self.btn_analyze.configure(state="normal")

    # --- МЕТОДЫ ОБНОВЛЕНИЯ UI (Безопасный вызов из потоков) ---
    # В Tkinter/CustomTkinter обновление виджетов из других потоков обычно работает,
    # но для сложных операций лучше использовать after() или очереди. 
    # Для простоты здесь прямой вызов, так как GPython освобождает GIL, но события рендеринга идут в главный поток.

    def update_progressbar(self, value):
        self.after(0, lambda: self.progress_bar.set(value))

    def update_transcript_result(self, text):
        self.after(0, lambda: self.txt_transcript.insert("1.0", text))
        self.after(0, lambda: self.lbl_status.configure(text="Готово"))
        self.after(0, lambda: self.progress_bar.set(1))

    def update_ai_result(self, text):
        self.after(0, lambda: self.txt_ai_output.delete("1.0", "end"))
        self.after(0, lambda: self.txt_ai_output.insert("1.0", text))

    def update_status(self, message, side="left"):
        if side == "left":
            self.after(0, lambda: self.lbl_status.configure(text=message))
        else:
            # Можно добавить label статуса справа, если нужно
            pass

if __name__ == "__main__":
    app = NordScribeApp()
    app.mainloop()
