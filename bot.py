#!/usr/bin/env python3
"""
بوت تلجرام متكامل - معدل للعمل على Render.com
الإصدار: 1.0
"""

import os
import json
import asyncio
import logging
import sqlite3
import random
import string
from datetime import datetime, timedelta
from threading import Thread
from queue import Queue
import signal
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# ===== إعدادات مهمة للعمل على Render =====
# على Render، نحتاج لفتح port للتحقق من صحة الخدمة
RENDER_PORT = int(os.environ.get('PORT', 10000))

# إضافة dotenv للعمل محلياً (اختياري)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    InputFile
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.errors import SessionPasswordNeededError

# ===== تكوين البوت =====
# قراءة التوكن من متغير البيئة
BOT_TOKEN = os.environ.get('BOT_TOKEN')

if not BOT_TOKEN:
    print("=" * 60)
    print("❌ خطأ: BOT_TOKEN غير موجود!")
    print("=" * 60)
    print("🔧 **لإصلاح المشكلة على Render.com:**")
    print("1. اذهب إلى dashboard.render.com")
    print("2. اختر خدمتك")
    print("3. اضغط على Settings → Environment")
    print("4. أضف متغير جديد:")
    print("   - Key: BOT_TOKEN")
    print("   - Value: 8500469877:AAGCNojz50p2U2RJrQ85TEGuuR4b-S7XaLo")
    print("5. اضغط Save Changes")
    print("6. أعد تشغيل الخدمة")
    print("=" * 60)
    sys.exit(1)

print(f"✅ تم تحميل توكن البوت بنجاح")
print(f"🌐 Port المخصص: {RENDER_PORT}")

# ===== إعدادات قاعدة البيانات =====
DB_NAME = "bot_database.db"

# ===== حالات المحادثة =====
(
    ADD_ACCOUNT, ADD_AD_TYPE, ADD_AD_TEXT, ADD_AD_MEDIA, ADD_GROUP, 
    ADD_PRIVATE_REPLY, ADD_GROUP_REPLY, ADD_ADMIN, 
    ADD_USERNAME, ADD_RANDOM_REPLY, ADD_PRIVATE_TEXT, ADD_GROUP_TEXT, 
    ADD_GROUP_PHOTO
) = range(13)

# ===== تهيئة السجل =====
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),  # إرسال logs إلى Render
        logging.FileHandler('bot.log')      # حفظ logs في ملف
    ]
)
logger = logging.getLogger(__name__)

# ===== فئات البوت =====

class BotDatabase:
    def __init__(self):
        self.init_database()
    
    def init_database(self):
        """تهيئة قاعدة البيانات"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # جدول الحسابات
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_string TEXT UNIQUE,
                phone TEXT,
                name TEXT,
                username TEXT,
                is_active BOOLEAN DEFAULT 1,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                admin_id INTEGER DEFAULT 0
            )
        ''')
        
        # جدول الإعلانات
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT,
                text TEXT,
                media_path TEXT,
                file_type TEXT,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                admin_id INTEGER DEFAULT 0
            )
        ''')
        
        # جدول المجموعات
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link TEXT,
                status TEXT DEFAULT 'pending',
                join_date DATETIME,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                admin_id INTEGER DEFAULT 0
            )
        ''')
        
        # جدول المشرفين
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                username TEXT,
                full_name TEXT,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_super_admin BOOLEAN DEFAULT 0
            )
        ''')
        
        # جدول الردود الخاصة
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS private_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reply_text TEXT,
                is_active BOOLEAN DEFAULT 1,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                admin_id INTEGER DEFAULT 0
            )
        ''')
        
        # جدول الردود الجماعية النصية
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS group_text_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger TEXT,
                reply_text TEXT,
                is_active BOOLEAN DEFAULT 1,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                admin_id INTEGER DEFAULT 0
            )
        ''')
        
        # جدول الردود الجماعية مع الصور
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS group_photo_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger TEXT,
                reply_text TEXT,
                media_path TEXT,
                is_active BOOLEAN DEFAULT 1,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                admin_id INTEGER DEFAULT 0
            )
        ''')
        
        # جدول الردود العشوائية في القروبات
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS group_random_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reply_text TEXT,
                is_active BOOLEAN DEFAULT 1,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                admin_id INTEGER DEFAULT 0
            )
        ''')
        
        # جدول نشر الحسابات
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS account_publishing (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER,
                status TEXT DEFAULT 'active',
                last_publish DATETIME,
                FOREIGN KEY (account_id) REFERENCES accounts (id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    # باقي دوال الفئة تبقى كما هي...
    def add_account(self, session_string, phone, name, username, admin_id=0):
        """إضافة حساب جديد"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO accounts (session_string, phone, name, username, admin_id)
                VALUES (?, ?, ?, ?, ?)
            ''', (session_string, phone, name, username, admin_id))
            account_id = cursor.lastrowid
            
            cursor.execute('''
                INSERT INTO account_publishing (account_id)
                VALUES (?)
            ''', (account_id,))
            
            conn.commit()
            return True, "تم إضافة الحساب بنجاح"
        except sqlite3.IntegrityError:
            return False, "هذا الحساب مضاف مسبقاً"
        except Exception as e:
            return False, f"خطأ في إضافة الحساب: {str(e)}"
        finally:
            conn.close()
    
    # ... (جميع الدوال الأخرى تبقى كما هي بدون تغيير) ...
    # لقد حذفتها للإيجاز، لكنها موجودة في الكود الأصلي
    
    def get_active_publishing_accounts(self, admin_id=None):
        """الحصول على الحسابات النشطة للنشر"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id is not None:
            cursor.execute('''
                SELECT a.id, a.session_string, a.name, a.username
                FROM accounts a
                JOIN account_publishing ap ON a.id = ap.account_id
                WHERE ap.status = 'active' AND a.is_active = 1 
                AND (a.admin_id = ? OR a.admin_id = 0)
            ''', (admin_id,))
        else:
            cursor.execute('''
                SELECT a.id, a.session_string, a.name, a.username
                FROM accounts a
                JOIN account_publishing ap ON a.id = ap.account_id
                WHERE ap.status = 'active' AND a.is_active = 1
            ''')
            
        accounts = cursor.fetchall()
        conn.close()
        return accounts

class TelegramBotManager:
    def __init__(self, db):
        self.db = db
        self.publishing_active = False
        self.publishing_thread = None
        self.private_reply_active = False
        self.private_reply_thread = None
        self.group_reply_active = False
        self.group_reply_thread = None
        self.random_reply_active = False
        self.random_reply_thread = None
    
    async def test_session(self, session_string):
        """اختبار جلسة تيليجرام"""
        try:
            client = TelegramClient(StringSession(session_string), 1, "b")
            await client.connect()
            
            if await client.is_user_authorized():
                me = await client.get_me()
                await client.disconnect()
                return True, me
            else:
                await client.disconnect()
                return False, None
        except Exception as e:
            logger.error(f"خطأ في اختبار الجلسة: {str(e)}")
            return False, None
    
    # ... (جميع الدوال الأخرى تبقى كما هي) ...

class BotHandler:
    def __init__(self):
        self.db = BotDatabase()
        self.manager = TelegramBotManager(self.db)
        self.application = None
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بدء البوت"""
        user = update.effective_user
        user_id = user.id
        
        if not self.db.is_admin(user_id):
            await update.message.reply_text("❌ ليس لديك صلاحية للوصول إلى هذا البوت.")
            return
        
        if context.user_data.get('conversation_active'):
            context.user_data['conversation_active'] = False
        
        # ترتيب جديد للوحة التحكم
        keyboard = [
            [InlineKeyboardButton("👥 إدارة الحسابات", callback_data="manage_accounts")],
            [InlineKeyboardButton("📢 إدارة الإعلانات", callback_data="manage_ads")],
            [InlineKeyboardButton("👥 إدارة المجموعات", callback_data="manage_groups")],
            [InlineKeyboardButton("💬 إدارة الردود", callback_data="manage_replies")],
            [InlineKeyboardButton("👨‍💼 إدارة المشرفين", callback_data="manage_admins")],
            [InlineKeyboardButton("⚙️ الإعدادات", callback_data="settings")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🎮 **لوحة تحكم البوت المتكامل**\n\n"
            "اختر القسم الذي تريد إدارته:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    # ... (جميع الدوال الأخرى تبقى كما هي) ...

    def setup_handlers(self):
        """إعداد معالجات البوت"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("cancel", self.cancel))
        
        # ... (جميع handlers الأخرى تبقى كما هي) ...
        
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
    
    def run(self):
        """تشغيل البوت"""
        self.application = Application.builder().token(BOT_TOKEN).build()
        self.setup_handlers()
        
        # إضافة المشرف الرئيسي تلقائياً عند التشغيل الأول
        try:
            self.db.add_admin(8390377822, "@user", "المشرف الرئيسي", True)
            logger.info("✅ تم إضافة الآيدي 8390377822 كمشرف رئيسي")
        except:
            logger.info("⚠️ المشرف الرئيسي مضاف مسبقاً")
        
        # إنشاء المجلدات المطلوبة
        os.makedirs("ads", exist_ok=True)
        os.makedirs("profile_photos", exist_ok=True)
        os.makedirs("group_replies", exist_ok=True)
        
        logger.info("🤖 بدء تشغيل بوت تلجرام...")
        self.application.run_polling()

# ===== Health Check Server للعمل على Render =====
class HealthHandler(BaseHTTPRequestHandler):
    """معالج للتحقق من صحة الخدمة"""
    def do_GET(self):
        if self.path in ['/', '/health', '/status']:
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<h1>🤖 Telegram Bot is Running!</h1>')
            self.wfile.write(b'<p>✅ Service is healthy and ready</p>')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        """تعطيل logging الافتراضي"""
        pass

def run_health_server(port=10000):
    """تشغيل خادم للتحقق من الصحة"""
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    print(f"✅ Health check server running on port {port}")
    print(f"🌐 Access at: http://0.0.0.0:{port}/health")
    server.serve_forever()

def run_telegram_bot():
    """تشغيل بوت تلجرام"""
    try:
        bot = BotHandler()
        bot.run()
    except Exception as e:
        print(f"❌ Error running Telegram bot: {e}")
        import traceback
        traceback.print_exc()

# ===== الدالة الرئيسية =====
def main():
    """الدالة الرئيسية لتشغيل كل شيء"""
    print("=" * 60)
    print("🚀 Starting Telegram Bot System")
    print("=" * 60)
    
    # إنشاء المجلدات المطلوبة
    os.makedirs("ads", exist_ok=True)
    os.makedirs("profile_photos", exist_ok=True)
    os.makedirs("group_replies", exist_ok=True)
    
    # بدء خادم Health Check في thread منفصل
    health_thread = threading.Thread(
        target=run_health_server,
        args=(RENDER_PORT,),
        daemon=True
    )
    health_thread.start()
    
    print(f"✅ Health server started on port {RENDER_PORT}")
    
    # بدء بوت تلجرام
    print("🤖 Starting Telegram Bot...")
    
    # إعداد signal handlers للإغلاق النظيف
    def signal_handler(signum, frame):
        print(f"\n🛑 Received signal {signum}, shutting down...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # تشغيل البوت (سيحجب هذا thread)
    run_telegram_bot()

if __name__ == "__main__":
    main()
