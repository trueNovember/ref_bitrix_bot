# 1. Берем официальный, легкий образ Python
FROM python:3.13-slim

# 2. Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# 3. Копируем только файл с зависимостями
COPY requirements.txt .

# 4. Устанавливаем зависимости
# --no-cache-dir экономит место
RUN pip install --no-cache-dir -r requirements.txt

# 5. Копируем ВЕСЬ остальной код проекта в /app
COPY . .

# 6. Создаем папку для нашей базы данных
RUN mkdir /app/data

# 7. Указываем команду, которая запустит бота
CMD ["python", "bot.py"]