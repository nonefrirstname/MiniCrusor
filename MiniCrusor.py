import sys
import os
import subprocess
import re
import requests
import json
import tempfile
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout,
    QFileDialog, QLabel, QHBoxLayout, QPushButton, QLineEdit,
    QAction, QMessageBox, QSplitter, QPlainTextEdit, QComboBox, QSizePolicy, QTextEdit, QCheckBox, QMenuBar
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QThread, QObject, QMimeData, QEvent, QSize, QByteArray
from PyQt5.QtGui import QFont, QColor, QPixmap, QImage, QIcon, QDragEnterEvent, QDropEvent, QKeySequence, QPainter
from PyQt5.Qsci import QsciScintilla, QsciLexerPython, QsciAPIs
from PyQt5.QtSvg import QSvgRenderer

MARKER_FUNC = 1
MARKER_CLASS = 2

class ExpandingTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.textChanged.connect(self.updateGeometry)

        self.send_btn = QPushButton("⤵️", self)
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: #3c3f41;
                border: 1px solid #555;
                border-radius: 22px; /* половина от 44 */
                font-size: 22px;
            }
            QPushButton:hover {
                background-color: #4a4d50;
            }
            QPushButton:pressed {
                background-color: #585b5e;
            }
        """)
        self.send_btn.setFixedSize(44, 44)
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setToolTip("Отправить сообщение")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        button_size = self.send_btn.size()
        padding = 8
        
        # Помещаем кнопку в правый нижний угол, учитывая ширину скроллбара
        x = self.width() - button_size.width() - padding
        if self.verticalScrollBar().isVisible():
            x -= self.verticalScrollBar().width()
        y = self.height() - button_size.height() - padding
        self.send_btn.move(x, y)

        # Добавляем отступ справа, чтобы текст не заезжал под кнопку
        self.setViewportMargins(0, 0, button_size.width() + padding, 0)

    def sizeHint(self):
        doc_height = self.document().size().height()
        frame_h = self.frameWidth() * 2
        margins = self.contentsMargins()
        ideal_height = doc_height + frame_h + margins.top() + margins.bottom()
        
        h = max(ideal_height, self.minimumHeight())

        if self.maximumHeight() != 16777215:
            h = min(h, self.maximumHeight())
            
        return QSize(super().sizeHint().width(), int(h))

class CodeEditor(QsciScintilla):
    modificationChanged = pyqtSignal(bool)
    code_submitted_for_ai = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        font = QFont("Consolas", 12)
        self.setFont(font)
        self.setMarginsFont(font)

        self.lexer = QsciLexerPython()
        self.lexer.setDefaultFont(font)

        # --- Настройка цветов для синтаксиса Python (стиль One Dark) ---
        paper_color = QColor("#282c34")
        
        # Устанавливаем фон для всех стилей
        for style in range(128):
            self.lexer.setPaper(paper_color, style)
            
        # Настраиваем цвета для конкретных токенов
        self.lexer.setColor(QColor("#abb2bf"), QsciLexerPython.Default)
        self.lexer.setColor(QColor("#5c6370"), QsciLexerPython.Comment)
        self.lexer.setColor(QColor("#d19a66"), QsciLexerPython.Number)
        self.lexer.setColor(QColor("#c678dd"), QsciLexerPython.Keyword)
        self.lexer.setColor(QColor("#98c379"), QsciLexerPython.DoubleQuotedString)
        self.lexer.setColor(QColor("#98c379"), QsciLexerPython.SingleQuotedString)
        self.lexer.setColor(QColor("#e5c07b"), QsciLexerPython.ClassName)
        self.lexer.setColor(QColor("#61afef"), QsciLexerPython.FunctionMethodName)
        self.lexer.setColor(QColor("#56b6c2"), QsciLexerPython.Operator)
        self.lexer.setColor(QColor("#abb2bf"), QsciLexerPython.Identifier)
        self.lexer.setColor(QColor("#98c379"), QsciLexerPython.TripleSingleQuotedString)
        self.lexer.setColor(QColor("#98c379"), QsciLexerPython.TripleDoubleQuotedString)
        self.lexer.setColor(QColor("#d19a66"), QsciLexerPython.Decorator)
        # Ошибки в строках
        self.lexer.setColor(QColor("#e06c75"), QsciLexerPython.UnclosedString)
        self.lexer.setEolFill(True, QsciLexerPython.UnclosedString)
        self.lexer.setPaper(QColor("#3a2426"), QsciLexerPython.UnclosedString)
        
        self.setLexer(self.lexer)

        self.api = QsciAPIs(self.lexer)
        for kw in ["def", "class", "import", "from", "return", "if", "else", "elif",
                   "for", "while", "try", "except", "with", "as", "pass", "break"]:
            self.api.add(kw)
        self.api.prepare()
        self.setAutoCompletionSource(QsciScintilla.AcsAll)
        self.setAutoCompletionThreshold(1)

        self.setMarginType(0, QsciScintilla.NumberMargin)
        self.setMarginWidth(0, 40)

        self.setMarginsBackgroundColor(QColor("#282c34"))
        self.setMarginsForegroundColor(QColor("#b0b0b0"))

        self.setMarginType(1, QsciScintilla.SymbolMargin)
        self.setMarginWidth(1, 12)
        self.setMarginSensitivity(1, True)
        self.markerDefine(QsciScintilla.RightArrow, MARKER_FUNC)
        self.setMarkerBackgroundColor(QColor("#00AA00"), MARKER_FUNC)
        self.markerDefine(QsciScintilla.Circle, MARKER_CLASS)
        self.setMarkerBackgroundColor(QColor("#0000AA"), MARKER_CLASS)

        self.marginClicked.connect(self.on_margin_clicked)

        self.setIndentationsUseTabs(False)
        self.setIndentationWidth(4)
        self.setTabWidth(4)
        self.setIndentationGuides(True)

        self.modificationChanged.connect(self.modificationChanged.emit)
        self.textChanged.connect(self.update_markers)

        self.update_markers()

        self.setPaper(QColor("#282c34"))
        self.setColor(QColor("#e0e0e0"))
        self.setCaretLineBackgroundColor(QColor("#232629"))
        self.setCaretForegroundColor(QColor("#00ffcc"))
        self.setSelectionBackgroundColor(QColor("#444a56"))
        self.setSelectionForegroundColor(QColor("#ffffff"))
        self.setBraceMatching(QsciScintilla.SloppyBraceMatch)
        self.setMatchedBraceBackgroundColor(QColor("#444a56"))
        self.setMatchedBraceForegroundColor(QColor("#00ffcc"))
        self.setUnmatchedBraceForegroundColor(QColor("#ff5555"))
        self.setWhitespaceVisibility(QsciScintilla.WsVisible)
        self.setWhitespaceForegroundColor(QColor("#444a56"))
        self.setEdgeColor(QColor("#393e46"))
        self.setEdgeMode(QsciScintilla.EdgeLine)
        self.setEdgeColumn(120)
        self.setUtf8(True)
        
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

    def show_context_menu(self, pos):
        menu = self.createStandardContextMenu()
        if self.hasSelectedText():
            ask_ai_action = QAction("Спросить у нейросети", self)
            ask_ai_action.triggered.connect(self.ask_ai_about_selection)
            menu.addSeparator()
            menu.addAction(ask_ai_action)
        menu.exec_(self.mapToGlobal(pos))

    def ask_ai_about_selection(self):
        selected_text = self.selectedText()
        if selected_text:
            self.code_submitted_for_ai.emit(selected_text)

    def update_markers(self):
        self.markerDeleteAll(MARKER_FUNC)
        self.markerDeleteAll(MARKER_CLASS)
        text = self.text()
        for i, line in enumerate(text.splitlines()):
            if re.match(r"\s*def\s", line):
                self.markerAdd(i, MARKER_FUNC)
            elif re.match(r"\s*class\s", line):
                self.markerAdd(i, MARKER_CLASS)

    def on_margin_clicked(self, margin, line, modifiers):
        if margin == 1:
            markers = self.markersAtLine(line)
            if markers & (1 << (MARKER_FUNC - 1)):
                self.setCursorPosition(line, 0)
                self.ensureLineVisible(line)
            elif markers & (1 << (MARKER_CLASS - 1)):
                self.setCursorPosition(line, 0)
                self.ensureLineVisible(line)

    def is_modified(self):
        return self.isModified()

    def update_autocomplete(self):
        words = set(re.findall(r"\b\w{3,}\b", self.text()))
        for w in words:
            self.api.add(w)
        self.api.prepare()

class EditorTab(QWidget):
    code_for_ai = pyqtSignal(str)
    def __init__(self, filepath=None):
        super().__init__()
        self.filepath = filepath
        self.filename = os.path.basename(filepath) if filepath else "Без имени"
        self.is_saved = True

        self.editor = CodeEditor()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.editor)
        self.setLayout(layout)

        self.editor.modificationChanged.connect(self.on_modified)
        self.editor.code_submitted_for_ai.connect(self.code_for_ai)

        if filepath:
            self.load_file(filepath)

    def load_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.editor.setText(f.read())
            self.filepath = path
            self.filename = os.path.basename(path)
            self.is_saved = True
            self.editor.setModified(False)
            self.editor.update_autocomplete()
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось открыть файл:\n{e}")

    def save_file(self, path=None):
        path = path or self.filepath
        if not path:
            return False
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.editor.text())
            self.filepath = path
            self.filename = os.path.basename(path)
            self.is_saved = True
            self.editor.setModified(False)
            self.editor.update_autocomplete()
            return True
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить файл:\n{e}")
            return False

    def on_modified(self, modified):
        self.is_saved = not modified
        self.parent().parent().update_tab_title(self)

class ConsoleWidget(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setMaximumHeight(150)
        font = QFont("Consolas", 11)
        self.setFont(font)
        self.setContentsMargins(12, 0, 12, 8)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def append_text(self, text):
        self.appendPlainText(text)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

class ModelDownloader(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(str)
    def __init__(self, model_name):
        super().__init__()
        self.model_name = model_name
    def run(self):
        import requests
        url = "http://localhost:11434/api/pull"
        data = {"name": self.model_name}
        last_msg = None
        file_size_reported = False
        try:
            with requests.post(url, json=data, stream=True, timeout=300) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        info = json.loads(line)
                        status = info.get("status", "")
                        if status != last_msg:
                            if "pulling manifest" in status:
                                self.progress.emit(f"Получение манифеста...")
                            elif "verifying" in status:
                                self.progress.emit(f"Проверка...")
                            elif "downloading" in status and not file_size_reported:
                                total = info.get("total", 0)
                                if total > 0:
                                    self.progress.emit(f"Размер файла: {total/1e9:.2f} GB")
                                    file_size_reported = True
                            last_msg = status
                        
                        progress = info.get("progress", "")
                        percent_str = ""
                        if progress and "/" in progress:
                            try:
                                left, right = progress.split("/")
                                left_val, left_unit = left.strip().split()
                                right_val, right_unit = right.strip().split()
                                def to_bytes(val, unit):
                                    val = float(val)
                                    if unit.lower().startswith("k"): return val * 1024
                                    if unit.lower().startswith("m"): return val * 1024**2
                                    if unit.lower().startswith("g"): return val * 1024**3
                                    return val
                                left_b = to_bytes(left_val, left_unit)
                                right_b = to_bytes(right_val, right_unit)
                                if right_b > 0:
                                    percent = (left_b / right_b) * 100
                                    percent_str = f"{int(percent)}%"
                                self.progress.emit(f"Загрузка: {progress} ({percent_str})")
                            except ValueError:
                                self.progress.emit(f"Загрузка: {progress}")

                    except json.JSONDecodeError:
                        self.progress.emit(f"Не удалось обработать: {line}")

            self.finished.emit(f"[Ollama] Модель '{self.model_name}' успешно загружена.")
        except Exception as e:
            self.finished.emit(f"[Ошибка Ollama] Не удалось загрузить модель: {e}")

class OllamaWorker(QThread):
    result = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, prompt, model):
        super().__init__()
        self.prompt = prompt
        self.model = model

    def run(self):
        import requests
        url = "http://localhost:11434/api/generate"
        data = {
            "model": self.model,
            "prompt": self.prompt,
            "stream": False
        }
        try:
            resp = requests.post(url, json=data, timeout=120)
            resp.raise_for_status()
            response_data = resp.json()
            self.result.emit(response_data.get("response", "Нет ответа в JSON"))
        except Exception as e:
            self.error.emit(f"Ошибка Ollama: {e}")
        finally:
            self.finished.emit()

class ProcessRunner(QObject):
    output_received = pyqtSignal(str)
    finished = pyqtSignal(int)

    def __init__(self, path_to_script):
        super().__init__()
        self.path_to_script = path_to_script
        self.proc = None

    def run(self):
        try:
            self.proc = subprocess.Popen(
                [sys.executable, self.path_to_script],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='ignore',
                bufsize=1
            )
        except Exception as e:
            self.output_received.emit(f"[Ошибка запуска]: {e}")
            self.finished.emit(-1)
            return

        for line in iter(self.proc.stdout.readline, ''):
            self.output_received.emit(line.rstrip())

        self.proc.stdout.close()
        return_code = self.proc.wait()
        self.finished.emit(return_code)

class ChatWidget(QWidget):
    def __init__(self, console=None, parent_window=None, parent=None):
        super().__init__(parent)
        self.console = console
        self.parent_window = parent_window
        self.downloader = None
        self.ollama_worker = None
        self.suggested_code = ""
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 0, 12, 0)

        top_layout = QHBoxLayout()
        self.model_box = QComboBox()
        self.model_info = {}
        self.download_btn = QPushButton("⬇️")
        self.download_btn.setToolTip("Скачать модель")
        self.download_btn.clicked.connect(self.download_model)
        self.update_btn = QPushButton("🔄")
        self.update_btn.setToolTip("Обновить модель")
        self.update_btn.clicked.connect(self.update_model)
        self.update_btn.setEnabled(False)
        top_layout.addWidget(QLabel("Модель:"))
        top_layout.addWidget(self.model_box)
        top_layout.addWidget(self.download_btn)
        top_layout.addWidget(self.update_btn)
        top_layout.addStretch()

        chat_options_layout = QHBoxLayout()
        self.include_code_checkbox = QCheckBox("Включить код из активной вкладки")
        self.include_code_checkbox.setChecked(True)
        chat_options_layout.addWidget(self.include_code_checkbox)
        chat_options_layout.addStretch()

        self.history = QTextEdit()
        self.history.setReadOnly(True)
        self.history.setFont(QFont("Consolas", 10))
        self.history.setAcceptDrops(True)
        self.history.viewport().setAcceptDrops(True)
        self.history.installEventFilter(self)
        self.history.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        self.history.setMinimumHeight(60)

        # --- Умный виджет ввода со встроенной кнопкой ---
        self.input = ExpandingTextEdit()
        self.input.setPlaceholderText("Введите сообщение для нейросети...")
        self.input.setMinimumHeight(40)
        self.input.setMaximumHeight(150)
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.input.setAcceptRichText(False)
        self.input.setTabChangesFocus(True)
        self.input.installEventFilter(self)
        
        # Соединяем сигнал кнопки из нашего нового виджета
        self.input.send_btn.clicked.connect(self.send_message)
        
        self.apply_code_btn = QPushButton("Применить предложенный код")
        self.apply_code_btn.clicked.connect(self.apply_suggested_code)
        self.apply_code_btn.hide()

        layout.addLayout(top_layout)
        layout.addWidget(self.history)
        layout.addLayout(chat_options_layout)
        layout.addWidget(self.input) # Добавляем только поле ввода
        layout.addWidget(self.apply_code_btn)
        self.setLayout(layout)

        self.current_model = ""
        self.current_version = None
        self.model_box.currentIndexChanged.connect(self.on_model_changed)

        self.refresh_models()
        self.on_model_changed()

    def eventFilter(self, obj, event):
        if obj == self.input and event.type() == event.KeyPress:
            if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
                if not (event.modifiers() & Qt.ShiftModifier):
                    self.send_message()
                    return True
                else:
                    return super().eventFilter(obj, event)

        if obj == self.history:
            if event.type() == event.DragEnter:
                if event.mimeData().hasImage() or event.mimeData().hasUrls():
                    event.accept()
                    return True
            if event.type() == event.Drop:
                if event.mimeData().hasImage():
                    image = event.mimeData().imageData()
                    if isinstance(image, QImage):
                        pix = QPixmap.fromImage(image)
                        self.append_image(pix)
                    return True
                if event.mimeData().hasUrls():
                    for url in event.mimeData().urls():
                        path = url.toLocalFile()
                        if path.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif')):
                            pix = QPixmap(path)
                            self.append_image(pix, path)
                    return True
            if event.type() == event.KeyPress and event.matches(QKeySequence.Paste):
                clipboard = QApplication.clipboard()
                mime = clipboard.mimeData()
                if mime.hasImage():
                    image = clipboard.image()
                    pix = QPixmap.fromImage(image)
                    self.append_image(pix)
                    return True
        return super().eventFilter(obj, event)

    def append_image(self, pixmap, path=None):
        from base64 import b64encode
        buffer = QImage(pixmap.toImage())
        ba = QByteArray()
        buffer.save(ba, 'PNG')
        b64 = b64encode(ba.data()).decode('utf-8')
        html = f'<img src="data:image/png;base64,{b64}" width="120"/>'
        if path:
            html += f'<br><span style="color:#aaa;font-size:10pt">{path}</span>'
        self.history.append(html)
        self._adjust_history_height()

    def _adjust_history_height(self):
        doc_height = self.history.document().size().height()
        margin = 20
        target_height = int(doc_height + margin)
        
        min_h = 60
        max_h = self.maximumHeight() if self.maximumHeight() != 16777215 else 800
        
        final_height = min(max_h, max(min_h, target_height))
        self.history.setMinimumHeight(final_height)

    def refresh_models(self):
        import re
        available = ["llama2", "codellama", "phi3", "mistral", "gemma"]
        try:
            result = subprocess.run(["cmd", "/c", "ollama list"], capture_output=True, text=True, timeout=5)
            lines = result.stdout.splitlines()
            downloaded = []
            self.model_info = {}
            for line in lines[1:]:
                if not line.strip():
                    continue
                parts = line.split()
                name = parts[0]
                version = None
                if ":" in name:
                    model_name, version = name.split(":", 1)
                else:
                    model_name = name
                downloaded.append(model_name)
                self.model_info[model_name] = (True, version or "latest")
        except Exception:
            downloaded = []
            self.model_info = {}
        self.model_box.clear()
        for model in sorted(set(downloaded)):
            is_downloaded, version = self.model_info.get(model, (False, None))
            label = f"🟢 {model} ({version})" if version else f"🟢 {model}"
            self.model_box.addItem(label)
        self.model_box.insertSeparator(self.model_box.count())
        for model in available:
            if model not in downloaded:
                self.model_box.addItem(f"🔴 {model}")
                self.model_info[model] = (False, None)
        if self.model_box.count() > 0:
            self.model_box.setCurrentIndex(0)
        self.on_model_changed()

    def on_model_changed(self):
        text = self.model_box.currentText()
        import re
        m = re.match(r"[🟢🔴]?\s*([\w\-]+)(?:\s*\(([^)]+)\))?", text)
        if m:
            self.current_model = m.group(1)
            self.current_version = m.group(2) if m.group(2) else None
        else:
            self.current_model = text.strip()
            self.current_version = None
        self._update_buttons()

    def _update_buttons(self):
        is_downloaded, _ = self.model_info.get(self.current_model, (False, None))
        self.update_btn.setEnabled(is_downloaded)
        self.download_btn.setVisible(not is_downloaded)

    def append_message(self, sender, text):
        if sender == "Вы":
            html = f'<div style="margin:4px 0;"><b style="color:#7ecfff">{sender}:</b> {text}</div>'
        else:
            html = f'<div style="margin:4px 0;"><b style="color:#ffb86c">{sender}:</b> {text}</div>'
        self.history.append(html)
        self._adjust_history_height()
        self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())

    def send_message(self):
        user_text = self.input.toPlainText().strip()
        if not user_text:
            return

        self.apply_code_btn.hide()
        self.suggested_code = ""
        
        self.append_message("Вы", user_text)
        self.input.clear()
        
        prompt = user_text
        if self.include_code_checkbox.isChecked() and self.parent_window:
            current_code = self.parent_window.get_current_editor_text()
            if current_code:
                prompt = (f"Пожалуйста, ответь на мой вопрос, учитывая следующий код из моего редактора:\n\n"
                          f"```python\n{current_code}\n```\n\n"
                          f"Мой вопрос: {user_text}")

        self.append_message("Ollama", "...ожидание ответа...")
        self.input.send_btn.setEnabled(False)
        self.input.setEnabled(False)
        self.ollama_worker = OllamaWorker(prompt, self.current_model)
        self.ollama_worker.result.connect(self._on_ollama_result)
        self.ollama_worker.error.connect(self._on_ollama_error)
        self.ollama_worker.finished.connect(self._on_ollama_finished)
        self.ollama_worker.start()

    def _on_ollama_result(self, response):
        import re
        cursor = self.history.textCursor()
        cursor.movePosition(cursor.End)
        cursor.select(cursor.BlockUnderCursor)
        cursor.removeSelectedText()
        cursor.deletePreviousChar()
        self.append_message("Ollama", response)

        code_blocks = re.findall(r"```(?:python\n)?(.*?)```", response, re.DOTALL)
        if code_blocks:
            self.suggested_code = code_blocks[0].strip()
            self.apply_code_btn.show()
        else:
            self.suggested_code = ""
            self.apply_code_btn.hide()

    def _on_ollama_error(self, error_text):
        cursor = self.history.textCursor()
        cursor.movePosition(cursor.End)
        cursor.select(cursor.BlockUnderCursor)
        cursor.removeSelectedText()
        cursor.deletePreviousChar()
        self.append_message("Ошибка", error_text)

    def _on_ollama_finished(self):
        self.input.send_btn.setEnabled(True)
        self.input.setEnabled(True)

    def download_model(self):
        model = self.current_model
        if self.console:
            self.console.append_text(f"[Ollama] Начинаю загрузку модели '{model}'...")
        self.download_btn.setEnabled(False)
        self.downloader = ModelDownloader(model)
        self.downloader.progress.connect(self._on_download_progress)
        self.downloader.finished.connect(self._on_download_finished)
        self.downloader.start()

    def update_model(self):
        model = self.current_model
        version = self.current_version or "latest"
        full_name = f"{model}:{version}"
        if self.console:
            self.console.append_text(f"[Ollama] Обновление модели '{full_name}'...")
        self.update_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        self.downloader = ModelDownloader(full_name)
        self.downloader.progress.connect(self._on_download_progress)
        self.downloader.finished.connect(self._on_download_finished)
        self.downloader.start()

    def _on_download_progress(self, msg):
        if self.console:
            self.console.append_text(msg)
            
    def _on_download_finished(self, msg):
        if self.console:
            self.console.append_text(msg)
        self.download_btn.setEnabled(True)
        self.update_btn.setEnabled(True)
        self.refresh_models()

    def set_input_text_with_code(self, code):
        prompt = f"Проанализируй этот код:\n\n```python\n{code}\n```"
        self.input.setPlainText(prompt)
        self.input.setFocus()

    def apply_suggested_code(self):
        if self.suggested_code and self.parent_window:
            self.parent_window.set_current_editor_text(self.suggested_code)
            self.apply_code_btn.hide()

    def close_current_tab(self):
        index = self.tabs.currentIndex()
        if index >= 0:
            self.close_tab(index)

    def run_code(self):
        if self.process_thread and self.process_thread.isRunning():
            self.console.append_text("[Ошибка] Другой процесс уже запущен.")
            return

        tab = self.current_tab()
        if not tab:
            return
        code = tab.editor.text()
        if not code.strip():
            self.console.append_text("[Ошибка] Нет кода для запуска")
            return

        try:
            fd, path = tempfile.mkstemp(suffix=".py", text=True)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(code)
        except Exception as e:
            self.console.append_text(f"[Ошибка] Не удалось создать временный файл: {e}")
            return

        self.console.append_text(f"Запуск {tab.filename}...\n")
        
        self.process_thread = QThread()
        self.process_runner = ProcessRunner(path)
        self.process_runner.moveToThread(self.process_thread)
        
        self.process_runner.output_received.connect(self.console.append_text)
        self.process_thread.started.connect(self.process_runner.run)
        self.process_runner.finished.connect(self.on_run_finished)
        
        self.process_thread.start()

    def on_run_finished(self, return_code):
        self.console.append_text(f"\n=== Выполнение завершено (код: {return_code}) ===")
        
        self.process_thread.quit()
        self.process_thread.wait()

        try:
            os.remove(self.process_runner.path_to_script)
        except (OSError, AttributeError):
            pass 

        self.process_runner = None
        self.process_thread = None

    def set_current_editor_text(self, text):
        tab = self.current_tab()
        if tab:
            tab.editor.setText(text)

class CustomTitleBar(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        layout = QHBoxLayout()
        layout.setContentsMargins(10, 0, 0, 0)
        layout.setSpacing(0)

        self.title_label = QLabel(self.parent.windowTitle())
        self.menu_bar = QMenuBar(self)
        
        layout.addWidget(self.title_label)
        layout.addWidget(self.menu_bar)
        layout.addStretch()
        
        self.minimize_btn = self.create_btn("minimize")
        self.maximize_btn = self.create_btn("maximize")
        self.restore_btn = self.create_btn("restore")
        self.close_btn = self.create_btn("close")

        self.restore_btn.hide()
        self.close_btn.setObjectName("close_btn")

        self.minimize_btn.clicked.connect(self.parent.showMinimized)
        self.maximize_btn.clicked.connect(self.toggle_maximize_restore)
        self.restore_btn.clicked.connect(self.toggle_maximize_restore)
        self.close_btn.clicked.connect(self.parent.close)
        
        layout.addWidget(self.minimize_btn)
        layout.addWidget(self.maximize_btn)
        layout.addWidget(self.restore_btn)
        layout.addWidget(self.close_btn)
        
        self.setLayout(layout)
        
        self.start_pos = None

    def create_btn(self, name):
        btn = QPushButton(self)
        btn.setFixedSize(35, 35)
        
        icon_widget = IconWidget(name, "#e0e0e0")
        
        icon = QIcon(icon_widget.render_to_pixmap())
        btn.setIcon(icon)
        btn.setIconSize(QSize(12, 12))
        return btn

    def toggle_maximize_restore(self):
        if self.parent.isMaximized():
            self.parent.showNormal()
        else:
            self.parent.showMaximized()

    def set_title(self, title):
        self.title_label.setText(title)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start_pos = event.globalPos()

    def mouseMoveEvent(self, event):
        if self.parent.isMaximized():
            return
        if event.buttons() == Qt.LeftButton and self.start_pos:
            delta = event.globalPos() - self.start_pos
            self.parent.move(self.parent.pos() + delta)
            self.start_pos = event.globalPos()

    def mouseReleaseEvent(self, event):
        self.start_pos = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_maximize_restore()

class IconWidget(QWidget):
    SVG_PATHS = {
        "minimize": "M0 5 H10",
        "maximize": "M0 0 H10 V10 H0 Z",
        "restore": "M0 3 H7 V10 H0 Z M3 0 H10 V7 H3 Z",
        "close": "M0 0 L10 10 M10 0 L0 10"
    }
    def __init__(self, icon_name, color, parent=None):
        super().__init__(parent)
        self.path = self.SVG_PATHS.get(icon_name, "")
        self.color = color

    def render_to_pixmap(self):
        svg = f"""
        <svg width="12" height="12" viewBox="0 0 12 12" xmlns="http://www.w3.org/2000/svg">
          <path d="{self.path}" stroke="{self.color}" stroke-width="1.5" fill="none" />
        </svg>
        """.encode("utf-8")
        renderer = QSvgRenderer(svg)
        pixmap = QPixmap(12, 12)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        return pixmap

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MiniCrusor")
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(1200, 800)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self.tab_changed)
        self.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.path_label = QLabel("Путь: ")
        font = self.path_label.font()
        font.setPointSize(9)
        self.path_label.setFont(font)
        self.path_label.setStyleSheet("color: #888;")

        self.console = ConsoleWidget()
        self.console.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.chat = ChatWidget(console=self.console, parent_window=self)
        self.chat.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        editor_console_splitter = QSplitter(Qt.Vertical)
        editor_console_splitter.addWidget(self.tabs)
        spacer = QWidget()
        spacer.setFixedHeight(12)
        editor_console_splitter.addWidget(spacer)
        editor_console_splitter.addWidget(self.console)
        editor_console_splitter.setHandleWidth(2)
        editor_console_splitter.setStretchFactor(0, 4)
        editor_console_splitter.setStretchFactor(2, 1)
        editor_console_splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        editor_console_splitter.setChildrenCollapsible(False)
        editor_console_splitter.setStyleSheet("QSplitter::handle { height: 12px; }")

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(editor_console_splitter)
        main_splitter.addWidget(self.chat)
        main_splitter.setHandleWidth(2)
        main_splitter.setStretchFactor(0, 3)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        main_frame = QWidget()
        main_frame.setObjectName("main_frame")
        main_layout = QVBoxLayout(main_frame)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.title_bar = CustomTitleBar(self)

        content_container = QWidget()
        content_layout = QVBoxLayout(content_container)
        content_layout.setContentsMargins(12, 0, 12, 12)
        content_layout.setSpacing(8)
        content_layout.addWidget(self.path_label)
        content_layout.addWidget(main_splitter)
        
        main_layout.addWidget(self.title_bar)
        main_layout.addWidget(content_container)
        
        self.setCentralWidget(main_frame)

        self.create_menu(self.title_bar.menu_bar)
        self.open_new_tab()
        
        self.process_runner = None
        self.process_thread = None

        self.windowTitleChanged.connect(self.title_bar.set_title)

    def changeEvent(self, event):
        if event.type() == QEvent.WindowStateChange:
            is_maximized = self.isMaximized()
            self.centralWidget().setProperty("maximized", is_maximized)
            self.title_bar.setProperty("maximized", is_maximized)

            self.centralWidget().style().unpolish(self.centralWidget())
            self.centralWidget().style().polish(self.centralWidget())
            self.title_bar.style().unpolish(self.title_bar)
            self.title_bar.style().polish(self.title_bar)

            if self.isMaximized():
                self.title_bar.maximize_btn.hide()
                self.title_bar.restore_btn.show()
            else:
                self.title_bar.maximize_btn.show()
                self.title_bar.restore_btn.hide()
        super().changeEvent(event)

    def create_menu(self, menu):
        file_menu = menu.addMenu("Файл")
        open_action = QAction("Открыть...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_file_dialog)
        file_menu.addAction(open_action)

        save_action = QAction("Сохранить", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_current_file)
        file_menu.addAction(save_action)

        save_as_action = QAction("Сохранить как...", self)
        save_as_action.triggered.connect(self.save_current_file_as)
        file_menu.addAction(save_as_action)

        close_action = QAction("Закрыть вкладку", self)
        close_action.setShortcut("Ctrl+W")
        close_action.triggered.connect(self.close_current_tab)
        file_menu.addAction(close_action)

        run_action = QAction("Запустить", self)
        run_action.setShortcut("F5")
        run_action.triggered.connect(self.run_code)
        menu.addAction(run_action)

    def open_new_tab(self, filepath=None):
        tab = EditorTab(filepath)
        tab.code_for_ai.connect(self.handle_code_for_ai)
        self.tabs.addTab(tab, tab.filename)
        self.tabs.setCurrentWidget(tab)
        self.update_path_display()
        self.update_tab_title(tab)
        tab.editor.textChanged.connect(tab.editor.update_autocomplete)

    def close_tab(self, index):
        tab = self.tabs.widget(index)
        if tab.editor.isModified():
            ret = QMessageBox.question(self, "Сохранение", f"Файл '{tab.filename}' изменён. Сохранить перед закрытием?",
                                       QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if ret == QMessageBox.Yes:
                if not tab.save_file():
                    return
            elif ret == QMessageBox.Cancel:
                return
        self.tabs.removeTab(index)
        if self.tabs.count() == 0:
            self.open_new_tab()

    def tab_changed(self, index):
        self.update_path_display()

    def update_tab_title(self, tab):
        index = self.tabs.indexOf(tab)
        if index == -1:
            return
        title = tab.filename
        if tab.editor.isModified():
            title = "*" + title
        self.tabs.setTabText(index, title)
        self.setWindowTitle(f"{tab.filename} - MiniCrusor")

    def update_path_display(self):
        tab = self.current_tab()
        if not tab or not tab.filepath:
            self.path_label.setText("Путь: [Без имени]")
            return

        parts = tab.filepath.split(os.sep)
        if os.name == 'nt' and len(parts) > 0 and parts[0].endswith(':'):
            parts[0] = parts[0] + ' '

        max_parts = 6
        if len(parts) > max_parts:
            parts = parts[:3] + ["..."] + parts[-2:]

        display_path = " > ".join(parts)
        self.path_label.setText(f"Путь: {display_path}")

    def current_tab(self):
        return self.tabs.currentWidget()

    def get_current_editor_text(self):
        tab = self.current_tab()
        if tab:
            return tab.editor.text()
        return ""

    def set_current_editor_text(self, text):
        tab = self.current_tab()
        if tab:
            tab.editor.setText(text)

    def handle_code_for_ai(self, code):
        self.chat.set_input_text_with_code(code)

    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Открыть файл", "", "Python Files (*.py);;Все файлы (*)")
        if path:
            self.open_new_tab(path)

    def save_current_file(self):
        tab = self.current_tab()
        if tab:
            if not tab.filepath:
                self.save_current_file_as()
            else:
                tab.save_file()
                self.update_tab_title(tab)

    def save_current_file_as(self):
        tab = self.current_tab()
        if tab:
            path, _ = QFileDialog.getSaveFileName(self, "Сохранить файл как", tab.filename, "Python Files (*.py);;Все файлы (*)")
            if path:
                if tab.save_file(path):
                    self.update_tab_title(tab)
                    self.update_path_display()

    def close_current_tab(self):
        index = self.tabs.currentIndex()
        if index >= 0:
            self.close_tab(index)

    def run_code(self):
        if self.process_thread and self.process_thread.isRunning():
            self.console.append_text("[Ошибка] Другой процесс уже запущен.")
            return

        tab = self.current_tab()
        if not tab:
            return
        code = tab.editor.text()
        if not code.strip():
            self.console.append_text("[Ошибка] Нет кода для запуска")
            return

        try:
            fd, path = tempfile.mkstemp(suffix=".py", text=True)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(code)
        except Exception as e:
            self.console.append_text(f"[Ошибка] Не удалось создать временный файл: {e}")
            return

        self.console.append_text(f"Запуск {tab.filename}...\n")
        
        self.process_thread = QThread()
        self.process_runner = ProcessRunner(path)
        self.process_runner.moveToThread(self.process_thread)
        
        self.process_runner.output_received.connect(self.console.append_text)
        self.process_thread.started.connect(self.process_runner.run)
        self.process_runner.finished.connect(self.on_run_finished)
        
        self.process_thread.start()

    def on_run_finished(self, return_code):
        self.console.append_text(f"\n=== Выполнение завершено (код: {return_code}) ===")
        
        self.process_thread.quit()
        self.process_thread.wait()

        try:
            os.remove(self.process_runner.path_to_script)
        except (OSError, AttributeError):
            pass 

        self.process_runner = None
        self.process_thread = None

def main():
    app = QApplication(sys.argv)
    app.setStyleSheet("""
    QWidget {
        font-family: 'Segoe UI', 'Arial', sans-serif;
        font-size: 12pt;
        background: transparent;
        color: #e0e0e0;
    }
    #main_frame {
        background-color: #1e2124;
        border-radius: 10px;
    }
    #main_frame[maximized="true"] {
        border-radius: 0px;
    }
    QTabWidget::pane {
        border: none;
        top: 5px;
        margin-bottom: 0px;
    }
    QTabBar::tab {
        background: #232629;
        border-radius: 8px 8px 0 0;
        padding: 4px 10px;
        margin-right: 2px;
        margin-top: 0px;
        color: #e0e0e0;
        border: 1px solid transparent;
        border-width: 1px;
    }
    QTabBar::tab:selected {
        background: #282c34;
        color: #fff;
        border: 1px solid #3a3f44;
        border-bottom: 2px solid #282c34;
        border-radius: 8px 8px 0 0;
    }
    QTabBar::close-button {
        border-radius: 8px;
        min-width: 16px;
        min-height: 16px;
        margin: 2px 4px;
    }
    QTabBar::close-button:hover {
        background: #e06c75;
    }
    QsciScintilla {
        background: #282c34;
        border-radius: 14px;
        border: 1px solid #393e46;
        padding: 18px 12px 12px 12px;
    }
    QPlainTextEdit, QTextEdit {
        background: #282c34;
        border-radius: 14px;
        border: 1px solid #393e46;
        padding: 12px;
    }
    QLineEdit {
        background: #2d2f31;
        border-radius: 10px;
        border: 1px solid #444;
        padding: 8px;
    }
    QPushButton {
        background: #3a3f44;
        border-radius: 6px;
        padding: 6px 16px;
        color: #fff;
        border: 1px solid #444;
    }
    QPushButton:hover {
        background: #50555a;
    }
    QComboBox {
        background: #2d2f31;
        border-radius: 6px;
        padding: 4px 8px;
        color: #fff;
        border: 1px solid #444;
    }
    QComboBox QAbstractItemView {
        background: #232629;
        color: #e0e0e0;
        border-radius: 6px;
    }
    QLabel {
        color: #b0b0b0;
    }
    QSplitter::handle {
        background: transparent;
        border: none;
        min-width: 1px;
        max-width: 1px;
    }
    QScrollBar:vertical, QScrollBar:horizontal {
        background: transparent;
        border: none;
        width: 12px;
        margin: 0px;
        border-radius: 6px;
    }
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
        background: #50555a;
        min-height: 20px;
        border-radius: 6px;
    }
    QScrollBar::add-line, QScrollBar::sub-line {
        background: none;
    }
    QCheckBox {
        color: #b0b0b0;
        spacing: 5px;
    }
    QCheckBox::indicator {
        width: 14px;
        height: 14px;
        border: 1px solid #555;
        border-radius: 4px;
        background: #2d2f31;
    }
    QCheckBox::indicator:checked {
        background: #7ecfff;
        border-color: #7ecfff;
    }
    QMenuBar {
        background-color: transparent;
        color: #e0e0e0;
        border: none;
        padding-left: 5px;
    }
    QMenuBar::item {
        background-color: transparent;
        padding: 4px 10px;
    }
    QMenuBar::item:selected {
        background-color: #3a3f44;
        color: #fff;
        border-radius: 4px;
    }
    QMenu {
        background-color: #282c34;
        color: #e0e0e0;
        border: 1px solid #393e46;
        padding: 4px;
    }
    QMenu::item {
        padding: 4px 20px;
        border-radius: 4px;
    }
    QMenu::item:selected {
        background-color: #3a3f44;
    }
    CustomTitleBar {
        background-color: #282c34;
        border-bottom: 1px solid #3a3f44;
        height: 35px;
        border-top-left-radius: 10px;
        border-top-right-radius: 10px;
    }
    CustomTitleBar[maximized="true"] {
        border-top-left-radius: 0px;
        border-top-right-radius: 0px;
    }
    CustomTitleBar QLabel {
        color: #b0b0b0;
        padding-left: 5px;
        font-size: 11pt;
    }
    CustomTitleBar QPushButton {
        background-color: transparent;
        border: none;
        font-size: 16pt;
        color: #e0e0e0;
    }
    CustomTitleBar QPushButton:hover {
        background-color: #3a3f44;
    }
    CustomTitleBar QPushButton#close_btn:hover {
        background-color: #e06c75;
        color: #fff;
    }
    """)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()