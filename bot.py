import os
import json
import asyncio
import logging
import sqlite3
import random
import string
import threading
import re
import base64
from datetime import datetime, timedelta
from threading import Thread
from queue import Queue
from http.server import HTTPServer, BaseHTTPRequestHandler

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

from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, GetDialogsRequest
from telethon.tl.types import InputPeerEmpty, ChatEmpty
from telethon.errors import SessionPasswordNeededError, FloodWaitError

# خادم HTTP بسيط لمشكلة Port
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot is running!')
    
    def log_message(self, *args):
        pass

def run_health_server():
    """تشغيل خادم HTTP للتحقق من الصحة"""
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"✅ Health server running on port {port}")
    server.serve_forever()

# تكوين البوت - قراءة التوكن من متغير البيئة
BOT_TOKEN = os.environ.get('BOT_TOKEN')

# التحقق من وجود التوكن
if not BOT_TOKEN:
    print("❌ خطأ: لم يتم تعيين BOT_TOKEN في متغيرات البيئة")
    print("⚠️  يرجى إضافة BOT_TOKEN في Render.com → Environment")
    exit(1)

# إعدادات قاعدة البيانات
DB_NAME = "bot_database.db"

# حالات المحادثة
(
    ADD_ACCOUNT, ADD_AD_TYPE, ADD_AD_TEXT, ADD_AD_MEDIA, ADD_GROUP, 
    ADD_PRIVATE_REPLY, ADD_GROUP_REPLY, ADD_ADMIN, 
    ADD_USERNAME, ADD_RANDOM_REPLY, ADD_PRIVATE_TEXT, ADD_GROUP_TEXT, 
    ADD_GROUP_PHOTO, DELETE_REPLY
) = range(14)

# تهيئة السجل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class TextEncoder:
    """فئة لتشفير النصوص"""
    
    @staticmethod
    def encode_text(text):
        """تشفير النص باستخدام تقنيات متعددة"""
        try:
            # 1. Base64 Encoding
            encoded = base64.b64encode(text.encode()).decode()
            
            # 2. Reverse text
            reversed_text = text[::-1]
            
            # 3. XOR encoding with random key
            key = random.randint(1, 255)
            xor_encoded = ''.join(chr(ord(c) ^ key) for c in text)
            
            # 4. Combine multiple encodings
            combined = f"B64:{encoded}|REV:{reversed_text}|XOR:{xor_encoded}|KEY:{key}"
            
            # 5. Additional Base64
            final_encoded = base64.b64encode(combined.encode()).decode()
            
            return final_encoded
            
        except Exception as e:
            logger.error(f"خطأ في تشفير النص: {str(e)}")
            return text
    
    @staticmethod
    def decode_text(encoded_text):
        """فك تشفير النص"""
        try:
            # Decode from Base64
            decoded = base64.b64decode(encoded_text.encode()).decode()
            
            # Extract parts
            parts = {}
            for part in decoded.split('|'):
                if ':' in part:
                    key, value = part.split(':', 1)
                    parts[key] = value
            
            # Get original text from XOR
            if 'XOR' in parts and 'KEY' in parts:
                key = int(parts['KEY'])
                xor_decoded = ''.join(chr(ord(c) ^ key) for c in parts['XOR'])
                return xor_decoded
            
            # Fallback to base64
            if 'B64' in parts:
                return base64.b64decode(parts['B64']).decode()
                
            return decoded
        except:
            return encoded_text

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
                admin_id INTEGER DEFAULT 0,
                is_encoded BOOLEAN DEFAULT 1
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
                admin_id INTEGER DEFAULT 0,
                is_encoded BOOLEAN DEFAULT 1
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
                admin_id INTEGER DEFAULT 0,
                is_encoded BOOLEAN DEFAULT 1
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
                admin_id INTEGER DEFAULT 0,
                is_encoded BOOLEAN DEFAULT 1
            )
        ''')
        
        # جدول الردود العشوائية في القروبات
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS group_random_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reply_text TEXT,
                media_path TEXT,
                is_active BOOLEAN DEFAULT 1,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                admin_id INTEGER DEFAULT 0,
                is_encoded BOOLEAN DEFAULT 1,
                has_media BOOLEAN DEFAULT 0
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
        
        # جدول المجموعات المجمعة
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bulk_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER,
                link TEXT,
                name TEXT,
                status TEXT DEFAULT 'pending',
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                admin_id INTEGER DEFAULT 0
            )
        ''')
        
        conn.commit()
        conn.close()
    
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
    
    def get_accounts(self, admin_id=None):
        """الحصول على جميع الحسابات"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id is not None:
            cursor.execute('''
                SELECT id, session_string, phone, name, username, is_active 
                FROM accounts 
                WHERE admin_id = ? OR admin_id = 0
                ORDER BY id
            ''', (admin_id,))
        else:
            cursor.execute('''
                SELECT id, session_string, phone, name, username, is_active 
                FROM accounts 
                ORDER BY id
            ''')
            
        accounts = cursor.fetchall()
        conn.close()
        return accounts
    
    def delete_account(self, account_id, admin_id=None):
        """حذف حساب"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id:
            cursor.execute('DELETE FROM accounts WHERE id = ? AND (admin_id = ? OR admin_id = 0)', (account_id, admin_id))
        else:
            cursor.execute('DELETE FROM accounts WHERE id = ?', (account_id,))
            
        cursor.execute('DELETE FROM account_publishing WHERE account_id = ?', (account_id,))
        
        conn.commit()
        conn.close()
        return True
    
    def add_ad(self, ad_type, text=None, media_path=None, file_type=None, admin_id=0):
        """إضافة إعلان"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        try:
            # تشفير النص إذا كان موجوداً
            encoded_text = TextEncoder.encode_text(text) if text else None
            
            cursor.execute('''
                INSERT INTO ads (type, text, media_path, file_type, admin_id, is_encoded)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (ad_type, encoded_text, media_path, file_type, admin_id, 1 if text else 0))
            
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"خطأ في إضافة الإعلان: {str(e)}")
            return False
        finally:
            conn.close()
    
    def get_ads(self, admin_id=None, decode=True):
        """الحصول على جميع الإعلانات"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id is not None:
            cursor.execute('SELECT * FROM ads WHERE admin_id = ? OR admin_id = 0 ORDER BY id', (admin_id,))
        else:
            cursor.execute('SELECT * FROM ads ORDER BY id')
            
        ads = cursor.fetchall()
        conn.close()
        
        # فك تشفير النصوص إذا طُلب
        if decode:
            decoded_ads = []
            for ad in ads:
                ad_list = list(ad)
                if ad_list[2] and ad_list[6]:  # النص وكان مشفراً
                    try:
                        ad_list[2] = TextEncoder.decode_text(ad_list[2])
                    except:
                        pass
                decoded_ads.append(tuple(ad_list))
            return decoded_ads
        return ads
    
    def delete_ad(self, ad_id, admin_id=None):
        """حذف إعلان"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id:
            cursor.execute('DELETE FROM ads WHERE id = ? AND (admin_id = ? OR admin_id = 0)', (ad_id, admin_id))
        else:
            cursor.execute('DELETE FROM ads WHERE id = ?', (ad_id,))
            
        conn.commit()
        conn.close()
        return True
    
    def add_group(self, link, admin_id=0):
        """إضافة مجموعة"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO groups (link, admin_id)
            VALUES (?, ?)
        ''', (link, admin_id))
        
        conn.commit()
        conn.close()
        return True
    
    def add_bulk_groups(self, groups_data, admin_id=0):
        """إضافة مجموعات مجمعة"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        for link, name in groups_data:
            cursor.execute('''
                INSERT INTO bulk_groups (link, name, admin_id)
                VALUES (?, ?, ?)
            ''', (link, name, admin_id))
        
        conn.commit()
        conn.close()
        return True
    
    def get_bulk_groups(self, admin_id=None):
        """الحصول على المجموعات المجمعة"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id is not None:
            cursor.execute('SELECT * FROM bulk_groups WHERE admin_id = ? OR admin_id = 0 ORDER BY id', (admin_id,))
        else:
            cursor.execute('SELECT * FROM bulk_groups ORDER BY id')
            
        groups = cursor.fetchall()
        conn.close()
        return groups
    
    def update_bulk_group_status(self, group_id, status):
        """تحديث حالة المجموعة المجمعة"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE bulk_groups 
            SET status = ?
            WHERE id = ?
        ''', (status, group_id))
        
        conn.commit()
        conn.close()
        return True
    
    def get_groups(self, admin_id=None):
        """الحصول على جميع المجموعات"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id is not None:
            cursor.execute('SELECT * FROM groups WHERE admin_id = ? OR admin_id = 0 ORDER BY id', (admin_id,))
        else:
            cursor.execute('SELECT * FROM groups ORDER BY id')
            
        groups = cursor.fetchall()
        conn.close()
        return groups
    
    def update_group_status(self, group_id, status):
        """تحديث حالة المجموعة"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE groups 
            SET status = ?, join_date = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (status, group_id))
        
        conn.commit()
        conn.close()
        return True
    
    def add_admin(self, user_id, username, full_name, is_super_admin=False):
        """إضافة مشرف"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO admins (user_id, username, full_name, is_super_admin)
                VALUES (?, ?, ?, ?)
            ''', (user_id, username, full_name, is_super_admin))
            conn.commit()
            return True, "تم إضافة المشرف بنجاح"
        except sqlite3.IntegrityError:
            return False, "هذا المشرف مضاف مسبقاً"
        finally:
            conn.close()
    
    def get_admins(self):
        """الحصول على جميع المشرفين"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM admins ORDER BY id')
        admins = cursor.fetchall()
        conn.close()
        return admins
    
    def delete_admin(self, admin_id):
        """حذف مشرف"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM admins WHERE id = ?', (admin_id,))
        conn.commit()
        conn.close()
        return True
    
    def is_admin(self, user_id):
        """التحقق إذا كان المستخدم مشرف"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('SELECT id FROM admins WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    
    def is_super_admin(self, user_id):
        """التحقق إذا كان المستخدم مشرف رئيسي"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('SELECT id FROM admins WHERE user_id = ? AND is_super_admin = 1', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    
    def add_private_reply(self, reply_text, admin_id=0):
        """إضافة رد خاص"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        encoded_text = TextEncoder.encode_text(reply_text)
        
        cursor.execute('''
            INSERT INTO private_replies (reply_text, admin_id, is_encoded)
            VALUES (?, ?, ?)
        ''', (encoded_text, admin_id, 1))
        
        conn.commit()
        conn.close()
        return True
    
    def get_private_replies(self, admin_id=None, decode=True):
        """الحصول على الردود الخاصة"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id is not None:
            cursor.execute('SELECT * FROM private_replies WHERE admin_id = ? OR admin_id = 0 ORDER BY id', (admin_id,))
        else:
            cursor.execute('SELECT * FROM private_replies ORDER BY id')
            
        replies = cursor.fetchall()
        conn.close()
        
        # فك تشفير النصوص
        if decode:
            decoded_replies = []
            for reply in replies:
                reply_list = list(reply)
                if reply_list[1] and reply_list[5]:  # النص وكان مشفراً
                    try:
                        reply_list[1] = TextEncoder.decode_text(reply_list[1])
                    except:
                        pass
                decoded_replies.append(tuple(reply_list))
            return decoded_replies
        return replies
    
    def delete_private_reply(self, reply_id, admin_id=None):
        """حذف رد خاص"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id:
            cursor.execute('DELETE FROM private_replies WHERE id = ? AND (admin_id = ? OR admin_id = 0)', (reply_id, admin_id))
        else:
            cursor.execute('DELETE FROM private_replies WHERE id = ?', (reply_id,))
            
        conn.commit()
        conn.close()
        return True
    
    def add_group_text_reply(self, trigger, reply_text, admin_id=0):
        """إضافة رد نصي جماعي"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        encoded_reply = TextEncoder.encode_text(reply_text)
        
        cursor.execute('''
            INSERT INTO group_text_replies (trigger, reply_text, admin_id, is_encoded)
            VALUES (?, ?, ?, ?)
        ''', (trigger, encoded_reply, admin_id, 1))
        
        conn.commit()
        conn.close()
        return True
    
    def get_group_text_replies(self, admin_id=None, decode=True):
        """الحصول على الردود النصية الجماعية"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id is not None:
            cursor.execute('SELECT * FROM group_text_replies WHERE admin_id = ? OR admin_id = 0 ORDER BY id', (admin_id,))
        else:
            cursor.execute('SELECT * FROM group_text_replies ORDER BY id')
            
        replies = cursor.fetchall()
        conn.close()
        
        # فك تشفير النصوص
        if decode:
            decoded_replies = []
            for reply in replies:
                reply_list = list(reply)
                if reply_list[2] and reply_list[6]:  # النص وكان مشفراً
                    try:
                        reply_list[2] = TextEncoder.decode_text(reply_list[2])
                    except:
                        pass
                decoded_replies.append(tuple(reply_list))
            return decoded_replies
        return replies
    
    def delete_group_text_reply(self, reply_id, admin_id=None):
        """حذف رد نصي جماعي"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id:
            cursor.execute('DELETE FROM group_text_replies WHERE id = ? AND (admin_id = ? OR admin_id = 0)', (reply_id, admin_id))
        else:
            cursor.execute('DELETE FROM group_text_replies WHERE id = ?', (reply_id,))
            
        conn.commit()
        conn.close()
        return True
    
    def add_group_photo_reply(self, trigger, reply_text, media_path, admin_id=0):
        """إضافة رد جماعي مع صورة"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        encoded_reply = TextEncoder.encode_text(reply_text) if reply_text else None
        
        cursor.execute('''
            INSERT INTO group_photo_replies (trigger, reply_text, media_path, admin_id, is_encoded)
            VALUES (?, ?, ?, ?, ?)
        ''', (trigger, encoded_reply, media_path, admin_id, 1 if reply_text else 0))
        
        conn.commit()
        conn.close()
        return True
    
    def get_group_photo_replies(self, admin_id=None, decode=True):
        """الحصول على الردود الجماعية مع الصور"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id is not None:
            cursor.execute('SELECT * FROM group_photo_replies WHERE admin_id = ? OR admin_id = 0 ORDER BY id', (admin_id,))
        else:
            cursor.execute('SELECT * FROM group_photo_replies ORDER BY id')
            
        replies = cursor.fetchall()
        conn.close()
        
        # فك تشفير النصوص
        if decode:
            decoded_replies = []
            for reply in replies:
                reply_list = list(reply)
                if reply_list[2] and reply_list[7]:  # النص وكان مشفراً
                    try:
                        reply_list[2] = TextEncoder.decode_text(reply_list[2])
                    except:
                        pass
                decoded_replies.append(tuple(reply_list))
            return decoded_replies
        return replies
    
    def delete_group_photo_reply(self, reply_id, admin_id=None):
        """حذف رد جماعي مع صورة"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id:
            cursor.execute('DELETE FROM group_photo_replies WHERE id = ? AND (admin_id = ? OR admin_id = 0)', (reply_id, admin_id))
        else:
            cursor.execute('DELETE FROM group_photo_replies WHERE id = ?', (reply_id,))
            
        conn.commit()
        conn.close()
        return True
    
    def add_group_random_reply(self, reply_text, media_path=None, admin_id=0):
        """إضافة رد عشوائي في القروبات"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        encoded_reply = TextEncoder.encode_text(reply_text) if reply_text else None
        
        cursor.execute('''
            INSERT INTO group_random_replies (reply_text, media_path, admin_id, is_encoded, has_media)
            VALUES (?, ?, ?, ?, ?)
        ''', (encoded_reply, media_path, admin_id, 1 if reply_text else 0, 1 if media_path else 0))
        
        conn.commit()
        conn.close()
        return True
    
    def get_group_random_replies(self, admin_id=None, decode=True):
        """الحصول على الردود العشوائية في القروبات"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id is not None:
            cursor.execute('SELECT * FROM group_random_replies WHERE (admin_id = ? OR admin_id = 0) AND is_active = 1 ORDER BY id', (admin_id,))
        else:
            cursor.execute('SELECT * FROM group_random_replies WHERE is_active = 1 ORDER BY id')
            
        replies = cursor.fetchall()
        conn.close()
        
        # فك تشفير النصوص
        if decode:
            decoded_replies = []
            for reply in replies:
                reply_list = list(reply)
                if reply_list[1] and reply_list[6]:  # النص وكان مشفراً
                    try:
                        reply_list[1] = TextEncoder.decode_text(reply_list[1])
                    except:
                        pass
                decoded_replies.append(tuple(reply_list))
            return decoded_replies
        return replies
    
    def delete_group_random_reply(self, reply_id, admin_id=None):
        """حذف رد عشوائي في القروبات"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id:
            cursor.execute('DELETE FROM group_random_replies WHERE id = ? AND (admin_id = ? OR admin_id = 0)', (reply_id, admin_id))
        else:
            cursor.execute('DELETE FROM group_random_replies WHERE id = ?', (reply_id,))
            
        conn.commit()
        conn.close()
        return True
    
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
        self.publishing_active = {}
        self.publishing_tasks = {}
        self.private_reply_active = {}
        self.private_reply_tasks = {}
        self.group_reply_active = {}
        self.group_reply_tasks = {}
        self.random_reply_active = {}
        self.random_reply_tasks = {}
        self.join_groups_active = {}
        self.join_groups_tasks = {}
        self.client_cache = {}
        self.lock = threading.Lock()
    
    async def get_client(self, session_string):
        """الحصول على عميل من الذاكرة المؤقتة"""
        if session_string not in self.client_cache:
            try:
                client = TelegramClient(StringSession(session_string), 1, "b")
                await client.connect()
                if await client.is_user_authorized():
                    self.client_cache[session_string] = client
                else:
                    await client.disconnect()
                    return None
            except Exception as e:
                logger.error(f"خطأ في الاتصال: {str(e)}")
                return None
        
        return self.client_cache.get(session_string)
    
    async def cleanup_client(self, session_string):
        """تنظيف العميل من الذاكرة المؤقتة"""
        if session_string in self.client_cache:
            try:
                client = self.client_cache[session_string]
                await client.disconnect()
            except:
                pass
            del self.client_cache[session_string]
    
    async def join_groups_task(self, admin_id):
        """مهمة الانضمام إلى المجموعات"""
        while self.join_groups_active.get(admin_id, False):
            try:
                accounts = self.db.get_active_publishing_accounts(admin_id)
                groups = self.db.get_groups(admin_id)
                bulk_groups = self.db.get_bulk_groups(admin_id)
                
                pending_groups = [g for g in groups if g[2] == 'pending']
                pending_bulk_groups = [g for g in bulk_groups if g[4] == 'pending']
                
                all_pending = []
                for g in pending_groups:
                    all_pending.append((g[0], g[1], 'group'))
                for g in pending_bulk_groups:
                    all_pending.append((g[0], g[2], 'bulk_group'))
                
                if not accounts or not all_pending:
                    await asyncio.sleep(5)  # تقليل الانتظار إلى 5 ثواني فقط
                    continue
                
                for account in accounts:
                    if not self.join_groups_active.get(admin_id, False):
                        break
                    
                    account_id, session_string, name, username = account
                    
                    for group_info in all_pending:
                        if not self.join_groups_active.get(admin_id, False):
                            break
                        
                        group_id, group_link, group_type = group_info
                        
                        try:
                            client = await self.get_client(session_string)
                            if not client:
                                continue
                            
                            success = await self.join_single_group(client, group_link)
                            
                            if success:
                                if group_type == 'group':
                                    self.db.update_group_status(group_id, 'joined')
                                else:
                                    self.db.update_bulk_group_status(group_id, 'joined')
                                logger.info(f"✅ انضم الحساب {name} إلى المجموعة {group_link}")
                            else:
                                if group_type == 'group':
                                    self.db.update_group_status(group_id, 'failed')
                                else:
                                    self.db.update_bulk_group_status(group_id, 'failed')
                                logger.warning(f"❌ فشل انضمام {name} إلى {group_link}")
                            
                            # انتظار 0.5 ثانية فقط بين كل رابط (أقصى سرعة)
                            await asyncio.sleep(0.5)
                            
                        except Exception as e:
                            logger.error(f"خطأ في الحساب {name}: {str(e)}")
                            await self.cleanup_client(session_string)
                            continue
                
                await asyncio.sleep(5)  # الانتظار 5 ثواني بين الدورات (بدلاً من 60)
                
            except Exception as e:
                logger.error(f"خطأ في عملية الانضمام: {str(e)}")
                await asyncio.sleep(5)
    
    async def join_single_group(self, client, group_link):
        """الانضمام إلى مجموعة واحدة"""
        try:
            # تنظيف الرابط
            if group_link.startswith('https://'):
                group_link = group_link.replace('https://', '')
            
            if group_link.startswith('t.me/'):
                group_link = group_link.replace('t.me/', '')
            
            # التعامل مع أنواع الروابط المختلفة
            if group_link.startswith('+') or 'joinchat' in group_link:
                # رابط دعوة
                if group_link.startswith('+'):
                    invite_hash = group_link[1:]
                else:
                    invite_hash = group_link.split('/')[-1]
                
                await client(ImportChatInviteRequest(invite_hash))
                return True
                
            elif 'addlist' in group_link:
                # رابط قائمة (مجلد)
                folder_hash = group_link.split('/')[-1]
                try:
                    await client(ImportChatInviteRequest(folder_hash))
                    return True
                except:
                    # محاولة كرابط عادي
                    try:
                        await client(JoinChannelRequest(f'@{folder_hash}'))
                        return True
                    except:
                        return False
            else:
                # رابط عادي
                await client(JoinChannelRequest(f'@{group_link}'))
                return True
                
        except errors.FloodWaitError as e:
            logger.warning(f"⏳ Flood wait: {e.seconds} seconds")
            await asyncio.sleep(e.seconds + 1)  # تقليل وقت الانتظار الإضافي
            return False
        except errors.ChannelInvalidError:
            logger.error(f"❌ رابط غير صالح: {group_link}")
            return False
        except errors.ChannelPrivateError:
            logger.error(f"🔒 القناة خاصة: {group_link}")
            return False
        except errors.InviteHashExpiredError:
            logger.error(f"⏰ رابط منتهي: {group_link}")
            return False
        except errors.InviteHashInvalidError:
            logger.error(f"❌ رابط غير صالح: {group_link}")
            return False
        except errors.UserAlreadyParticipantError:
            logger.info(f"✅ مستخدم بالفعل في المجموعة: {group_link}")
            return True
        except Exception as e:
            logger.error(f"❌ خطأ في الانضمام: {str(e)}")
            return False
    
    async def publish_to_groups_task(self, admin_id):
        """مهمة النشر في المجموعات - أقصى سرعة"""
        while self.publishing_active.get(admin_id, False):
            try:
                accounts = self.db.get_active_publishing_accounts(admin_id)
                ads = self.db.get_ads(admin_id)
                
                if not accounts or not ads:
                    await asyncio.sleep(1)  # انتظار 1 ثانية فقط
                    continue
                
                # نشر من كل حساب
                for account in accounts:
                    if not self.publishing_active.get(admin_id, False):
                        break
                    
                    account_id, session_string, name, username = account
                    
                    try:
                        client = await self.get_client(session_string)
                        if not client:
                            continue
                        
                        # الحصول على جميع المجموعات التي انضم إليها الحساب
                        dialogs = await client.get_dialogs(limit=200)  # زيادة الحد إلى 200
                        
                        for dialog in dialogs:
                            if not self.publishing_active.get(admin_id, False):
                                break
                            
                            if dialog.is_group or dialog.is_channel:
                                try:
                                    # نشر جميع الإعلانات في هذه المجموعة
                                    for ad in ads:
                                        if not self.publishing_active.get(admin_id, False):
                                            break
                                        
                                        ad_id, ad_type, ad_text, media_path, file_type, added_date, ad_admin_id, is_encoded = ad
                                        
                                        try:
                                            if ad_type == 'text':
                                                await client.send_message(dialog.id, ad_text)
                                                logger.info(f"✅ نشر نص في {dialog.name} بواسطة {name}")
                                            elif ad_type == 'photo' and media_path and os.path.exists(media_path):
                                                await client.send_file(dialog.id, media_path, caption=ad_text)
                                                logger.info(f"✅ نشر صورة في {dialog.name} بواسطة {name}")
                                            elif ad_type == 'contact' and media_path and os.path.exists(media_path):
                                                # عند إرسال ملف VCF، تأكد من اسم الملف
                                                if media_path.endswith('.vcf'):
                                                    # تحميل الملف وإرساله باسم "تسوي سكليف صحتي واتساب.vcf"
                                                    with open(media_path, 'rb') as f:
                                                        await client.send_file(
                                                            dialog.id, 
                                                            f, 
                                                            caption=ad_text,
                                                            file_name="تسوي سكليف صحتي واتساب.vcf",
                                                            allow_cache=False
                                                        )
                                                    logger.info(f"✅ نشر جهة اتصال في {dialog.name} بواسطة {name}")
                                                else:
                                                    await client.send_file(dialog.id, media_path)
                                                    logger.info(f"✅ نشر ملف في {dialog.name} بواسطة {name}")
                                            elif media_path and os.path.exists(media_path):
                                                await client.send_file(dialog.id, media_path, caption=ad_text)
                                                logger.info(f"✅ نشر ملف في {dialog.name} بواسطة {name}")
                                            
                                            # انتظار 0.1 ثانية فقط بين الإعلانات (أقصى سرعة)
                                            await asyncio.sleep(0.1)
                                            
                                        except errors.FloodWaitError as e:
                                            logger.warning(f"⏳ Flood wait: {e.seconds} seconds")
                                            await asyncio.sleep(e.seconds + 1)  # تقليل وقت الانتظار
                                            continue
                                        except Exception as e:
                                            logger.error(f"❌ فشل نشر الإعلان {ad_id}: {str(e)}")
                                            continue
                                    
                                    # انتظار 0.2 ثانية فقط بين المجموعات (أقصى سرعة)
                                    await asyncio.sleep(0.2)
                                    
                                except Exception as e:
                                    logger.error(f"❌ فشل النشر في {dialog.name}: {str(e)}")
                                    continue
                        
                    except Exception as e:
                        logger.error(f"❌ خطأ في الحساب {name}: {str(e)}")
                        await self.cleanup_client(session_string)
                        continue
                
                # الانتظار 10 ثواني فقط قبل الدورة التالية (بدلاً من 30)
                await asyncio.sleep(10)
                
            except Exception as e:
                logger.error(f"❌ خطأ في عملية النشر: {str(e)}")
                await asyncio.sleep(10)
    
    async def handle_private_messages_task(self, admin_id):
        """مهمة الرد على الرسائل الخاصة - أقصى سرعة"""
        while self.private_reply_active.get(admin_id, False):
            try:
                accounts = self.db.get_active_publishing_accounts(admin_id)
                private_replies = self.db.get_private_replies(admin_id)
                
                if not accounts or not private_replies:
                    await asyncio.sleep(1)  # انتظار 1 ثانية فقط
                    continue
                
                for account in accounts:
                    if not self.private_reply_active.get(admin_id, False):
                        break
                    
                    account_id, session_string, name, username = account
                    
                    try:
                        client = await self.get_client(session_string)
                        if not client:
                            continue
                        
                        # الحصول على الرسائل الجديدة
                        async for message in client.iter_messages(None, limit=50):  # زيادة الحد إلى 50
                            if not self.private_reply_active.get(admin_id, False):
                                break
                            
                            if message.is_private and not message.out:
                                for reply in private_replies:
                                    reply_id, reply_text, is_active, added_date, reply_admin_id, is_encoded = reply
                                    if is_active:
                                        try:
                                            await client.send_message(message.sender_id, reply_text)
                                            logger.info(f"💬 رد على رسالة خاصة بواسطة {name}")
                                            await asyncio.sleep(0.05)  # انتظار 0.05 ثانية فقط
                                            break
                                        except errors.FloodWaitError as e:
                                            logger.warning(f"⏳ Flood wait: {e.seconds} seconds")
                                            await asyncio.sleep(e.seconds + 1)
                                            continue
                                        except Exception as e:
                                            logger.error(f"❌ فشل الرد: {str(e)}")
                                            continue
                        
                    except Exception as e:
                        logger.error(f"❌ خطأ في الحساب {name}: {str(e)}")
                        await self.cleanup_client(session_string)
                        continue
                
                # الانتظار 3 ثواني فقط قبل الدورة التالية
                await asyncio.sleep(3)
                
            except Exception as e:
                logger.error(f"❌ خطأ في معالجة الرسائل الخاصة: {str(e)}")
                await asyncio.sleep(5)
    
    async def handle_group_replies_task(self, admin_id):
        """مهمة الردود في المجموعات - أقصى سرعة"""
        while self.group_reply_active.get(admin_id, False):
            try:
                accounts = self.db.get_active_publishing_accounts(admin_id)
                text_replies = self.db.get_group_text_replies(admin_id)
                photo_replies = self.db.get_group_photo_replies(admin_id)
                
                if not accounts or (not text_replies and not photo_replies):
                    await asyncio.sleep(1)  # انتظار 1 ثانية فقط
                    continue
                
                for account in accounts:
                    if not self.group_reply_active.get(admin_id, False):
                        break
                    
                    account_id, session_string, name, username = account
                    
                    try:
                        client = await self.get_client(session_string)
                        if not client:
                            continue
                        
                        dialogs = await client.get_dialogs(limit=100)  # زيادة الحد إلى 100
                        
                        for dialog in dialogs:
                            if not self.group_reply_active.get(admin_id, False):
                                break
                            
                            if dialog.is_group:
                                try:
                                    async for message in client.iter_messages(dialog.id, limit=10):  # زيادة الحد إلى 10
                                        if not self.group_reply_active.get(admin_id, False):
                                            break
                                        
                                        if message.text and not message.out:
                                            # الردود النصية
                                            for reply in text_replies:
                                                reply_id, trigger, reply_text, is_active, added_date, reply_admin_id, is_encoded = reply
                                                
                                                if is_active and trigger.lower() in message.text.lower():
                                                    try:
                                                        await client.send_message(dialog.id, reply_text, reply_to=message.id)
                                                        logger.info(f"💬 رد على {trigger} في {dialog.name} بواسطة {name}")
                                                        await asyncio.sleep(0.05)  # انتظار 0.05 ثانية فقط
                                                        break
                                                    except errors.FloodWaitError as e:
                                                        logger.warning(f"⏳ Flood wait: {e.seconds} seconds")
                                                        await asyncio.sleep(e.seconds + 1)
                                                        continue
                                                    except Exception as e:
                                                        logger.error(f"❌ فشل الرد: {str(e)}")
                                                        continue
                                            
                                            # الردود مع الصور
                                            for reply in photo_replies:
                                                reply_id, trigger, reply_text, media_path, is_active, added_date, reply_admin_id, is_encoded = reply
                                                
                                                if is_active and trigger.lower() in message.text.lower() and os.path.exists(media_path):
                                                    try:
                                                        await client.send_file(dialog.id, media_path, caption=reply_text, reply_to=message.id)
                                                        logger.info(f"🖼️ رد بصورة على {trigger} في {dialog.name} بواسطة {name}")
                                                        await asyncio.sleep(0.05)  # انتظار 0.05 ثانية فقط
                                                        break
                                                    except errors.FloodWaitError as e:
                                                        logger.warning(f"⏳ Flood wait: {e.seconds} seconds")
                                                        await asyncio.sleep(e.seconds + 1)
                                                        continue
                                                    except Exception as e:
                                                        logger.error(f"❌ فشل الرد: {str(e)}")
                                                        continue
                                    
                                except Exception as e:
                                    logger.error(f"❌ فشل في المجموعة {dialog.name}: {str(e)}")
                                    continue
                        
                    except Exception as e:
                        logger.error(f"❌ خطأ في الحساب {name}: {str(e)}")
                        await self.cleanup_client(session_string)
                        continue
                
                # الانتظار 3 ثواني فقط قبل الدورة التالية
                await asyncio.sleep(3)
                
            except Exception as e:
                logger.error(f"❌ خطأ في معالجة الردود الجماعية: {str(e)}")
                await asyncio.sleep(5)
    
    async def handle_random_replies_task(self, admin_id):
        """مهمة الردود العشوائية في القروبات - أقصى سرعة"""
        while self.random_reply_active.get(admin_id, False):
            try:
                accounts = self.db.get_active_publishing_accounts(admin_id)
                random_replies = self.db.get_group_random_replies(admin_id)
                
                if not accounts or not random_replies:
                    await asyncio.sleep(1)  # انتظار 1 ثانية فقط
                    continue
                
                for account in accounts:
                    if not self.random_reply_active.get(admin_id, False):
                        break
                    
                    account_id, session_string, name, username = account
                    
                    try:
                        client = await self.get_client(session_string)
                        if not client:
                            continue
                        
                        dialogs = await client.get_dialogs(limit=100)  # زيادة الحد إلى 100
                        
                        for dialog in dialogs:
                            if not self.random_reply_active.get(admin_id, False):
                                break
                            
                            if dialog.is_group:
                                try:
                                    async for message in client.iter_messages(dialog.id, limit=5):  # زيادة الحد إلى 5
                                        if not self.random_reply_active.get(admin_id, False):
                                            break
                                        
                                        if message.text and not message.out and random.random() < 1.0:  # 100% رد
                                            random_reply = random.choice(random_replies)
                                            reply_id, reply_text, media_path, is_active, added_date, reply_admin_id, is_encoded, has_media = random_reply
                                            
                                            if is_active:
                                                try:
                                                    if has_media and media_path and os.path.exists(media_path):
                                                        await client.send_file(dialog.id, media_path, caption=reply_text, reply_to=message.id)
                                                        logger.info(f"🎲 رد عشوائي مع صورة في {dialog.name} بواسطة {name}")
                                                    else:
                                                        await client.send_message(dialog.id, reply_text, reply_to=message.id)
                                                        logger.info(f"🎲 رد عشوائي في {dialog.name} بواسطة {name}")
                                                    
                                                    await asyncio.sleep(0.05)  # انتظار 0.05 ثانية فقط
                                                    break
                                                    
                                                except errors.FloodWaitError as e:
                                                    logger.warning(f"⏳ Flood wait: {e.seconds} seconds")
                                                    await asyncio.sleep(e.seconds + 1)
                                                    continue
                                                except Exception as e:
                                                    logger.error(f"❌ فشل الرد العشوائي: {str(e)}")
                                                    continue
                                    
                                except Exception as e:
                                    logger.error(f"❌ فشل في المجموعة {dialog.name}: {str(e)}")
                                    continue
                        
                    except Exception as e:
                        logger.error(f"❌ خطأ في الحساب {name}: {str(e)}")
                        await self.cleanup_client(session_string)
                        continue
                
                # الانتظار 3 ثواني فقط قبل الدورة التالية
                await asyncio.sleep(3)
                
            except Exception as e:
                logger.error(f"❌ خطأ في معالجة الردود العشوائية: {str(e)}")
                await asyncio.sleep(5)
    
    def start_publishing(self, admin_id):
        """بدء النشر التلقائي"""
        with self.lock:
            if not self.publishing_active.get(admin_id, False):
                self.publishing_active[admin_id] = True
                task = asyncio.create_task(self.publish_to_groups_task(admin_id))
                self.publishing_tasks[admin_id] = task
                return True
        return False
    
    def stop_publishing(self, admin_id):
        """إيقاف النشر التلقائي"""
        with self.lock:
            if self.publishing_active.get(admin_id, False):
                self.publishing_active[admin_id] = False
                if admin_id in self.publishing_tasks:
                    try:
                        self.publishing_tasks[admin_id].cancel()
                    except:
                        pass
                    del self.publishing_tasks[admin_id]
                return True
        return False
    
    def start_private_reply(self, admin_id):
        """بدء الرد على الرسائل الخاصة"""
        with self.lock:
            if not self.private_reply_active.get(admin_id, False):
                self.private_reply_active[admin_id] = True
                task = asyncio.create_task(self.handle_private_messages_task(admin_id))
                self.private_reply_tasks[admin_id] = task
                return True
        return False
    
    def stop_private_reply(self, admin_id):
        """إيقاف الرد على الرسائل الخاصة"""
        with self.lock:
            if self.private_reply_active.get(admin_id, False):
                self.private_reply_active[admin_id] = False
                if admin_id in self.private_reply_tasks:
                    try:
                        self.private_reply_tasks[admin_id].cancel()
                    except:
                        pass
                    del self.private_reply_tasks[admin_id]
                return True
        return False
    
    def start_group_reply(self, admin_id):
        """بدء الردود في المجموعات"""
        with self.lock:
            if not self.group_reply_active.get(admin_id, False):
                self.group_reply_active[admin_id] = True
                task = asyncio.create_task(self.handle_group_replies_task(admin_id))
                self.group_reply_tasks[admin_id] = task
                return True
        return False
    
    def stop_group_reply(self, admin_id):
        """إيقاف الردود في المجموعات"""
        with self.lock:
            if self.group_reply_active.get(admin_id, False):
                self.group_reply_active[admin_id] = False
                if admin_id in self.group_reply_tasks:
                    try:
                        self.group_reply_tasks[admin_id].cancel()
                    except:
                        pass
                    del self.group_reply_tasks[admin_id]
                return True
        return False
    
    def start_random_reply(self, admin_id):
        """بدء الردود العشوائية في القروبات"""
        with self.lock:
            if not self.random_reply_active.get(admin_id, False):
                self.random_reply_active[admin_id] = True
                task = asyncio.create_task(self.handle_random_replies_task(admin_id))
                self.random_reply_tasks[admin_id] = task
                return True
        return False
    
    def stop_random_reply(self, admin_id):
        """إيقاف الردود العشوائية في القروبات"""
        with self.lock:
            if self.random_reply_active.get(admin_id, False):
                self.random_reply_active[admin_id] = False
                if admin_id in self.random_reply_tasks:
                    try:
                        self.random_reply_tasks[admin_id].cancel()
                    except:
                        pass
                    del self.random_reply_tasks[admin_id]
                return True
        return False
    
    def start_join_groups(self, admin_id):
        """بدء الانضمام إلى المجموعات"""
        with self.lock:
            if not self.join_groups_active.get(admin_id, False):
                self.join_groups_active[admin_id] = True
                task = asyncio.create_task(self.join_groups_task(admin_id))
                self.join_groups_tasks[admin_id] = task
                return True
        return False
    
    def stop_join_groups(self, admin_id):
        """إيقاف الانضمام إلى المجموعات"""
        with self.lock:
            if self.join_groups_active.get(admin_id, False):
                self.join_groups_active[admin_id] = False
                if admin_id in self.join_groups_tasks:
                    try:
                        self.join_groups_tasks[admin_id].cancel()
                    except:
                        pass
                    del self.join_groups_tasks[admin_id]
                return True
        return False
    
    async def cleanup_all(self):
        """تنظيف جميع الموارد"""
        for session_string in list(self.client_cache.keys()):
            await self.cleanup_client(session_string)

class BotHandler:
    def __init__(self):
        self.db = BotDatabase()
        self.manager = TelegramBotManager(self.db)
        self.application = None
        self.user_conversations = {}
    
    def get_user_context(self, user_id):
        """الحصول على سياق المستخدم"""
        if user_id not in self.user_conversations:
            self.user_conversations[user_id] = {}
        return self.user_conversations[user_id]
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بدء البوت"""
        user = update.effective_user
        user_id = user.id
        
        if not self.db.is_admin(user_id):
            await update.message.reply_text("❌ ليس لديك صلاحية للوصول إلى هذا البوت.")
            return
        
        user_context = self.get_user_context(user_id)
        user_context['conversation_active'] = False
        
        keyboard = [
            [InlineKeyboardButton("👥 إدارة الحسابات", callback_data="manage_accounts")],
            [InlineKeyboardButton("📢 إدارة الإعلانات", callback_data="manage_ads")],
            [InlineKeyboardButton("👥 إدارة المجموعات", callback_data="manage_groups")],
            [InlineKeyboardButton("💬 إدارة الردود", callback_data="manage_replies")],
            [InlineKeyboardButton("👨‍💼 إدارة المشرفين", callback_data="manage_admins")],
            [InlineKeyboardButton("🚀 بدء النشر", callback_data="start_publishing")],
            [InlineKeyboardButton("⏹️ إيقاف النشر", callback_data="stop_publishing")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🚀 **لوحة تحكم البوت الفعلي - السرعة القصوى**\n\n"
            "⚡ النشر بأقصى سرعة ممكنة\n"
            "⚡ الردود التلقائية بأقصى سرعة\n"
            "⚡ الانضمام للمجموعات بأقصى سرعة\n\n"
            "اختر الإجراء الذي تريد تنفيذه:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """إلغاء الأمر الحالي"""
        user_id = update.message.from_user.id
        if not self.db.is_admin(user_id):
            await update.message.reply_text("❌ ليس لديك صلاحية للوصول إلى هذا البوت.")
            return
        
        user_context = self.get_user_context(user_id)
        user_context['conversation_active'] = False
        
        await update.message.reply_text("❌ تم إلغاء الأمر.")
        await self.start(update, context)
        return ConversationHandler.END
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة الأزرار"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        if not self.db.is_admin(user_id):
            await query.edit_message_text("❌ ليس لديك صلاحية للوصول إلى هذا البوت.")
            return
        
        data = query.data
        
        user_context = self.get_user_context(user_id)
        
        if data == "manage_accounts":
            await self.manage_accounts(query, context)
        elif data == "manage_ads":
            await self.manage_ads(query, context)
        elif data == "manage_groups":
            await self.manage_groups(query, context)
        elif data == "manage_replies":
            await self.manage_replies(query, context)
        elif data == "manage_admins":
            await self.manage_admins(query, context)
        elif data == "start_publishing":
            await self.start_publishing(query, context)
        elif data == "stop_publishing":
            await self.stop_publishing(query, context)
        elif data == "start_private_reply":
            await self.start_private_reply(query, context)
        elif data == "stop_private_reply":
            await self.stop_private_reply(query, context)
        elif data == "start_group_reply":
            await self.start_group_reply(query, context)
        elif data == "stop_group_reply":
            await self.stop_group_reply(query, context)
        elif data == "start_random_reply":
            await self.start_random_reply(query, context)
        elif data == "stop_random_reply":
            await self.stop_random_reply(query, context)
        elif data == "start_join_groups":
            await self.start_join_groups(query, context)
        
        # إدارة الحسابات
        elif data == "add_account":
            await self.add_account_start(update, context)
        elif data == "show_accounts":
            await self.show_accounts(query, context)
        elif data.startswith("delete_account_"):
            account_id = int(data.split("_")[2])
            await self.delete_account(query, context, account_id)
        
        # إدارة الإعلانات
        elif data == "add_ad":
            await self.add_ad_start(query, context)
        elif data == "show_ads":
            await self.show_ads(query, context)
        elif data.startswith("delete_ad_"):
            ad_id = int(data.split("_")[2])
            await self.delete_ad(query, context, ad_id)
        
        # أنواع الإعلانات
        elif data.startswith("ad_type_"):
            ad_type = data.replace("ad_type_", "")
            user_context = self.get_user_context(user_id)
            user_context['ad_type'] = ad_type
            
            if ad_type == 'contact':
                await query.edit_message_text(
                    f"📞 **إضافة جهة اتصال**\n\n"
                    f"أرسل ملف VCF أو جهة اتصال:\n\n"
                    f"أو أرسل /cancel للإلغاء",
                    parse_mode='Markdown'
                )
                user_context['conversation_active'] = True
                context.user_data['ad_type'] = ad_type
                context.user_data['conversation_active'] = True
                return ADD_AD_MEDIA
            else:
                file_type_text = {
                    'text': 'نص الإعلان',
                    'photo': 'نص الإعلان للصورة',
                }
                
                await query.edit_message_text(
                    f"📝 **{file_type_text.get(ad_type, 'إضافة نص الإعلان')}**\n\n"
                    f"أرسل النص الآن:\n\n"
                    f"أو أرسل /cancel للإلغاء",
                    parse_mode='Markdown'
                )
                user_context['conversation_active'] = True
                context.user_data['ad_type'] = ad_type
                context.user_data['conversation_active'] = True
                return ADD_AD_TEXT
        
        # إدارة المجموعات
        elif data == "add_group":
            await self.add_group_start(update, context)
        elif data == "show_groups":
            await self.show_groups(query, context)
        
        # إدارة الردود
        elif data == "private_replies":
            await self.manage_private_replies(query, context)
        elif data == "group_replies":
            await self.manage_group_replies(query, context)
        elif data == "add_private_reply":
            await self.add_private_reply_start(update, context)
        elif data == "add_group_text_reply":
            await self.add_group_text_reply_start(update, context)
        elif data == "add_group_photo_reply":
            await self.add_group_photo_reply_start(update, context)
        elif data == "add_random_reply":
            await self.add_random_reply_start(update, context)
        elif data == "show_replies":
            await self.show_replies_menu(query, context)
        
        # حذف الردود
        elif data.startswith("delete_private_reply_"):
            reply_id = int(data.split("_")[3])
            await self.delete_private_reply(query, context, reply_id)
        elif data.startswith("delete_text_reply_"):
            reply_id = int(data.split("_")[3])
            await self.delete_text_reply(query, context, reply_id)
        elif data.startswith("delete_photo_reply_"):
            reply_id = int(data.split("_")[3])
            await self.delete_photo_reply(query, context, reply_id)
        elif data.startswith("delete_random_reply_"):
            reply_id = int(data.split("_")[3])
            await self.delete_random_reply(query, context, reply_id)
        
        # إدارة المشرفين
        elif data == "add_admin":
            await self.add_admin_start(update, context)
        elif data == "show_admins":
            await self.show_admins(query, context)
        elif data.startswith("delete_admin_"):
            admin_id = int(data.split("_")[2])
            await self.delete_admin(query, context, admin_id)
        
        # الرجوع
        elif data == "back_to_main":
            await self.start_from_query(query, context)
        elif data == "back_to_accounts":
            await self.manage_accounts(query, context)
        elif data == "back_to_ads":
            await self.manage_ads(query, context)
        elif data == "back_to_groups":
            await self.manage_groups(query, context)
        elif data == "back_to_replies":
            await self.manage_replies(query, context)
        elif data == "back_to_admins":
            await self.manage_admins(query, context)
        elif data == "back_to_private_replies":
            await self.manage_private_replies(query, context)
        elif data == "back_to_group_replies":
            await self.manage_group_replies(query, context)
    
    async def start_from_query(self, query, context):
        """بدء البوت من استعلام"""
        user_id = query.from_user.id
        user_context = self.get_user_context(user_id)
        user_context['conversation_active'] = False
            
        keyboard = [
            [InlineKeyboardButton("👥 إدارة الحسابات", callback_data="manage_accounts")],
            [InlineKeyboardButton("📢 إدارة الإعلانات", callback_data="manage_ads")],
            [InlineKeyboardButton("👥 إدارة المجموعات", callback_data="manage_groups")],
            [InlineKeyboardButton("💬 إدارة الردود", callback_data="manage_replies")],
            [InlineKeyboardButton("👨‍💼 إدارة المشرفين", callback_data="manage_admins")],
            [InlineKeyboardButton("🚀 بدء النشر", callback_data="start_publishing")],
            [InlineKeyboardButton("⏹️ إيقاف النشر", callback_data="stop_publishing")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🚀 **لوحة تحكم البوت الفعلي - السرعة القصوى**\n\n"
            "⚡ النشر بأقصى سرعة ممكنة\n"
            "⚡ الردود التلقائية بأقصى سرعة\n"
            "⚡ الانضمام للمجموعات بأقصى سرعة\n\n"
            "اختر الإجراء الذي تريد تنفيذه:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def start_publishing(self, query, context):
        """بدء النشر التلقائي"""
        admin_id = query.from_user.id
        
        # التحقق من وجود حسابات
        accounts = self.db.get_active_publishing_accounts(admin_id)
        if not accounts:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "❌ **لا توجد حسابات نشطة!**\n\n"
                "يجب إضافة حسابات أولاً قبل بدء النشر.",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            return
        
        # التحقق من وجود إعلانات
        ads = self.db.get_ads(admin_id)
        if not ads:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "❌ **لا توجد إعلانات!**\n\n"
                "يجب إضافة إعلانات أولاً قبل بدء النشر.",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            return
        
        if self.manager.start_publishing(admin_id):
            keyboard = [
                [InlineKeyboardButton("⏹️ إيقاف النشر", callback_data="stop_publishing")],
                [InlineKeyboardButton("💬 بدء الرد في الخاص", callback_data="start_private_reply")],
                [InlineKeyboardButton("👥 بدء الرد في القروبات", callback_data="start_group_reply")],
                [InlineKeyboardButton("🎲 بدء الرد العشوائي", callback_data="start_random_reply")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "🚀 **تم بدء النشر بأقصى سرعة!**\n\n"
                f"✅ عدد الحسابات: {len(accounts)}\n"
                f"✅ عدد الإعلانات: {len(ads)}\n"
                f"⚡ السرعة: 0.1 ثانية بين الإعلانات\n"
                f"⚡ 0.2 ثانية بين المجموعات\n"
                f"⚡ 10 ثواني بين الدورات\n\n"
                "سيبدأ البوت بالنشر في جميع المجموعات الآن بأقصى سرعة ممكنة.",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
            # تسجيل بدء النشر
            logger.info(f"✅ بدأ النشر بأقصى سرعة للمشرف {admin_id} بـ {len(accounts)} حساب و {len(ads)} إعلان")
        else:
            await query.edit_message_text("⚠️ النشر يعمل بالفعل!")
    
    async def stop_publishing(self, query, context):
        """إيقاف النشر التلقائي"""
        admin_id = query.from_user.id
        if self.manager.stop_publishing(admin_id):
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("⏹️ **تم إيقاف النشر!**", reply_markup=reply_markup)
            logger.info(f"⏹️ توقف النشر للمشرف {admin_id}")
        else:
            await query.edit_message_text("⚠️ النشر غير نشط!")
    
    async def start_private_reply(self, query, context):
        """بدء الرد التلقائي في الخاص"""
        admin_id = query.from_user.id
        
        # التحقق من وجود حسابات
        accounts = self.db.get_active_publishing_accounts(admin_id)
        if not accounts:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_private_replies")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "❌ **لا توجد حسابات نشطة!**",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            return
        
        # التحقق من وجود ردود
        replies = self.db.get_private_replies(admin_id)
        if not replies:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_private_replies")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "❌ **لا توجد ردود خاصة!**",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            return
        
        if self.manager.start_private_reply(admin_id):
            keyboard = [[InlineKeyboardButton("⏹️ إيقاف الرد", callback_data="stop_private_reply")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "💬 **تم بدء الرد في الخاص بأقصى سرعة!**\n\n"
                f"✅ عدد الحسابات: {len(accounts)}\n"
                f"✅ عدد الردود: {len(replies)}\n"
                f"⚡ السرعة: 0.05 ثانية بين الردود\n"
                f"⚡ 3 ثواني بين الدورات\n\n"
                "سيبدأ البوت بالرد على الرسائل الخاصة الآن بأقصى سرعة ممكنة.",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            logger.info(f"💬 بدأ الرد في الخاص بأقصى سرعة للمشرف {admin_id}")
        else:
            await query.edit_message_text("⚠️ الرد في الخاص يعمل بالفعل!")
    
    async def stop_private_reply(self, query, context):
        """إيقاف الرد التلقائي في الخاص"""
        admin_id = query.from_user.id
        if self.manager.stop_private_reply(admin_id):
            await query.edit_message_text("⏹️ تم إيقاف الرد في الخاص!")
            logger.info(f"⏹️ توقف الرد في الخاص للمشرف {admin_id}")
        else:
            await query.edit_message_text("⚠️ الرد في الخاص غير نشط!")
    
    async def start_group_reply(self, query, context):
        """بدء الرد التلقائي في القروبات"""
        admin_id = query.from_user.id
        
        accounts = self.db.get_active_publishing_accounts(admin_id)
        if not accounts:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_group_replies")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("❌ لا توجد حسابات نشطة!", reply_markup=reply_markup)
            return
        
        text_replies = self.db.get_group_text_replies(admin_id)
        photo_replies = self.db.get_group_photo_replies(admin_id)
        
        if not text_replies and not photo_replies:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_group_replies")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("❌ لا توجد ردود مضافة!", reply_markup=reply_markup)
            return
        
        if self.manager.start_group_reply(admin_id):
            keyboard = [[InlineKeyboardButton("⏹️ إيقاف الرد", callback_data="stop_group_reply")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "👥 **تم بدء الرد في القروبات بأقصى سرعة!**\n\n"
                f"✅ عدد الحسابات: {len(accounts)}\n"
                f"✅ عدد الردود النصية: {len(text_replies)}\n"
                f"✅ عدد الردود مع الصور: {len(photo_replies)}\n"
                f"⚡ السرعة: 0.05 ثانية بين الردود\n"
                f"⚡ 3 ثواني بين الدورات\n\n"
                "سيبدأ البوت بالرد على الرسائل في القروبات الآن بأقصى سرعة ممكنة.",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            logger.info(f"👥 بدأ الرد في القروبات بأقصى سرعة للمشرف {admin_id}")
        else:
            await query.edit_message_text("⚠️ الرد في القروبات يعمل بالفعل!")
    
    async def stop_group_reply(self, query, context):
        """إيقاف الرد التلقائي في القروبات"""
        admin_id = query.from_user.id
        if self.manager.stop_group_reply(admin_id):
            await query.edit_message_text("⏹️ تم إيقاف الرد في القروبات!")
            logger.info(f"⏹️ توقف الرد في القروبات للمشرف {admin_id}")
        else:
            await query.edit_message_text("⚠️ الرد في القروبات غير نشط!")
    
    async def start_random_reply(self, query, context):
        """بدء الردود العشوائية في القروبات"""
        admin_id = query.from_user.id
        
        accounts = self.db.get_active_publishing_accounts(admin_id)
        if not accounts:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_group_replies")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("❌ لا توجد حسابات نشطة!", reply_markup=reply_markup)
            return
        
        random_replies = self.db.get_group_random_replies(admin_id)
        if not random_replies:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_group_replies")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("❌ لا توجد ردود عشوائية مضافة!", reply_markup=reply_markup)
            return
        
        if self.manager.start_random_reply(admin_id):
            keyboard = [[InlineKeyboardButton("⏹️ إيقاف الرد العشوائي", callback_data="stop_random_reply")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "🎲 **تم بدء الردود العشوائية بأقصى سرعة!**\n\n"
                f"✅ عدد الحسابات: {len(accounts)}\n"
                f"✅ عدد الردود العشوائية: {len(random_replies)}\n"
                f"✅ الرد على 100% من الرسائل\n"
                f"⚡ السرعة: 0.05 ثانية بين الردود\n"
                f"⚡ 3 ثواني بين الدورات\n\n"
                "سيبدأ البوت بالرد العشوائي في القروبات الآن بأقصى سرعة ممكنة.",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            logger.info(f"🎲 بدأ الرد العشوائي بأقصى سرعة للمشرف {admin_id}")
        else:
            await query.edit_message_text("⚠️ الرد العشوائي يعمل بالفعل!")
    
    async def stop_random_reply(self, query, context):
        """إيقاف الردود العشوائية في القروبات"""
        admin_id = query.from_user.id
        if self.manager.stop_random_reply(admin_id):
            await query.edit_message_text("⏹️ تم إيقاف الرد العشوائي!")
            logger.info(f"⏹️ توقف الرد العشوائي للمشرف {admin_id}")
        else:
            await query.edit_message_text("⚠️ الرد العشوائي غير نشط!")
    
    async def start_join_groups(self, query, context):
        """بدء الانضمام إلى المجموعات"""
        admin_id = query.from_user.id
        
        accounts = self.db.get_active_publishing_accounts(admin_id)
        if not accounts:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_groups")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("❌ لا توجد حسابات نشطة!", reply_markup=reply_markup)
            return
        
        groups = self.db.get_groups(admin_id)
        bulk_groups = self.db.get_bulk_groups(admin_id)
        
        pending_groups = [g for g in groups if g[2] == 'pending']
        pending_bulk_groups = [g for g in bulk_groups if g[4] == 'pending']
        
        if not pending_groups and not pending_bulk_groups:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_groups")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("❌ لا توجد مجموعات معلقة للانضمام!", reply_markup=reply_markup)
            return
        
        if self.manager.start_join_groups(admin_id):
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_groups")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "👥 **بدأ الانضمام إلى المجموعات بأقصى سرعة!**\n\n"
                f"✅ عدد الحسابات: {len(accounts)}\n"
                f"✅ عدد المجموعات المعلقة: {len(pending_groups) + len(pending_bulk_groups)}\n"
                f"⚡ الانتظار: 0.5 ثانية فقط بين كل رابط\n"
                f"⚡ 5 ثواني بين الدورات\n\n"
                "سيبدأ البوت بالانضمام إلى جميع المجموعات المعلقة الآن بأقصى سرعة ممكنة.",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            logger.info(f"👥 بدأ الانضمام للمجموعات بأقصى سرعة للمشرف {admin_id}")
        else:
            await query.edit_message_text("⚠️ عملية الانضمام تعمل بالفعل!")
    
    # قسم إدارة الحسابات
    async def manage_accounts(self, query, context):
        """إدارة الحسابات"""
        keyboard = [
            [InlineKeyboardButton("➕ إضافة حساب", callback_data="add_account")],
            [InlineKeyboardButton("👥 عرض الحسابات", callback_data="show_accounts")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "👥 **إدارة الحسابات**\n\n"
            "اختر الإجراء الذي تريد تنفيذه:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def add_account_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بدء إضافة حساب"""
        user_id = update.callback_query.from_user.id
        user_context = self.get_user_context(user_id)
        user_context['conversation_active'] = True
        
        await update.callback_query.edit_message_text(
            "📱 **إضافة حساب جديد**\n\n"
            "أرسل كود الجلسة (Session String):\n\n"
            "أو أرسل /cancel للإلغاء",
            parse_mode='Markdown'
        )
        context.user_data['conversation_active'] = True
        return ADD_ACCOUNT
    
    async def add_account_session(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة كود الجلسة"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
        if not user_context.get('conversation_active', False) and not context.user_data.get('conversation_active', False):
            await update.message.reply_text("❌ تم إلغاء العملية. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        session_string = update.message.text
        admin_id = update.message.from_user.id
        
        await update.message.reply_text("⏳ جاري اختبار الجلسة...")
        
        try:
            client = TelegramClient(StringSession(session_string), 1, "b")
            await client.connect()
            
            if await client.is_user_authorized():
                me = await client.get_me()
                await client.disconnect()
                
                phone = me.phone if me.phone else "غير معروف"
                name = f"{me.first_name} {me.last_name}" if me.last_name else me.first_name
                username = f"@{me.username}" if me.username else "لا يوجد"
                
                result, message = self.db.add_account(session_string, phone, name, username, admin_id)
                
                if result:
                    keyboard = [[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="back_to_accounts")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await update.message.reply_text(
                        f"✅ {message}\n\n"
                        f"📱 الحساب: {name}\n"
                        f"📞 الهاتف: {phone}\n"
                        f"👤 المستخدم: {username}",
                        reply_markup=reply_markup
                    )
                else:
                    await update.message.reply_text(f"❌ {message}")
            else:
                await client.disconnect()
                await update.message.reply_text("❌ كود الجلسة غير صالح أو الحساب غير مفعل")
                
        except Exception as e:
            logger.error(f"خطأ في اختبار الجلسة: {str(e)}")
            await update.message.reply_text(f"❌ خطأ في اختبار الجلسة: {str(e)}")
        
        user_context['conversation_active'] = False
        context.user_data['conversation_active'] = False
        return ConversationHandler.END
    
    async def show_accounts(self, query, context):
        """عرض الحسابات"""
        admin_id = query.from_user.id
        accounts = self.db.get_accounts(admin_id)
        
        if not accounts:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_accounts")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("❌ لا توجد حسابات مضافة", reply_markup=reply_markup)
            return
        
        text = "👥 **الحسابات المضافة:**\n\n"
        keyboard = []
        
        for account in accounts:
            account_id, session_string, phone, name, username, is_active = account
            status = "🟢 نشط" if is_active else "🔴 غير نشط"
            
            text += f"**#{account_id}** - {name}\n"
            text += f"📱 {phone} | {username}\n"
            text += f"الحالة: {status}\n"
            text += "─" * 20 + "\n"
            
            keyboard.append([InlineKeyboardButton(f"🗑️ حذف #{account_id}", callback_data=f"delete_account_{account_id}")])
        
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_to_accounts")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def delete_account(self, query, context, account_id):
        """حذف حساب"""
        admin_id = query.from_user.id
        self.db.delete_account(account_id, admin_id)
        await query.edit_message_text(f"✅ تم حذف الحساب #{account_id}")
        await self.show_accounts(query, context)
    
    # قسم إدارة الإعلانات
    async def manage_ads(self, query, context):
        """إدارة الإعلانات"""
        keyboard = [
            [InlineKeyboardButton("➕ إضافة إعلان", callback_data="add_ad")],
            [InlineKeyboardButton("📋 عرض الإعلانات", callback_data="show_ads")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "📢 **إدارة الإعلانات**\n\n"
            "اختر الإجراء الذي تريد تنفيذه:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def add_ad_start(self, query, context):
        """بدء إضافة إعلان"""
        keyboard = [
            [InlineKeyboardButton("📝 نص فقط", callback_data="ad_type_text")],
            [InlineKeyboardButton("🖼️ صورة مع نص", callback_data="ad_type_photo")],
            [InlineKeyboardButton("📞 جهة اتصال (VCF)", callback_data="ad_type_contact")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_ads")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "📢 **إضافة إعلان جديد**\n\n"
            "اختر نوع الإعلان:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def add_ad_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة نص الإعلان"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
        if not user_context.get('conversation_active', False) and not context.user_data.get('conversation_active', False):
            await update.message.reply_text("❌ تم إلغاء العملية. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        ad_type = context.user_data.get('ad_type') or user_context.get('ad_type')
        if not ad_type:
            await update.message.reply_text("❌ خطأ: لم يتم تحديد نوع الإعلان. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        ad_text = update.message.text
        admin_id = update.message.from_user.id
        
        user_context['ad_text'] = ad_text
        context.user_data['ad_text'] = ad_text
        
        if ad_type == 'text':
            success = self.db.add_ad('text', ad_text, admin_id=admin_id)
            if success:
                keyboard = [[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="back_to_ads")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text("✅ تم إضافة الإعلان النصي بنجاح", reply_markup=reply_markup)
            else:
                await update.message.reply_text("❌ فشل إضافة الإعلان النصي")
            
            user_context['conversation_active'] = False
            context.user_data['conversation_active'] = False
            return ConversationHandler.END
        elif ad_type == 'photo':
            await update.message.reply_text(
                f"🖼️ **إضافة صورة**\n\n"
                f"أرسل الصورة الآن:\n\n"
                f"أو أرسل /cancel للإلغاء"
            )
            return ADD_AD_MEDIA
    
    def create_vcf_from_contact(self, contact):
        """إنشاء ملف VCF من بيانات جهة الاتصال"""
        try:
            vcf_lines = []
            vcf_lines.append("BEGIN:VCARD")
            vcf_lines.append("VERSION:3.0")
            
            full_name = ""
            if contact.first_name:
                full_name += contact.first_name
            if contact.last_name:
                full_name += " " + contact.last_name
            
            if full_name.strip():
                # تحديث اسم الملف ليكون: تسوي سكليف صحتي واتساب
                vcf_lines.append(f"FN:تسوي سكليف صحتي واتساب")
                vcf_lines.append(f"N:سكليف صحتي واتساب;تسوي;;;")
            else:
                vcf_lines.append(f"FN:تسوي سكليف صحتي واتساب")
                vcf_lines.append(f"N:سكليف صحتي واتساب;تسوي;;;")
            
            if contact.phone_number:
                vcf_lines.append(f"TEL;TYPE=CELL:{contact.phone_number}")
            
            if contact.user_id:
                vcf_lines.append(f"X-TELEGRAM-ID:{contact.user_id}")
            
            vcf_lines.append("END:VCARD")
            
            return "\n".join(vcf_lines)
        except Exception as e:
            logger.error(f"خطأ في إنشاء VCF: {str(e)}")
            return None
    
    async def add_ad_media(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة ملف الإعلان"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
        if not user_context.get('conversation_active', False) and not context.user_data.get('conversation_active', False):
            await update.message.reply_text("❌ تم إلغاء العملية. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        ad_type = context.user_data.get('ad_type') or user_context.get('ad_type')
        if not ad_type:
            await update.message.reply_text("❌ خطأ: لم يتم تحديد نوع الإعلان. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        ad_text = context.user_data.get('ad_text') or user_context.get('ad_text')
        admin_id = update.message.from_user.id
        
        file_id = None
        file_type = None
        file_name = None
        mime_type = None
        
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            file_type = 'photo'
        elif update.message.document:
            file_id = update.message.document.file_id
            file_type = 'document'
            file_name = update.message.document.file_name
            mime_type = update.message.document.mime_type
            
            # إذا كان الملف هو VCF، غيّر نوع الإعلان إلى contact
            if file_name and file_name.lower().endswith(('.vcf', '.vcard')):
                ad_type = 'contact'
            elif mime_type and 'vcard' in mime_type.lower():
                ad_type = 'contact'
                
        elif update.message.contact:
            contact = update.message.contact
            vcf_content = self.create_vcf_from_contact(contact)
            
            if vcf_content:
                try:
                    os.makedirs("ads", exist_ok=True)
                    # اسم الملف الثابت: تسوي سكليف صحتي واتساب.vcf
                    file_path = "ads/تسوي سكليف صحتي واتساب.vcf"
                    
                    # إذا كان الملف موجوداً، أضف رقم نسخة
                    counter = 1
                    if os.path.exists(file_path):
                        base_name = "تسوي سكليف صحتي واتساب"
                        while os.path.exists(f"ads/{base_name}_{counter}.vcf"):
                            counter += 1
                        file_path = f"ads/{base_name}_{counter}.vcf"
                    
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(vcf_content)
                    
                    success = self.db.add_ad('contact', None, file_path, 'contact', admin_id)
                    if success:
                        keyboard = [[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="back_to_ads")]]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        await update.message.reply_text("✅ تم إضافة جهة الاتصال بنجاح", reply_markup=reply_markup)
                    else:
                        await update.message.reply_text("❌ فشل إضافة جهة الاتصال")
                    
                    user_context['conversation_active'] = False
                    context.user_data['conversation_active'] = False
                    return ConversationHandler.END
                except Exception as e:
                    logger.error(f"خطأ في حفظ جهة الاتصال: {str(e)}")
                    await update.message.reply_text("❌ حدث خطأ أثناء حفظ جهة الاتصال")
                    return ConversationHandler.END
        
        if file_id:
            try:
                os.makedirs("ads", exist_ok=True)
                
                file = await context.bot.get_file(file_id)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                
                if ad_type == 'contact':
                    # اسم الملف لجهات الاتصال: تسوي سكليف صحتي واتساب.vcf
                    file_path = "ads/تسوي سكليف صحتي واتساب.vcf"
                    
                    # إذا كان الملف موجوداً، أضف رقم نسخة
                    counter = 1
                    if os.path.exists(file_path):
                        base_name = "تسوي سكليف صحتي واتساب"
                        while os.path.exists(f"ads/{base_name}_{counter}.vcf"):
                            counter += 1
                        file_path = f"ads/{base_name}_{counter}.vcf"
                elif file_type == 'photo':
                    file_path = f"ads/photo_{timestamp}.jpg"
                else:
                    ext = file_name.split('.')[-1] if file_name else 'bin'
                    file_path = f"ads/document_{timestamp}.{ext}"
                
                await file.download_to_drive(file_path)
                
                if ad_type == 'contact':
                    success = self.db.add_ad('contact', None, file_path, 'contact', admin_id)
                    message = "✅ تم إضافة جهة الاتصال بنجاح"
                elif ad_type == 'photo':
                    success = self.db.add_ad('photo', ad_text, file_path, 'photo', admin_id)
                    message = "✅ تم إضافة الإعلان بالصورة بنجاح"
                else:
                    success = False
                    message = "❌ نوع الإعلان غير معروف"
                
                if success:
                    keyboard = [[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="back_to_ads")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await update.message.reply_text(message, reply_markup=reply_markup)
                else:
                    await update.message.reply_text("❌ فشل إضافة الإعلان، حاول مرة أخرى")
                    
            except Exception as e:
                logger.error(f"خطأ في حفظ الملف: {str(e)}")
                await update.message.reply_text("❌ حدث خطأ أثناء حفظ الملف")
        else:
            await update.message.reply_text("❌ لم يتم التعرف على الملف")
        
        user_context['conversation_active'] = False
        context.user_data['conversation_active'] = False
        return ConversationHandler.END
    
    async def show_ads(self, query, context):
        """عرض الإعلانات"""
        admin_id = query.from_user.id
        ads = self.db.get_ads(admin_id)
        
        if not ads:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_ads")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("❌ لا توجد إعلانات مضافة", reply_markup=reply_markup)
            return
        
        text = "📢 **الإعلانات المضافة:**\n\n"
        keyboard = []
        
        for ad in ads:
            ad_id, ad_type, ad_text, media_path, file_type, added_date, ad_admin_id, is_encoded = ad
            type_emoji = {"text": "📝", "photo": "🖼️", "contact": "📞"}

            text += f"**#{ad_id}** - {type_emoji.get(ad_type, '📄')} {ad_type}\n"
            
            if ad_type == 'text' and ad_text:
                text += f"📋 {ad_text[:50]}...\n"
            elif ad_type == 'photo' and ad_text:
                text += f"📋 {ad_text[:30]}... + صورة\n"
            elif ad_type == 'contact':
                text += f"📞 جهة اتصال (تسوي سكليف صحتي واتساب.vcf)\n"
            
            text += "─" * 20 + "\n"
            
            keyboard.append([InlineKeyboardButton(f"🗑️ حذف #{ad_id}", callback_data=f"delete_ad_{ad_id}")])
        
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_to_ads")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def delete_ad(self, query, context, ad_id):
        """حذف إعلان"""
        admin_id = query.from_user.id
        self.db.delete_ad(ad_id, admin_id)
        await query.edit_message_text(f"✅ تم حذف الإعلان #{ad_id}")
        await self.show_ads(query, context)
    
    # قسم إدارة المجموعات مع دعم المجموعات المجمعة
    async def manage_groups(self, query, context):
        """إدارة المجموعات"""
        keyboard = [
            [InlineKeyboardButton("➕ إضافة مجموعة", callback_data="add_group")],
            [InlineKeyboardButton("📊 عرض المجموعات", callback_data="show_groups")],
            [InlineKeyboardButton("👥 الانضمام للمجموعات", callback_data="start_join_groups")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "👥 **إدارة المجموعات**\n\n"
            "اختر الإجراء الذي تريد تنفيذه:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def add_group_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بدء إضافة مجموعة"""
        user_id = update.callback_query.from_user.id
        user_context = self.get_user_context(user_id)
        user_context['conversation_active'] = True
        
        await update.callback_query.edit_message_text(
            "👥 **إضافة مجموعات**\n\n"
            "أرسل رابط مجموعة أو عدة روابط:\n\n"
            "أو أرسل /cancel للإلغاء",
            parse_mode='Markdown'
        )
        context.user_data['conversation_active'] = True
        return ADD_GROUP
    
    async def add_group_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة رابط المجموعة"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
        if not user_context.get('conversation_active', False) and not context.user_data.get('conversation_active', False):
            await update.message.reply_text("❌ تم إلغاء العملية. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        message_text = update.message.text
        admin_id = update.message.from_user.id
        
        # البحث عن جميع الروابط في النص
        url_pattern = r'(https?://[^\s]+|t\.me/[^\s]+)'
        links = re.findall(url_pattern, message_text)
        
        added_count = 0
        for link in links:
            if 't.me' in link:
                self.db.add_group(link, admin_id)
                added_count += 1
        
        if added_count > 0:
            keyboard = [[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="back_to_groups")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"✅ تم إضافة {added_count} مجموعة\n\n"
                f"سيبدأ البوت بالانضمام إليها تلقائياً خلال 0.5 ثانية فقط بين كل رابط.",
                reply_markup=reply_markup
            )
            
            # بدء عملية الانضمام بعد تأكيد الإضافة
            asyncio.create_task(self.delayed_join_groups(admin_id))
        else:
            await update.message.reply_text("❌ لم يتم إضافة أي مجموعة، تأكد من صحة الروابط")
        
        user_context['conversation_active'] = False
        context.user_data['conversation_active'] = False
        return ConversationHandler.END
    
    async def delayed_join_groups(self, admin_id):
        """بدء الانضمام للمجموعات بعد تأخير"""
        await asyncio.sleep(1)  # انتظار قصير للتأكد من حفظ البيانات
        self.manager.start_join_groups(admin_id)
    
    async def show_groups(self, query, context):
        """عرض المجموعات"""
        admin_id = query.from_user.id
        groups = self.db.get_groups(admin_id)
        bulk_groups = self.db.get_bulk_groups(admin_id)
        
        if not groups and not bulk_groups:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_groups")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("❌ لا توجد مجموعات مضافة", reply_markup=reply_markup)
            return
        
        text = "👥 **المجموعات المضافة:**\n\n"
        
        if bulk_groups:
            text += "**المجموعات المجمعة:**\n"
            for group in bulk_groups:
                group_id, message_id, link, name, status, added_date, group_admin_id = group
                status_emoji = {"pending": "⏳", "joined": "✅", "failed": "❌"}
                
                text += f"**#{group_id}** - {name}\n"
                text += f"🔗 {link}\n"
                text += f"الحالة: {status_emoji.get(status, '❓')} {status}\n"
                text += "─" * 20 + "\n"
        
        if groups:
            if bulk_groups:
                text += "\n**المجموعات الفردية:**\n"
            for group in groups:
                group_id, link, status, join_date, added_date, group_admin_id = group
                status_emoji = {"pending": "⏳", "joined": "✅", "failed": "❌"}
                
                text += f"**#{group_id}** - {link}\n"
                text += f"الحالة: {status_emoji.get(status, '❓')} {status}\n"
                
                if join_date:
                    text += f"تاريخ الانضمام: {join_date}\n"
                
                text += "─" * 20 + "\n"
        
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_groups")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    # قسم إدارة الردود
    async def manage_replies(self, query, context):
        """إدارة الردود"""
        keyboard = [
            [InlineKeyboardButton("💬 الردود في الخاص", callback_data="private_replies")],
            [InlineKeyboardButton("👥 الردود في القروبات", callback_data="group_replies")],
            [InlineKeyboardButton("🗑️ عرض الردود للحذف", callback_data="show_replies")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "💬 **إدارة الردود**\n\n"
            "اختر نوع الردود التي تريد إدارتها:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def show_replies_menu(self, query, context):
        """عرض قائمة حذف الردود"""
        keyboard = [
            [InlineKeyboardButton("🗑️ حذف ردود الخاصة", callback_data="show_private_replies_delete")],
            [InlineKeyboardButton("🗑️ حذف ردود القروبات النصية", callback_data="show_text_replies_delete")],
            [InlineKeyboardButton("🗑️ حذف ردود القروبات مع صور", callback_data="show_photo_replies_delete")],
            [InlineKeyboardButton("🗑️ حذف ردود عشوائية", callback_data="show_random_replies_delete")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_replies")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🗑️ **حذف الردود**\n\n"
            "اختر نوع الردود التي تريد حذفها:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def show_private_replies_delete(self, query, context):
        """عرض الردود الخاصة للحذف"""
        admin_id = query.from_user.id
        replies = self.db.get_private_replies(admin_id)
        
        if not replies:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="show_replies")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("❌ لا توجد ردود خاصة مضافة", reply_markup=reply_markup)
            return
        
        text = "🗑️ **الردود في الخاص للحذف:**\n\n"
        keyboard = []
        
        for reply in replies:
            reply_id, reply_text, is_active, added_date, reply_admin_id, is_encoded = reply
            
            text += f"**#{reply_id}**\n"
            text += f"📝 {reply_text[:50]}...\n"
            text += f"الحالة: {'🟢 نشط' if is_active else '🔴 غير نشط'}\n"
            text += "─" * 20 + "\n"
            
            keyboard.append([InlineKeyboardButton(f"🗑️ حذف #{reply_id}", callback_data=f"delete_private_reply_{reply_id}")])
        
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="show_replies")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def delete_private_reply(self, query, context, reply_id):
        """حذف رد خاص"""
        admin_id = query.from_user.id
        self.db.delete_private_reply(reply_id, admin_id)
        await query.edit_message_text(f"✅ تم حذف الرد الخاص #{reply_id}")
        await self.show_private_replies_delete(query, context)
    
    async def show_text_replies_delete(self, query, context):
        """عرض الردود النصية للحذف"""
        admin_id = query.from_user.id
        replies = self.db.get_group_text_replies(admin_id)
        
        if not replies:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="show_replies")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("❌ لا توجد ردود نصية مضافة", reply_markup=reply_markup)
            return
        
        text = "🗑️ **الردود النصية في القروبات للحذف:**\n\n"
        keyboard = []
        
        for reply in replies:
            reply_id, trigger, reply_text, is_active, added_date, reply_admin_id, is_encoded = reply
            
            text += f"**#{reply_id}** - {trigger}\n"
            text += f"➡️ {reply_text[:30]}...\n"
            text += f"الحالة: {'🟢 نشط' if is_active else '🔴 غير نشط'}\n"
            text += "─" * 20 + "\n"
            
            keyboard.append([InlineKeyboardButton(f"🗑️ حذف #{reply_id}", callback_data=f"delete_text_reply_{reply_id}")])
        
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="show_replies")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def delete_text_reply(self, query, context, reply_id):
        """حذف رد نصي"""
        admin_id = query.from_user.id
        self.db.delete_group_text_reply(reply_id, admin_id)
        await query.edit_message_text(f"✅ تم حذف الرد النصي #{reply_id}")
        await self.show_text_replies_delete(query, context)
    
    async def show_photo_replies_delete(self, query, context):
        """عرض الردود مع الصور للحذف"""
        admin_id = query.from_user.id
        replies = self.db.get_group_photo_replies(admin_id)
        
        if not replies:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="show_replies")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("❌ لا توجد ردود مع صور مضافة", reply_markup=reply_markup)
            return
        
        text = "🗑️ **الردود مع الصور في القروبات للحذف:**\n\n"
        keyboard = []
        
        for reply in replies:
            reply_id, trigger, reply_text, media_path, is_active, added_date, reply_admin_id, is_encoded = reply
            
            text += f"**#{reply_id}** - {trigger}\n"
            text += f"➡️ {reply_text[:30] if reply_text else 'بدون نص'}...\n"
            text += f"🖼️ مع صورة\n"
            text += f"الحالة: {'🟢 نشط' if is_active else '🔴 غير نشط'}\n"
            text += "─" * 20 + "\n"
            
            keyboard.append([InlineKeyboardButton(f"🗑️ حذف #{reply_id}", callback_data=f"delete_photo_reply_{reply_id}")])
        
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="show_replies")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def delete_photo_reply(self, query, context, reply_id):
        """حذف رد مع صورة"""
        admin_id = query.from_user.id
        self.db.delete_group_photo_reply(reply_id, admin_id)
        await query.edit_message_text(f"✅ تم حذف الرد مع الصورة #{reply_id}")
        await self.show_photo_replies_delete(query, context)
    
    async def show_random_replies_delete(self, query, context):
        """عرض الردود العشوائية للحذف"""
        admin_id = query.from_user.id
        replies = self.db.get_group_random_replies(admin_id)
        
        if not replies:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="show_replies")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("❌ لا توجد ردود عشوائية مضافة", reply_markup=reply_markup)
            return
        
        text = "🗑️ **الردود العشوائية في القروبات للحذف:**\n\n"
        keyboard = []
        
        for reply in replies:
            reply_id, reply_text, media_path, is_active, added_date, reply_admin_id, is_encoded, has_media = reply
            
            text += f"**#{reply_id}**\n"
            text += f"🎲 {reply_text[:50] if reply_text else 'رد عشوائي'}...\n"
            text += f"🖼️ {'مع صورة' if has_media else 'نص فقط'}\n"
            text += f"الحالة: {'🟢 نشط' if is_active else '🔴 غير نشط'}\n"
            text += "─" * 20 + "\n"
            
            keyboard.append([InlineKeyboardButton(f"🗑️ حذف #{reply_id}", callback_data=f"delete_random_reply_{reply_id}")])
        
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="show_replies")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def delete_random_reply(self, query, context, reply_id):
        """حذف رد عشوائي"""
        admin_id = query.from_user.id
        self.db.delete_group_random_reply(reply_id, admin_id)
        await query.edit_message_text(f"✅ تم حذف الرد العشوائي #{reply_id}")
        await self.show_random_replies_delete(query, context)
    
    async def manage_private_replies(self, query, context):
        """إدارة الردود الخاصة"""
        admin_id = query.from_user.id
        replies = self.db.get_private_replies(admin_id)
        
        text = "💬 **الردود في الخاص:**\n\n"
        keyboard = []
        
        if replies:
            for reply in replies:
                reply_id, reply_text, is_active, added_date, reply_admin_id, is_encoded = reply
                status = "🟢 نشط" if is_active else "🔴 غير نشط"
                
                text += f"**#{reply_id}**\n"
                text += f"📝 {reply_text[:50]}...\n"
                text += f"الحالة: {status}\n"
                text += "─" * 20 + "\n"
        else:
            text += "❌ لا توجد ردود مضافة\n"
        
        keyboard.append([InlineKeyboardButton("➕ إضافة رد", callback_data="add_private_reply")])
        keyboard.append([InlineKeyboardButton("🚀 بدء الرد في الخاص", callback_data="start_private_reply")])
        keyboard.append([InlineKeyboardButton("⏹️ إيقاف الرد في الخاص", callback_data="stop_private_reply")])
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_to_replies")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def add_private_reply_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بدء إضافة رد خاص"""
        user_id = update.callback_query.from_user.id
        user_context = self.get_user_context(user_id)
        user_context['conversation_active'] = True
        
        await update.callback_query.edit_message_text(
            "💬 **إضافة رد في الخاص**\n\n"
            "أرسل نص الرد الآن:\n\n"
            "أو أرسل /cancel للإلغاء",
            parse_mode='Markdown'
        )
        context.user_data['conversation_active'] = True
        return ADD_PRIVATE_TEXT
    
    async def add_private_reply_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة نص الرد الخاص"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
        if not user_context.get('conversation_active', False) and not context.user_data.get('conversation_active', False):
            await update.message.reply_text("❌ تم إلغاء العملية. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        reply_text = update.message.text
        admin_id = update.message.from_user.id
        
        self.db.add_private_reply(reply_text, admin_id=admin_id)
        
        keyboard = [[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="back_to_private_replies")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text("✅ تم إضافة الرد في الخاص بنجاح", reply_markup=reply_markup)
        
        user_context['conversation_active'] = False
        context.user_data['conversation_active'] = False
        return ConversationHandler.END
    
    async def manage_group_replies(self, query, context):
        """إدارة الردود في القروبات"""
        admin_id = query.from_user.id
        text_replies = self.db.get_group_text_replies(admin_id)
        photo_replies = self.db.get_group_photo_replies(admin_id)
        random_replies = self.db.get_group_random_replies(admin_id)
        
        text = "👥 **الردود في القروبات:**\n\n"
        
        text += "**الردود على رسائل محددة:**\n"
        if text_replies or photo_replies:
            if text_replies:
                for reply in text_replies:
                    reply_id, trigger, reply_text, is_active, added_date, reply_admin_id, is_encoded = reply
                    status = "🟢 نشط" if is_active else "🔴 غير نشط"
                    
                    text += f"**#{reply_id}** - {trigger}\n"
                    text += f"➡️ {reply_text[:30]}...\n"
                    text += f"الحالة: {status}\n"
                    text += "─" * 20 + "\n"
            
            if photo_replies:
                for reply in photo_replies:
                    reply_id, trigger, reply_text, media_path, is_active, added_date, reply_admin_id, is_encoded = reply
                    status = "🟢 نشط" if is_active else "🔴 غير نشط"
                    
                    text += f"**#{reply_id}** - {trigger}\n"
                    text += f"➡️ {reply_text[:30] if reply_text else 'بدون نص'}...\n"
                    text += f"الحالة: {status}\n"
                    text += "─" * 20 + "\n"
        else:
            text += "❌ لا توجد ردود مضافة\n"
        
        text += "\n**الردود العشوائية (100%):**\n"
        if random_replies:
            for reply in random_replies:
                reply_id, reply_text, media_path, is_active, added_date, reply_admin_id, is_encoded, has_media = reply
                status = "🟢 نشط" if is_active else "🔴 غير نشط"
                
                text += f"**#{reply_id}** - {reply_text[:50] if reply_text else 'رد عشوائي'}...\n"
                text += f"🖼️ {'مع صورة' if has_media else 'نص فقط'}\n"
                text += f"الحالة: {status}\n"
                text += "─" * 20 + "\n"
        else:
            text += "❌ لا توجد ردود عشوائية مضافة\n"
        
        keyboard = [
            [InlineKeyboardButton("➕ إضافة رد محدد", callback_data="add_group_text_reply")],
            [InlineKeyboardButton("➕ إضافة رد مع صورة", callback_data="add_group_photo_reply")],
            [InlineKeyboardButton("➕ إضافة رد عشوائي", callback_data="add_random_reply")],
            [InlineKeyboardButton("🚀 بدء الردود المحددة", callback_data="start_group_reply")],
            [InlineKeyboardButton("⏹️ إيقاف الردود المحددة", callback_data="stop_group_reply")],
            [InlineKeyboardButton("🚀 بدء الردود العشوائية", callback_data="start_random_reply")],
            [InlineKeyboardButton("⏹️ إيقاف الردود العشوائية", callback_data="stop_random_reply")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_replies")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def add_group_text_reply_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بدء إضافة رد نصي في القروبات"""
        user_id = update.callback_query.from_user.id
        user_context = self.get_user_context(user_id)
        user_context['conversation_active'] = True
        
        await update.callback_query.edit_message_text(
            "👥 **إضافة رد نصي في القروبات**\n\n"
            "أرسل النص الذي سيتم الرد عليه:\n\n"
            "أو أرسل /cancel للإلغاء",
            parse_mode='Markdown'
        )
        context.user_data['conversation_active'] = True
        return ADD_GROUP_TEXT
    
    async def add_group_text_reply_trigger(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة نص الرد النصي"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
        if not user_context.get('conversation_active', False) and not context.user_data.get('conversation_active', False):
            await update.message.reply_text("❌ تم إلغاء العملية. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        user_context['group_text_trigger'] = update.message.text
        context.user_data['group_text_trigger'] = update.message.text
        
        await update.message.reply_text(
            "👥 **إضافة رد نصي في القروبات**\n\n"
            "أرسل نص الرد الآن:\n\n"
            "أو أرسل /cancel للإلغاء",
            parse_mode='Markdown'
        )
        return ADD_GROUP_TEXT
    
    async def add_group_text_reply_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة نص الرد النصي"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
        if not user_context.get('conversation_active', False) and not context.user_data.get('conversation_active', False):
            await update.message.reply_text("❌ تم إلغاء العملية. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        trigger = user_context.get('group_text_trigger') or context.user_data.get('group_text_trigger')
        reply_text = update.message.text
        admin_id = update.message.from_user.id
        
        if trigger:
            self.db.add_group_text_reply(trigger, reply_text, admin_id=admin_id)
            
            keyboard = [[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="back_to_group_replies")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text("✅ تم إضافة الرد النصي في القروبات بنجاح", reply_markup=reply_markup)
        else:
            await update.message.reply_text("❌ لم يتم تحديد النص المحفز")
        
        user_context['conversation_active'] = False
        context.user_data['conversation_active'] = False
        return ConversationHandler.END
    
    async def add_group_photo_reply_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بدء إضافة رد مع صورة في القروبات"""
        user_id = update.callback_query.from_user.id
        user_context = self.get_user_context(user_id)
        user_context['conversation_active'] = True
        
        await update.callback_query.edit_message_text(
            "👥 **إضافة رد مع صورة في القروبات**\n\n"
            "أرسل النص الذي سيتم الرد عليه:\n\n"
            "أو أرسل /cancel للإلغاء",
            parse_mode='Markdown'
        )
        context.user_data['conversation_active'] = True
        return ADD_GROUP_PHOTO
    
    async def add_group_photo_reply_trigger(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة نص الرد مع صورة"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
        if not user_context.get('conversation_active', False) and not context.user_data.get('conversation_active', False):
            await update.message.reply_text("❌ تم إلغاء العملية. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        user_context['group_photo_trigger'] = update.message.text
        context.user_data['group_photo_trigger'] = update.message.text
        
        await update.message.reply_text(
            "👥 **إضافة رد مع صورة في القروبات**\n\n"
            "أرسل نص الرد الآن (يمكنك تركها فارغة):\n\n"
            "أو أرسل /cancel للإلغاء",
            parse_mode='Markdown'
        )
        return ADD_GROUP_PHOTO
    
    async def add_group_photo_reply_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة نص الرد مع صورة"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
        if not user_context.get('conversation_active', False) and not context.user_data.get('conversation_active', False):
            await update.message.reply_text("❌ تم إلغاء العملية. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        user_context['group_photo_text'] = update.message.text
        context.user_data['group_photo_text'] = update.message.text
        
        await update.message.reply_text(
            "👥 **إضافة رد مع صورة في القروبات**\n\n"
            "أرسل الصورة الآن:\n\n"
            "أو أرسل /cancel للإلغاء",
            parse_mode='Markdown'
        )
        return ADD_GROUP_PHOTO
    
    async def add_group_photo_reply_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة صورة الرد"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
        if not user_context.get('conversation_active', False) and not context.user_data.get('conversation_active', False):
            await update.message.reply_text("❌ تم إلغاء العملية. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        if update.message.photo:
            trigger = user_context.get('group_photo_trigger') or context.user_data.get('group_photo_trigger')
            reply_text = user_context.get('group_photo_text') or context.user_data.get('group_photo_text')
            admin_id = update.message.from_user.id
            
            try:
                os.makedirs("group_replies", exist_ok=True)
                
                file_id = update.message.photo[-1].file_id
                file = await context.bot.get_file(file_id)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                file_path = f"group_replies/photo_{timestamp}.jpg"
                await file.download_to_drive(file_path)
                
                if trigger:
                    self.db.add_group_photo_reply(trigger, reply_text, file_path, admin_id=admin_id)
                    
                    keyboard = [[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="back_to_group_replies")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await update.message.reply_text("✅ تم إضافة الرد مع الصورة في القروبات بنجاح", reply_markup=reply_markup)
                else:
                    await update.message.reply_text("❌ لم يتم تحديد النص المحفز")
            except Exception as e:
                logger.error(f"خطأ في حفظ صورة الرد: {str(e)}")
                await update.message.reply_text("❌ حدث خطأ أثناء حفظ الصورة")
        else:
            await update.message.reply_text("❌ يرجى إرسال صورة صالحة")
            return ADD_GROUP_PHOTO
        
        user_context['conversation_active'] = False
        context.user_data['conversation_active'] = False
        return ConversationHandler.END
    
    async def add_random_reply_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بدء إضافة رد عشوائي"""
        user_id = update.callback_query.from_user.id
        user_context = self.get_user_context(user_id)
        user_context['conversation_active'] = True
        
        await update.callback_query.edit_message_text(
            "🎲 **إضافة رد عشوائي في القروبات**\n\n"
            "أرسل نص الرد العشوائي الآن:\n\n"
            "أو أرسل /cancel للإلغاء",
            parse_mode='Markdown'
        )
        context.user_data['conversation_active'] = True
        return ADD_RANDOM_REPLY
    
    async def add_random_reply_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة نص الرد العشوائي"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
        if not user_context.get('conversation_active', False) and not context.user_data.get('conversation_active', False):
            await update.message.reply_text("❌ تم إلغاء العملية. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        reply_text = update.message.text
        admin_id = update.message.from_user.id
        
        # حفظ النص أولاً
        user_context['random_reply_text'] = reply_text
        context.user_data['random_reply_text'] = reply_text
        
        await update.message.reply_text(
            "🎲 **إضافة رد عشوائي في القروبات**\n\n"
            "هل تريد إضافة صورة مع الرد؟\n"
            "✅ أرسل صورة الآن\n"
            "❌ أو أرسل /skip لتخطي إضافة صورة\n\n"
            "أرسل /cancel للإلغاء",
            parse_mode='Markdown'
        )
        
        # سيتم التعامل مع الصورة في معالج منفصل
        return ADD_RANDOM_REPLY
    
    async def add_random_reply_media(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة وسائط الرد العشوائي"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
        if not user_context.get('conversation_active', False) and not context.user_data.get('conversation_active', False):
            await update.message.reply_text("❌ تم إلغاء العملية. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        reply_text = user_context.get('random_reply_text') or context.user_data.get('random_reply_text')
        admin_id = update.message.from_user.id
        
        media_path = None
        
        if update.message.photo:
            try:
                os.makedirs("random_replies", exist_ok=True)
                
                file_id = update.message.photo[-1].file_id
                file = await context.bot.get_file(file_id)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                media_path = f"random_replies/photo_{timestamp}.jpg"
                await file.download_to_drive(media_path)
            except Exception as e:
                logger.error(f"خطأ في حفظ صورة الرد العشوائي: {str(e)}")
        
        if reply_text:
            self.db.add_group_random_reply(reply_text, media_path, admin_id=admin_id)
            
            keyboard = [[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="back_to_group_replies")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if media_path:
                await update.message.reply_text("✅ تم إضافة الرد العشوائي مع الصورة بنجاح", reply_markup=reply_markup)
            else:
                await update.message.reply_text("✅ تم إضافة الرد العشوائي النصي بنجاح", reply_markup=reply_markup)
        else:
            await update.message.reply_text("❌ لم يتم تحديد نص الرد")
        
        user_context['conversation_active'] = False
        context.user_data['conversation_active'] = False
        return ConversationHandler.END
    
    async def skip_random_reply_media(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """تخطي إضافة وسائط للرد العشوائي"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
        if not user_context.get('conversation_active', False) and not context.user_data.get('conversation_active', False):
            await update.message.reply_text("❌ تم إلغاء العملية. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        reply_text = user_context.get('random_reply_text') or context.user_data.get('random_reply_text')
        admin_id = update.message.from_user.id
        
        if reply_text:
            self.db.add_group_random_reply(reply_text, None, admin_id=admin_id)
            
            keyboard = [[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="back_to_group_replies")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text("✅ تم إضافة الرد العشوائي النصي بنجاح", reply_markup=reply_markup)
        else:
            await update.message.reply_text("❌ لم يتم تحديد نص الرد")
        
        user_context['conversation_active'] = False
        context.user_data['conversation_active'] = False
        return ConversationHandler.END
    
    # قسم إدارة المشرفين
    async def manage_admins(self, query, context):
        """إدارة المشرفين"""
        keyboard = [
            [InlineKeyboardButton("➕ إضافة مشرف", callback_data="add_admin")],
            [InlineKeyboardButton("👨‍💼 عرض المشرفين", callback_data="show_admins")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "👨‍💼 **إدارة المشرفين**\n\n"
            "اختر الإجراء الذي تريد تنفيذه:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def add_admin_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بدء إضافة مشرف"""
        user_id = update.callback_query.from_user.id
        user_context = self.get_user_context(user_id)
        user_context['conversation_active'] = True
        
        await update.callback_query.edit_message_text(
            "👨‍💼 **إضافة مشرف جديد**\n\n"
            "أرسل معرف المستخدم (User ID):\n\n"
            "أو أرسل /cancel للإلغاء",
            parse_mode='Markdown'
        )
        context.user_data['conversation_active'] = True
        return ADD_ADMIN
    
    async def add_admin_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة معرف المشرف"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
        if not user_context.get('conversation_active', False) and not context.user_data.get('conversation_active', False):
            await update.message.reply_text("❌ تم إلغاء العملية. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        try:
            user_id_to_add = int(update.message.text)
            
            username = "يتم إضافته"
            full_name = "مشرف جديد"
            
            result, message = self.db.add_admin(user_id_to_add, username, full_name, False)
            
            keyboard = [[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="back_to_admins")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(f"✅ {message}\n\nتم إضافة المستخدم {user_id_to_add} كمشرف", reply_markup=reply_markup)
                
        except ValueError:
            await update.message.reply_text("❌ معرف المستخدم يجب أن يكون رقماً")
        
        user_context['conversation_active'] = False
        context.user_data['conversation_active'] = False
        return ConversationHandler.END
    
    async def show_admins(self, query, context):
        """عرض المشرفين"""
        admins = self.db.get_admins()
        
        if not admins:
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_admins")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("❌ لا توجد مشرفين مضافة", reply_markup=reply_markup)
            return
        
        text = "👨‍💼 **المشرفين المضافين:**\n\n"
        keyboard = []
        
        for admin in admins:
            admin_id, user_id, username, full_name, added_date, is_super_admin = admin
            role = "🟢 مشرف رئيسي" if is_super_admin else "🔵 مشرف عادي"
            
            text += f"**#{admin_id}** - {full_name}\n"
            text += f"المعرف: {user_id} | {username}\n"
            text += f"الدور: {role}\n"
            text += "─" * 20 + "\n"
            
            keyboard.append([InlineKeyboardButton(f"🗑️ حذف #{admin_id}", callback_data=f"delete_admin_{admin_id}")])
        
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_to_admins")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def delete_admin(self, query, context, admin_id):
        """حذف مشرف"""
        self.db.delete_admin(admin_id)
        await query.edit_message_text(f"✅ تم حذف المشرف #{admin_id}")
        await self.show_admins(query, context)
    
    def setup_handlers(self):
        """إعداد معالجات البوت"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("cancel", self.cancel))
        
        # معالجات المحادثة
        add_account_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_account_start, pattern="^add_account$")],
            states={
                ADD_ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_account_session)]
            },
            fallbacks=[CommandHandler("cancel", self.cancel)]
        )
        self.application.add_handler(add_account_conv)
        
        add_ad_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.handle_callback, pattern="^ad_type_")],
            states={
                ADD_AD_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_ad_text)],
                ADD_AD_MEDIA: [
                    MessageHandler(filters.PHOTO, self.add_ad_media),
                    MessageHandler(filters.Document.ALL, self.add_ad_media),
                    MessageHandler(filters.CONTACT, self.add_ad_media)
                ]
            },
            fallbacks=[CommandHandler("cancel", self.cancel)]
        )
        self.application.add_handler(add_ad_conv)
        
        add_group_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_group_start, pattern="^add_group$")],
            states={
                ADD_GROUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_group_link)]
            },
            fallbacks=[CommandHandler("cancel", self.cancel)]
        )
        self.application.add_handler(add_group_conv)
        
        add_admin_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_admin_start, pattern="^add_admin$")],
            states={
                ADD_ADMIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_admin_id)]
            },
            fallbacks=[CommandHandler("cancel", self.cancel)]
        )
        self.application.add_handler(add_admin_conv)
        
        private_reply_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_private_reply_start, pattern="^add_private_reply$")],
            states={
                ADD_PRIVATE_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_private_reply_text)]
            },
            fallbacks=[CommandHandler("cancel", self.cancel)]
        )
        self.application.add_handler(private_reply_conv)
        
        group_text_reply_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_group_text_reply_start, pattern="^add_group_text_reply$")],
            states={
                ADD_GROUP_TEXT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_group_text_reply_trigger),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_group_text_reply_text)
                ]
            },
            fallbacks=[CommandHandler("cancel", self.cancel)]
        )
        self.application.add_handler(group_text_reply_conv)
        
        group_photo_reply_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_group_photo_reply_start, pattern="^add_group_photo_reply$")],
            states={
                ADD_GROUP_PHOTO: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_group_photo_reply_trigger),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_group_photo_reply_text),
                    MessageHandler(filters.PHOTO, self.add_group_photo_reply_photo)
                ]
            },
            fallbacks=[CommandHandler("cancel", self.cancel)]
        )
        self.application.add_handler(group_photo_reply_conv)
        
        random_reply_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_random_reply_start, pattern="^add_random_reply$")],
            states={
                ADD_RANDOM_REPLY: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_random_reply_text),
                    MessageHandler(filters.PHOTO, self.add_random_reply_media),
                    CommandHandler("skip", self.skip_random_reply_media)
                ]
            },
            fallbacks=[CommandHandler("cancel", self.cancel)]
        )
        self.application.add_handler(random_reply_conv)
        
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
    
    def run(self):
        """تشغيل البوت"""
        self.application = Application.builder().token(BOT_TOKEN).build()
        self.setup_handlers()
        
        # إضافة مشرف رئيسي
        try:
            self.db.add_admin(8294336757, "@user", "المشرف الرئيسي", True)
            print(f"✅ تم إضافة الآيدي 8294336757 كمشرف رئيسي")
        except:
            print(f"⚠️  الآيدي 8294336757 مضاف مسبقاً كمشرف رئيسي")
        
        print("🚀 **بوت النشر الفعلي - السرعة القصوى يعمل الآن!**")
        print("✅ تم تعديل السرعات لتصبح أقصى ما يمكن:")
        print("   ⚡ النشر: 0.1 ثانية بين الإعلانات")
        print("   ⚡ النشر: 0.2 ثانية بين المجموعات")
        print("   ⚡ النشر: 10 ثواني بين الدورات")
        print("   ⚡ الرد الخاص: 0.05 ثانية بين الردود")
        print("   ⚡ الرد الخاص: 3 ثواني بين الدورات")
        print("   ⚡ الرد في القروبات: 0.05 ثانية بين الردود")
        print("   ⚡ الرد العشوائي: 0.05 ثانية بين الردود")
        print("   ⚡ الانضمام للمجموعات: 0.5 ثانية بين الروابط")
        print("   ⚡ الانضمام للمجموعات: 5 ثواني بين الدورات")
        print("   📁 اسم ملف جهات الاتصال: تسوي سكليف صحتي واتساب.vcf")
        
        self.application.run_polling()

# الجزء الأخير من الكود
if __name__ == "__main__":
    # بدء خادم HTTP في خيط منفصل
    http_thread = threading.Thread(target=run_health_server, daemon=True)
    http_thread.start()
    
    # إنشاء المجلدات المطلوبة
    os.makedirs("ads", exist_ok=True)
    os.makedirs("group_replies", exist_ok=True)
    os.makedirs("random_replies", exist_ok=True)
    
    # تشغيل البوت
    try:
        bot = BotHandler()
        print("🚀 Starting Telegram Bot with Maximum Speed Publishing...")
        bot.run()
    except Exception as e:
        print(f"❌ Error: {e}")
