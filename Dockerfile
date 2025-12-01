FROM python:3.11-slim

WORKDIR /app

# تثبيت مكتبات النظام
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# نسخ ملفات المشروع
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# إنشاء المجلدات المطلوبة
RUN mkdir -p ads profile_photos group_replies

# تعيين متغيرات البيئة
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# تشغيل البوت
CMD ["python", "bot.py"]
