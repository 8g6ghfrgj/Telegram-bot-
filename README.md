# Telegram Bot - متعدد المهام

بوت تيليجرام متكامل لإدارة الحسابات والنشر التلقائي في المجموعات.

## المميزات:
- إدارة حسابات متعددة
- نشر تلقائي في المجموعات
- ردود تلقائية في الخاص والمجموعات
- إدارة مشرفين متعددة

## التنصيب على Render.com

1. انسخ هذا المستودع
2. سجل في Render.com
3. أنشئ Web Service جديد
4. أضف متغير البيئة BOT_TOKEN
5. شغّل البوت

## المتغيرات البيئية:
- `BOT_TOKEN`: توكن بوت التلجرام

## الأوامر:
- `/start`: بدء البوت
## ⚡ التثبيت السريع

### 1. النشر على Render.com (مستحسن)
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

### 2. التثبيت محلياً
```bash
# استنساخ المشروع
git clone https://github.com/اسم-المستخدم/telegram-bot.git
cd telegram-bot

# تثبيت المتطلبات
pip install -r requirements.txt

# إعداد ملف البيئة
cp .env.example .env
# تعديل .env وإضافة BOT_TOKEN

# تشغيل البوت
python bot.py
