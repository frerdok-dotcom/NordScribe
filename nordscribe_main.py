import customtkinter as ctk
import threading
import time
from typing import Callable

# Настройки внешнего вида
ctk.set_appearance_mode("Dark")  # Темная тема
ctk.set_default_color_theme("blue")

class NordScribeApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("NordScribe - Локальная Транскрибация")
        self.geometry("1100x700")

        # Сетка布局: 2 колонки
        self.grid_columnconfigure(0, weight=1)  # Левая колонка (Транскрибация)
        self.grid_columnconfigure(1, weight=1)  # Правая колонка (AI Анализ)
        self.grid_rowconfigure(0, weight=1)

        # === ЛЕВАЯ КОЛОНКА: ТРАНСКРИБАЦИЯ ===
        self.left_frame = ctk.CTkFrame(self, corner_radius=0)
        self.left_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        self.lbl_transcript = ctk.CTkLabel(self.left_frame, text="Распознавание речи (Whisper)", font=ctk.CTkFont(size=16, weight="bold"))
        self.lbl_transcript.pack(pady=10)

        self.txt_transcript = ctk.CTkTextbox(self.left_frame, wrap="word", font=ctk.CTkFont(size=14))
        self.txt_transcript.pack(fill="both", expand=True, padx=10, pady=10)

        self.btn_load_audio = ctk.CTkButton(self.left_frame, text="Загрузить аудио", command=self.start_transcription_thread)
        self.btn_load_audio.pack(pady=10)

        # Индикатор прогресса (пока скрыт или в нулевом положении)
        self.progress_bar = ctk.CTkProgressBar(self.left_frame)
        self.progress_bar.pack(pady=10, padx=20, fill="x")
        self.progress_bar.set(0)

        self.lbl_status = ctk.CTkLabel(self.left_frame, text="Готов к работе", text_color="gray")
        self.lbl_status.pack(pady=5)

        # === ПРАВАЯ КОЛОНКА: AI АНАЛИЗ ===
        self.right_frame = ctk.CTkFrame(self, corner_radius=0)
        self.right_frame.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)

        self.lbl_ai = ctk.CTkLabel(self.right_frame, text="AI Анализ (Ollama)", font=ctk.CTkFont(size=16, weight="bold"))
        self.lbl_ai.pack(pady=10)

        self.txt_ai_output = ctk.CTkTextbox(self.right_frame, wrap="word", font=ctk.CTkFont(size=14))
        self.txt_ai_output.pack(fill="both", expand=True, padx=10, pady=10)

        self.entry_prompt = ctk.CTkEntry(self.right_frame, placeholder_text="Введите запрос к ИИ...")
        self.entry_prompt.pack(fill="x", padx=10, pady=5)

        self.btn_analyze = ctk.CTkButton(self.right_frame, text="Анализировать текст", command=self.start_ai_thread)
        self.btn_analyze.pack(pady=10)

    # --- ЛОГИКА МНОГОПОТОЧНОСТИ ---

    def start_transcription_thread(self):
        """Запускает тяжелую задачу Whisper в отдельном потоке"""
        self.btn_load_audio.configure(state="disabled")
        self.lbl_status.configure(text="Обработка аудио...")
        self.progress_bar.set(0)
        
        # Очистка предыдущего результата
        self.txt_transcript.delete("1.0", "end")

        thread = threading.Thread(target=self.run_whisper_simulation, daemon=True)
        thread.start()

    def run_whisper_simulation(self):
        """Симуляция работы Whisper (заменить на реальный вызов model.transcribe)"""
        try:
            # Эмуляция процесса с прогрессом
            for i in range(1, 101, 5):
                time.sleep(0.1)  # Имитация вычислений
                # Обновление UI должно происходить в главном потоке!
                self.update_progressbar(i / 100)
            
            result_text = "Это пример распознанного текста. Здесь должен быть результат работы Whisper."
            self.update_transcript_result(result_text)
        except Exception as e:
            self.update_status(f"Ошибка: {str(e)}")
        finally:
            self.btn_load_audio.configure(state="normal")

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
