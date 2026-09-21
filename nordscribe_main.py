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
        self.model_size = "tiny"  # Доступные: tiny, base, small, medium, large
        # tiny - самый быстрый для тестов, base - хороший баланс скорости/точности
        
        # Переменные для записи с микрофона
        self.is_recording = False
        self.recording_thread = None
        self.audio_data = []
        self.sample_rate = 16000  # Частота дискретизации для Whisper
        self.record_duration = 0  # Длительность текущей записи в секундах

        # Настройка сетки главного окна: 2 равные колонки
        self.grid_columnconfigure(0, weight=1)  # Левая колонка (Транскрибация)
        self.grid_columnconfigure(1, weight=1)  # Правая колонка (AI Анализ)
        self.grid_rowconfigure(0, weight=1)

        # === ЛЕВАЯ КОЛОНКА: ТРАНСКРИБАЦИЯ ===
        self.left_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.left_frame.grid(row=0, column=0, sticky="nsew", padx=(15, 7), pady=15)
        self.left_frame.grid_rowconfigure(0, weight=1)  # Текстовое поле растягивается
        self.left_frame.grid_rowconfigure(1, weight=0)  # Панель управления фиксирована

        # Заголовок
        self.lbl_transcript = ctk.CTkLabel(
            self.left_frame, 
            text="🎙️ Транскрибация (Whisper)", 
            font=ctk.CTkFont(size=18, weight="bold")
        )
        self.lbl_transcript.grid(row=0, column=0, sticky="nw", pady=(0, 10))

        # Текстовое поле вывода (растягивается)
        self.txt_transcript = ctk.CTkTextbox(
            self.left_frame, 
            wrap="word", 
            font=ctk.CTkFont(size=14)
        )
        self.txt_transcript.grid(row=1, column=0, sticky="nsew", pady=(0, 15))

        # --- НИЖНИЙ КОНТЕЙНЕР ЛЕВОЙ КОЛОНКИ (Кнопки + Прогресс) ---
        self.frame_left_controls = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        self.frame_left_controls.grid(row=2, column=0, sticky="ew")
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
        self.right_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.right_frame.grid(row=0, column=1, sticky="nsew", padx=(7, 15), pady=15)
        self.right_frame.grid_rowconfigure(0, weight=1)
        self.right_frame.grid_rowconfigure(1, weight=0)

        # Заголовок
        self.lbl_ai = ctk.CTkLabel(
            self.right_frame, 
            text="🧠 ИИ Анализ (Ollama)", 
            font=ctk.CTkFont(size=18, weight="bold")
        )
        self.lbl_ai.grid(row=0, column=0, sticky="nw", pady=(0, 10))

        # Текстовое поле вывода (растягивается)
        self.txt_ai_output = ctk.CTkTextbox(
            self.right_frame, 
            wrap="word", 
            font=ctk.CTkFont(size=14)
        )
        self.txt_ai_output.grid(row=1, column=0, sticky="nsew", pady=(0, 15))

        # --- НИЖНИЙ КОНТЕЙНЕР ПРАВОЙ КОЛОНКИ (Ввод + Кнопка) ---
        # Этот фрейм стоит на той же визуальной высоте, что и левый controls
        self.frame_right_controls = ctk.CTkFrame(self.right_frame, fg_color="transparent")
        self.frame_right_controls.grid(row=2, column=0, sticky="ew")
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
        """Загружает модель Whisper при первом запуске (в главном потоке для корректности)"""
        if self.whisper_model is None:
            # Загрузка модели происходит в том же потоке, где вызвана функция
            # Для загрузки в фоне можно использовать threading, но тогда нужно блокировать UI
            self.lbl_status.configure(text=f"Загрузка модели Whisper ({self.model_size})...")
            self.update()  # Принудительное обновление UI перед загрузкой
            try:
                # Загрузка модели (кэшируется после первого раза в ~/.cache/whisper/)
                self.whisper_model = whisper.load_model(self.model_size)
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

    def run_ollama_simulation(self, text, prompt):
        """Симуляция работы Ollama (заменить на ollama.generate)"""
        try:
            time.sleep(2)  # Имитация задержки сети/вычислений
            response = f"AI ответ на запрос '{prompt}' по тексту: {text[:50]}..."
            self.update_ai_result(response)
        except Exception as e:
            self.update_status(f"Ошибка AI: {str(e)}", side="right")
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
