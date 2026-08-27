import os

file_path = r'd:\TiTech Prabha Solution\Kravein\Kravein\Kravein-AI---Lite-main\Kravein-AI---Lite-main\ui.py'
with open(file_path, 'r', encoding='utf-8') as f:
    text = f.read()

text = text.replace('lay.addWidget(self._header("KRAVEIN", "First Time Setup\nConnect your preferred AI provider to begin."))', 'lay.addWidget(self._header("KRAVEIN", "First Time Setup\\nConnect your preferred AI provider to begin."))')
text = text.replace('info = QLabel("Estimated time\n20 seconds")', 'info = QLabel("Estimated time\\n20 seconds")')

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(text)
print('Fixed syntax errors')
