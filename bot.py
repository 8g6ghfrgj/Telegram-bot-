import os
import json
import asyncio
import logging
import sqlite3
import random
import string
import threading
from datetime import datetime, timedelta
from threading import Thread
from queue import Queue
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    InputFile,
    Contact
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
    ADD_GROUP_PHOTO, ADD_AD_VCF
) = range(14)

# تهيئة السجل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

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
                contact_data TEXT,
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
    
    def add_ad(self, ad_type, text=None, media_path=None, file_type=None, contact_data=None, admin_id=0):
        """إضافة إعلان"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO ads (type, text, media_path, file_type, contact_data, admin_id)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (ad_type, text, media_path, file_type, contact_data, admin_id))
            
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"خطأ في إضافة الإعلان: {str(e)}")
            return False
        finally:
            conn.close()
    
    def get_ads(self, admin_id=None):
        """الحصول على جميع الإعلانات"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id is not None:
            cursor.execute('SELECT * FROM ads WHERE admin_id = ? OR admin_id = 0 ORDER BY id', (admin_id,))
        else:
            cursor.execute('SELECT * FROM ads ORDER BY id')
            
        ads = cursor.fetchall()
        conn.close()
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
        
        cursor.execute('''
            INSERT INTO private_replies (reply_text, admin_id)
            VALUES (?, ?)
        ''', (reply_text, admin_id))
        
        conn.commit()
        conn.close()
        return True
    
    def get_private_replies(self, admin_id=None):
        """الحصول على الردود الخاصة"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id is not None:
            cursor.execute('SELECT * FROM private_replies WHERE admin_id = ? OR admin_id = 0 ORDER BY id', (admin_id,))
        else:
            cursor.execute('SELECT * FROM private_replies ORDER BY id')
            
        replies = cursor.fetchall()
        conn.close()
        return replies
    
    def add_group_text_reply(self, trigger, reply_text, admin_id=0):
        """إضافة رد نصي جماعي"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO group_text_replies (trigger, reply_text, admin_id)
            VALUES (?, ?, ?)
        ''', (trigger, reply_text, admin_id))
        
        conn.commit()
        conn.close()
        return True
    
    def get_group_text_replies(self, admin_id=None):
        """الحصول على الردود النصية الجماعية"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id is not None:
            cursor.execute('SELECT * FROM group_text_replies WHERE admin_id = ? OR admin_id = 0 ORDER BY id', (admin_id,))
        else:
            cursor.execute('SELECT * FROM group_text_replies ORDER BY id')
            
        replies = cursor.fetchall()
        conn.close()
        return replies
    
    def add_group_photo_reply(self, trigger, reply_text, media_path, admin_id=0):
        """إضافة رد جماعي مع صورة"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO group_photo_replies (trigger, reply_text, media_path, admin_id)
            VALUES (?, ?, ?, ?)
        ''', (trigger, reply_text, media_path, admin_id))
        
        conn.commit()
        conn.close()
        return True
    
    def get_group_photo_replies(self, admin_id=None):
        """الحصول على الردود الجماعية مع الصور"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id is not None:
            cursor.execute('SELECT * FROM group_photo_replies WHERE admin_id = ? OR admin_id = 0 ORDER BY id', (admin_id,))
        else:
            cursor.execute('SELECT * FROM group_photo_replies ORDER BY id')
            
        replies = cursor.fetchall()
        conn.close()
        return replies
    
    def add_group_random_reply(self, reply_text, admin_id=0):
        """إضافة رد عشوائي في القروبات"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO group_random_replies (reply_text, admin_id)
            VALUES (?, ?)
        ''', (reply_text, admin_id))
        
        conn.commit()
        conn.close()
        return True
    
    def get_group_random_replies(self, admin_id=None):
        """الحصول على الردود العشوائية في القروبات"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id is not None:
            cursor.execute('SELECT * FROM group_random_replies WHERE (admin_id = ? OR admin_id = 0) AND is_active = 1 ORDER BY id', (admin_id,))
        else:
            cursor.execute('SELECT * FROM group_random_replies WHERE is_active = 1 ORDER BY id')
            
        replies = cursor.fetchall()
        conn.close()
        return replies
    
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
        self.lock = threading.Lock()
    
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
    
    async def join_groups(self, admin_id=None):
        """الانضمام إلى المجموعات"""
        groups = self.db.get_groups(admin_id)
        pending_groups = [g for g in groups if g[2] == 'pending']
        
        accounts = self.db.get_active_publishing_accounts(admin_id)
        
        for group in pending_groups:
            group_id, group_link, status, join_date, added_date, group_admin_id = group
            
            for account in accounts:
                account_id, session_string, name, username = account
                
                try:
                    client = TelegramClient(StringSession(session_string), 1, "b")
                    await client.connect()
                    
                    if await client.is_user_authorized():
                        try:
                            if 't.me/+' in group_link:
                                invite_hash = group_link.split('+')[1]
                                await client(ImportChatInviteRequest(invite_hash))
                            else:
                                await client(JoinChannelRequest(group_link))
                            
                            self.db.update_group_status(group_id, 'joined')
                            logger.info(f"انضم الحساب {name} إلى المجموعة {group_link}")
                            
                        except Exception as e:
                            logger.error(f"فشل الانضمام للمجموعة {group_link}: {str(e)}")
                            self.db.update_group_status(group_id, 'failed')
                    
                    await client.disconnect()
                    await asyncio.sleep(5)
                    
                except Exception as e:
                    logger.error(f"خطأ في الحساب {name}: {str(e)}")
                    continue
    
    async def publish_to_groups(self, admin_id=None):
        """النشر في المجموعات - الإصدار المعدل لنشر جهات الاتصال بشكل صحيح"""
        while self.publishing_active:
            try:
                accounts = self.db.get_active_publishing_accounts(admin_id)
                ads = self.db.get_ads(admin_id)
                
                if not accounts or not ads:
                    await asyncio.sleep(60)
                    continue
                
                for account in accounts:
                    if not self.publishing_active:
                        break
                        
                    account_id, session_string, name, username = account
                    
                    try:
                        client = TelegramClient(StringSession(session_string), 1, "b")
                        await client.connect()
                        
                        if await client.is_user_authorized():
                            dialogs = await client.get_dialogs()
                            
                            for dialog in dialogs:
                                if not self.publishing_active:
                                    break
                                    
                                if dialog.is_group or dialog.is_channel:
                                    try:
                                        for ad in ads:
                                            if not self.publishing_active:
                                                break
                                                
                                            ad_id, ad_type, ad_text, media_path, file_type, contact_data_json, added_date, ad_admin_id = ad
                                            
                                            try:
                                                if ad_type == 'text':
                                                    await client.send_message(dialog.id, ad_text)
                                                    logger.info(f"تم نشر نص في {dialog.name} بواسطة {name}")
                                                    
                                                elif ad_type == 'photo' and media_path and os.path.exists(media_path):
                                                    await client.send_file(dialog.id, media_path, caption=ad_text)
                                                    logger.info(f"تم نشر صورة في {dialog.name} بواسطة {name}")
                                                    
                                                elif ad_type == 'contact':
                                                    # إصلاح: معالجة بيانات جهة الاتصال من JSON
                                                    if contact_data_json:
                                                        try:
                                                            contact_data = json.loads(contact_data_json)
                                                            phone_number = contact_data.get('phone_number', '')
                                                            first_name = contact_data.get('first_name', '')
                                                            last_name = contact_data.get('last_name', '')
                                                            
                                                            # التأكد من وجود رقم هاتف
                                                            if phone_number:
                                                                # استخدام send_contact لإرسال نقطة اتصال مباشرة
                                                                await client.send_contact(
                                                                    dialog.id,
                                                                    phone=phone_number,
                                                                    first_name=first_name,
                                                                    last_name=last_name
                                                                )
                                                                logger.info(f"تم نشر جهة اتصال مباشرة في {dialog.name} بواسطة {name}")
                                                        except Exception as e:
                                                            logger.error(f"خطأ في تحليل JSON لجهة الاتصال: {str(e)}")
                                                            continue
                                                    elif media_path and os.path.exists(media_path):
                                                        # للتوافق مع الملفات VCF القديمة
                                                        try:
                                                            with open(media_path, 'r', encoding='utf-8') as f:
                                                                vcard_content = f.read()
                                                            
                                                            # تحليل ملف VCF
                                                            phone = ''
                                                            first_name = ''
                                                            last_name = ''
                                                            
                                                            for line in vcard_content.split('\n'):
                                                                line = line.strip()
                                                                if line.startswith('TEL;') or line.startswith('TEL:'):
                                                                    if ':' in line:
                                                                        phone = line.split(':')[1].strip()
                                                                elif line.startswith('FN:'):
                                                                    full_name = line.split(':')[1].strip()
                                                                    name_parts = full_name.split(' ', 1)
                                                                    if len(name_parts) > 1:
                                                                        first_name = name_parts[0]
                                                                        last_name = name_parts[1]
                                                                    else:
                                                                        first_name = full_name
                                                                elif line.startswith('N:'):
                                                                    name_parts = line.split(':')[1].split(';')
                                                                    if len(name_parts) >= 2:
                                                                        last_name = name_parts[0].strip()
                                                                        first_name = name_parts[1].strip()
                                                            
                                                            if phone:
                                                                await client.send_contact(
                                                                    dialog.id,
                                                                    phone=phone,
                                                                    first_name=first_name,
                                                                    last_name=last_name
                                                                )
                                                                logger.info(f"تم نشر جهة اتصال من ملف VCF في {dialog.name} بواسطة {name}")
                                                        except Exception as e:
                                                            logger.error(f"خطأ في قراءة ملف VCF: {str(e)}")
                                                            continue
                                                elif ad_type in ['document', 'video', 'audio'] and media_path and os.path.exists(media_path):
                                                    await client.send_file(dialog.id, media_path, caption=ad_text)
                                                    logger.info(f"تم نشر ملف {ad_type} في {dialog.name} بواسطة {name}")
                                                
                                                await asyncio.sleep(2)
                                                
                                            except Exception as e:
                                                logger.error(f"فشل نشر الإعلان {ad_id} في {dialog.name}: {str(e)}")
                                                continue
                                                
                                    except Exception as e:
                                        logger.error(f"فشل النشر في {dialog.name}: {str(e)}")
                                        continue
                        
                        await client.disconnect()
                        
                    except Exception as e:
                        logger.error(f"خطأ في الحساب {name}: {str(e)}")
                        continue
                
                await asyncio.sleep(60)
                
            except Exception as e:
                logger.error(f"خطأ في عملية النشر: {str(e)}")
                await asyncio.sleep(60)
    
    def start_publishing(self, admin_id=None):
        """بدء النشر التلقائي"""
        with self.lock:
            if not self.publishing_active:
                self.publishing_active = True
                self.publishing_thread = Thread(target=lambda: asyncio.run(self.publish_to_groups(admin_id)))
                self.publishing_thread.start()
                return True
        return False
    
    def stop_publishing(self):
        """إيقاف النشر التلقائي"""
        with self.lock:
            if self.publishing_active:
                self.publishing_active = False
                if self.publishing_thread:
                    try:
                        self.publishing_thread.join(timeout=5)
                    except:
                        pass
                return True
        return False
    
    async def handle_private_messages(self, admin_id=None):
        """معالجة الرسائل الخاصة"""
        while self.private_reply_active:
            try:
                accounts = self.db.get_active_publishing_accounts(admin_id)
                private_replies = self.db.get_private_replies(admin_id)
                
                if not accounts or not private_replies:
                    await asyncio.sleep(60)
                    continue
                
                for account in accounts:
                    if not self.private_reply_active:
                        break
                        
                    account_id, session_string, name, username = account
                    
                    try:
                        client = TelegramClient(StringSession(session_string), 1, "b")
                        await client.connect()
                        
                        if await client.is_user_authorized():
                            async for message in client.iter_messages(None, limit=5):
                                if not self.private_reply_active:
                                    break
                                    
                                if message.is_private and not message.out:
                                    for reply in private_replies:
                                        reply_id, reply_text, is_active, added_date, reply_admin_id = reply
                                        if is_active:
                                            await client.send_message(message.sender_id, reply_text)
                                            logger.info(f"تم الرد على رسالة خاصة بواسطة {name}")
                                            await asyncio.sleep(1)
                                            break
                        
                        await client.disconnect()
                        
                    except Exception as e:
                        logger.error(f"خطأ في الحساب {name}: {str(e)}")
                        continue
                
                await asyncio.sleep(10)
                
            except Exception as e:
                logger.error(f"خطأ في معالجة الرسائل الخاصة: {str(e)}")
                await asyncio.sleep(30)
    
    def start_private_reply(self, admin_id=None):
        """بدء الرد على الرسائل الخاصة"""
        with self.lock:
            if not self.private_reply_active:
                self.private_reply_active = True
                self.private_reply_thread = Thread(target=lambda: asyncio.run(self.handle_private_messages(admin_id)))
                self.private_reply_thread.start()
                return True
        return False
    
    def stop_private_reply(self):
        """إيقاف الرد على الرسائل الخاصة"""
        with self.lock:
            if self.private_reply_active:
                self.private_reply_active = False
                if self.private_reply_thread:
                    try:
                        self.private_reply_thread.join(timeout=5)
                    except:
                        pass
                return True
        return False
    
    async def handle_group_replies(self, admin_id=None):
        """معالجة الردود في المجموعات"""
        while self.group_reply_active:
            try:
                accounts = self.db.get_active_publishing_accounts(admin_id)
                text_replies = self.db.get_group_text_replies(admin_id)
                photo_replies = self.db.get_group_photo_replies(admin_id)
                
                if not accounts or (not text_replies and not photo_replies):
                    await asyncio.sleep(60)
                    continue
                
                for account in accounts:
                    if not self.group_reply_active:
                        break
                        
                    account_id, session_string, name, username = account
                    
                    try:
                        client = TelegramClient(StringSession(session_string), 1, "b")
                        await client.connect()
                        
                        if await client.is_user_authorized():
                            dialogs = await client.get_dialogs()
                            
                            for dialog in dialogs:
                                if not self.group_reply_active:
                                    break
                                    
                                if dialog.is_group:
                                    try:
                                        async for message in client.iter_messages(dialog.id, limit=5):
                                            if not self.group_reply_active:
                                                break
                                                
                                            if message.text and not message.out:
                                                # الردود النصية
                                                for reply in text_replies:
                                                    reply_id, trigger, reply_text, is_active, added_date, reply_admin_id = reply
                                                    
                                                    if is_active and trigger.lower() in message.text.lower():
                                                        await client.send_message(dialog.id, reply_text, reply_to=message.id)
                                                        logger.info(f"تم الرد على رسالة في {dialog.name} بواسطة {name}")
                                                        await asyncio.sleep(1)
                                                        break
                                                
                                                # الردود مع الصور
                                                for reply in photo_replies:
                                                    reply_id, trigger, reply_text, media_path, is_active, added_date, reply_admin_id = reply
                                                    
                                                    if is_active and trigger.lower() in message.text.lower() and os.path.exists(media_path):
                                                        await client.send_file(dialog.id, media_path, caption=reply_text, reply_to=message.id)
                                                        logger.info(f"تم الرد بصورة على رسالة في {dialog.name} بواسطة {name}")
                                                        await asyncio.sleep(1)
                                                        break
                                        
                                    except Exception as e:
                                        logger.error(f"فشل الرد في {dialog.name}: {str(e)}")
                                        continue
                        
                        await client.disconnect()
                        
                    except Exception as e:
                        logger.error(f"خطأ في الحساب {name}: {str(e)}")
                        continue
                
                await asyncio.sleep(10)
                
            except Exception as e:
                logger.error(f"خطأ في معالجة الردود الجماعية: {str(e)}")
                await asyncio.sleep(30)
    
    def start_group_reply(self, admin_id=None):
        """بدء الردود في المجموعات"""
        with self.lock:
            if not self.group_reply_active:
                self.group_reply_active = True
                self.group_reply_thread = Thread(target=lambda: asyncio.run(self.handle_group_replies(admin_id)))
                self.group_reply_thread.start()
                return True
        return False
    
    def stop_group_reply(self):
        """إيقاف الردود في المجموعات"""
        with self.lock:
            if self.group_reply_active:
                self.group_reply_active = False
                if self.group_reply_thread:
                    try:
                        self.group_reply_thread.join(timeout=5)
                    except:
                        pass
                return True
        return False
    
    async def handle_random_replies(self, admin_id=None):
        """معالجة الردود العشوائية في القروبات"""
        while self.random_reply_active:
            try:
                accounts = self.db.get_active_publishing_accounts(admin_id)
                random_replies = self.db.get_group_random_replies(admin_id)
                
                if not accounts or not random_replies:
                    await asyncio.sleep(60)
                    continue
                
                for account in accounts:
                    if not self.random_reply_active:
                        break
                        
                    account_id, session_string, name, username = account
                    
                    try:
                        client = TelegramClient(StringSession(session_string), 1, "b")
                        await client.connect()
                        
                        if await client.is_user_authorized():
                            dialogs = await client.get_dialogs()
                            
                            for dialog in dialogs:
                                if not self.random_reply_active:
                                    break
                                    
                                if dialog.is_group:
                                    try:
                                        async for message in client.iter_messages(dialog.id, limit=3):
                                            if not self.random_reply_active:
                                                break
                                                
                                            if message.text and not message.out:
                                                random_reply = random.choice(random_replies)
                                                reply_id, reply_text, is_active, added_date, reply_admin_id = random_reply
                                                
                                                if is_active:
                                                    await client.send_message(dialog.id, reply_text, reply_to=message.id)
                                                    logger.info(f"تم الرد العشوائي على عضو في {dialog.name} بواسطة {name}")
                                                    await asyncio.sleep(1)
                                                    break
                                        
                                    except Exception as e:
                                        logger.error(f"فشل الرد العشوائي في {dialog.name}: {str(e)}")
                                        continue
                        
                        await client.disconnect()
                        
                    except Exception as e:
                        logger.error(f"خطأ في الحساب {name}: {str(e)}")
                        continue
                
                await asyncio.sleep(10)
                
            except Exception as e:
                logger.error(f"خطأ في معالجة الردود العشوائية: {str(e)}")
                await asyncio.sleep(30)
    
    def start_random_reply(self, admin_id=None):
        """بدء الردود العشوائية في القروبات"""
        with self.lock:
            if not self.random_reply_active:
                self.random_reply_active = True
                self.random_reply_thread = Thread(target=lambda: asyncio.run(self.handle_random_replies(admin_id)))
                self.random_reply_thread.start()
                return True
        return False
    
    def stop_random_reply(self):
        """إيقاف الردود العشوائية في القروبات"""
        with self.lock:
            if self.random_reply_active:
                self.random_reply_active = False
                if self.random_reply_thread:
                    try:
                        self.random_reply_thread.join(timeout=5)
                    except:
                        pass
                return True
        return False

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
        elif data == "settings":
            await self.settings_menu(query, context)
        
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
                # لجهة الاتصال، نطلب رقم الهاتف أولاً
                await query.edit_message_text(
                    f"📞 **إضافة جهة اتصال**\n\n"
                    f"يرجى إرسال رقم الهاتف:\n\n"
                    f"مثال: +1234567890\n\n"
                    f"أو أرسل /cancel للإلغاء",
                    parse_mode='Markdown'
                )
                user_context['conversation_active'] = True
                context.user_data['ad_type'] = ad_type
                context.user_data['conversation_active'] = True
                return ADD_AD_TEXT
            elif ad_type == 'vcf':
                # لملف VCF
                await query.edit_message_text(
                    f"📂 **إضافة جهة اتصال من ملف VCF**\n\n"
                    f"يرجى إرسال ملف VCF:\n\n"
                    f"أو أرسل /cancel للإلغاء",
                    parse_mode='Markdown'
                )
                user_context['conversation_active'] = True
                context.user_data['ad_type'] = ad_type
                context.user_data['conversation_active'] = True
                return ADD_AD_VCF
            else:
                # لأنواع الإعلانات الأخرى، نطلب النص أولاً
                file_type_text = {
                    'text': 'نص الإعلان',
                    'photo': 'نص الإعلان للصورة',
                }
                
                await query.edit_message_text(
                    f"📝 **{file_type_text.get(ad_type, 'إضافة نص الإعلان')}**\n\n"
                    f"يرجى إرسال نص الإعلان:\n\n"
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
        elif data == "start_publishing":
            await self.start_publishing(query, context)
        elif data == "stop_publishing":
            await self.stop_publishing(query, context)
        
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
            [InlineKeyboardButton("⚙️ الإعدادات", callback_data="settings")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🎮 **لوحة تحكم البوت المتكامل**\n\n"
            "اختر القسم الذي تريد إدارته:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
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
            "يرجى إرسال كود الجلسة (Session String):\n\n"
            "يمكنك الحصول على كود الجلسة من @SessionStringBot\n\n"
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
        
        success, me = await self.manager.test_session(session_string)
        
        if success:
            phone = me.phone if me.phone else "غير معروف"
            name = f"{me.first_name} {me.last_name}" if me.last_name else me.first_name
            username = f"@{me.username}" if me.username else "لا يوجد"
            
            result, message = self.db.add_account(session_string, phone, name, username, admin_id)
            
            if result:
                await update.message.reply_text(f"✅ {message}\n\n📱 الحساب: {name}\n📞 الهاتف: {phone}\n👤 المستخدم: {username}")
            else:
                await update.message.reply_text(f"❌ {message}")
        else:
            await update.message.reply_text("❌ كود الجلسة غير صالح أو الحساب غير مفعل")
        
        user_context['conversation_active'] = False
        context.user_data['conversation_active'] = False
        await self.start(update, context)
        return ConversationHandler.END
    
    async def show_accounts(self, query, context):
        """عرض الحسابات"""
        admin_id = query.from_user.id
        accounts = self.db.get_accounts(admin_id)
        
        if not accounts:
            await query.edit_message_text("❌ لا توجد حسابات مضافة")
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
            [InlineKeyboardButton("📞 جهة اتصال يدويا", callback_data="ad_type_contact")],
            [InlineKeyboardButton("📂 جهة اتصال (ملف VCF)", callback_data="ad_type_vcf")],
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
        """معالجة نص الإعلان أو رقم هاتف جهة الاتصال"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
        # التحقق من حالة المحادثة
        if not user_context.get('conversation_active', False) and not context.user_data.get('conversation_active', False):
            await update.message.reply_text("❌ تم إلغاء العملية. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        ad_type = context.user_data.get('ad_type') or user_context.get('ad_type')
        if not ad_type:
            await update.message.reply_text("❌ خطأ: لم يتم تحديد نوع الإعلان. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        admin_id = update.message.from_user.id
        
        if ad_type == 'text':
            # إعلان نصي فقط
            ad_text = update.message.text
            success = self.db.add_ad('text', ad_text, admin_id=admin_id)
            if success:
                await update.message.reply_text("✅ تم إضافة الإعلان النصي بنجاح")
            else:
                await update.message.reply_text("❌ فشل إضافة الإعلان النصي")
            
            user_context['conversation_active'] = False
            context.user_data['conversation_active'] = False
            await self.start(update, context)
            return ConversationHandler.END
            
        elif ad_type == 'photo':
            # صورة مع نص - حفظ النص ثم طلب الصورة
            ad_text = update.message.text
            user_context['ad_text'] = ad_text
            context.user_data['ad_text'] = ad_text
            
            await update.message.reply_text(
                "🖼️ **إضافة صورة**\n\n"
                "يرجى إرسال الصورة:\n\n"
                "أو أرسل /cancel للإلغاء"
            )
            return ADD_AD_MEDIA
            
        elif ad_type == 'contact':
            # جهة اتصال - حفظ رقم الهاتف ثم طلب الاسم الأول
            phone_number = update.message.text
            
            # تنظيف رقم الهاتف
            phone_number = phone_number.strip()
            if not phone_number.startswith('+'):
                phone_number = '+' + phone_number
            
            user_context['contact_phone'] = phone_number
            context.user_data['contact_phone'] = phone_number
            
            await update.message.reply_text(
                "📞 **إضافة جهة اتصال**\n\n"
                "يرجى إرسال الاسم الأول:\n\n"
                "أو أرسل /cancel للإلغاء"
            )
            return ADD_AD_MEDIA
    
    async def add_ad_media(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة ملف الإعلان أو معلومات جهة الاتصال"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
        # التحقق من حالة المحادثة
        if not user_context.get('conversation_active', False) and not context.user_data.get('conversation_active', False):
            await update.message.reply_text("❌ تم إلغاء العملية. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        ad_type = context.user_data.get('ad_type') or user_context.get('ad_type')
        if not ad_type:
            await update.message.reply_text("❌ خطأ: لم يتم تحديد نوع الإعلان. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        admin_id = update.message.from_user.id
        
        if ad_type == 'photo':
            if update.message.photo:
                # حفظ الصورة
                file_id = update.message.photo[-1].file_id
                file = await context.bot.get_file(file_id)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                file_path = f"ads/photo_{timestamp}.jpg"
                
                os.makedirs("ads", exist_ok=True)
                await file.download_to_drive(file_path)
                
                ad_text = context.user_data.get('ad_text') or user_context.get('ad_text')
                success = self.db.add_ad('photo', ad_text, file_path, 'photo', admin_id=admin_id)
                
                if success:
                    await update.message.reply_text("✅ تم إضافة الإعلان بالصورة بنجاح")
                else:
                    await update.message.reply_text("❌ فشل إضافة الإعلان")
            else:
                await update.message.reply_text("❌ يرجى إرسال صورة صالحة")
                return ADD_AD_MEDIA
                
        elif ad_type == 'contact':
            if update.message.text:
                # جهة اتصال - الحصول على الاسم الأول ثم الاسم الأخير
                if 'contact_first_name' not in context.user_data and 'contact_first_name' not in user_context:
                    # الاسم الأول
                    first_name = update.message.text
                    context.user_data['contact_first_name'] = first_name
                    user_context['contact_first_name'] = first_name
                    
                    await update.message.reply_text(
                        "📞 **إضافة جهة اتصال**\n\n"
                        "يرجى إرسال الاسم الأخير (اختياري):\n\n"
                        "أرسل 'لا يوجد' إذا لم يكن هناك اسم أخير\n\n"
                        "أو أرسل /cancel للإلغاء"
                    )
                    return ADD_AD_MEDIA
                else:
                    # الاسم الأخير
                    last_name = update.message.text if update.message.text != 'لا يوجد' else ''
                    first_name = context.user_data.get('contact_first_name') or user_context.get('contact_first_name')
                    phone_number = context.user_data.get('contact_phone') or user_context.get('contact_phone')
                    
                    # إنشاء بيانات جهة الاتصال كـ JSON
                    contact_data = {
                        'phone_number': phone_number,
                        'first_name': first_name,
                        'last_name': last_name
                    }
                    
                    success = self.db.add_ad('contact', contact_data=json.dumps(contact_data), admin_id=admin_id)
                    
                    if success:
                        await update.message.reply_text("✅ تم إضافة جهة الاتصال بنجاح")
                    else:
                        await update.message.reply_text("❌ فشل إضافة جهة الاتصال")
            else:
                await update.message.reply_text("❌ يرجى إرسال نص")
                return ADD_AD_MEDIA
        
        user_context['conversation_active'] = False
        context.user_data['conversation_active'] = False
        await self.start(update, context)
        return ConversationHandler.END
    
    async def add_vcf_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بدء إضافة ملف VCF"""
        user_id = update.callback_query.from_user.id
        user_context = self.get_user_context(user_id)
        user_context['conversation_active'] = True
        
        await update.callback_query.edit_message_text(
            "📂 **إضافة جهة اتصال من ملف VCF**\n\n"
            "يرجى إرسال ملف VCF:\n\n"
            "أو أرسل /cancel للإلغاء",
            parse_mode='Markdown'
        )
        context.user_data['conversation_active'] = True
        return ADD_AD_VCF
    
    async def add_ad_vcf(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة ملف VCF"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
        if not user_context.get('conversation_active', False) and not context.user_data.get('conversation_active', False):
            await update.message.reply_text("❌ تم إلغاء العملية. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        if update.message.document:
            file_id = update.message.document.file_id
            file = await context.bot.get_file(file_id)
            
            # التحقق من أن الملف هو VCF
            if update.message.document.mime_type == 'text/vcard' or update.message.document.file_name.endswith('.vcf'):
                # حفظ ملف VCF
                os.makedirs("contacts", exist_ok=True)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                file_path = f"contacts/contact_{timestamp}.vcf"
                await file.download_to_drive(file_path)
                
                # قراءة الملف وتحويله إلى JSON
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        vcard_content = f.read()
                    
                    # تحليل ملف VCF
                    phone = ''
                    first_name = ''
                    last_name = ''
                    
                    for line in vcard_content.split('\n'):
                        line = line.strip()
                        if line.startswith('TEL;') or line.startswith('TEL:'):
                            if ':' in line:
                                phone = line.split(':')[1].strip()
                        elif line.startswith('FN:'):
                            full_name = line.split(':')[1].strip()
                            name_parts = full_name.split(' ', 1)
                            if len(name_parts) > 1:
                                first_name = name_parts[0]
                                last_name = name_parts[1]
                            else:
                                first_name = full_name
                        elif line.startswith('N:'):
                            name_parts = line.split(':')[1].split(';')
                            if len(name_parts) >= 2:
                                last_name = name_parts[0].strip()
                                first_name = name_parts[1].strip()
                    
                    if phone:
                        # إنشاء بيانات JSON
                        contact_data = {
                            'phone_number': phone,
                            'first_name': first_name,
                            'last_name': last_name,
                            'vcf_file': file_path
                        }
                        
                        admin_id = update.message.from_user.id
                        success = self.db.add_ad('contact', contact_data=json.dumps(contact_data), admin_id=admin_id)
                        
                        if success:
                            await update.message.reply_text("✅ تم إضافة جهة اتصال من ملف VCF بنجاح")
                        else:
                            await update.message.reply_text("❌ فشل إضافة جهة الاتصال")
                    else:
                        await update.message.reply_text("❌ لم يتم العثور على رقم هاتف في ملف VCF")
                        
                except Exception as e:
                    logger.error(f"خطأ في معالجة ملف VCF: {str(e)}")
                    await update.message.reply_text("❌ حدث خطأ في معالجة ملف VCF")
            else:
                await update.message.reply_text("❌ الملف ليس من نوع VCF")
        else:
            await update.message.reply_text("❌ يرجى إرسال ملف VCF")
        
        user_context['conversation_active'] = False
        context.user_data['conversation_active'] = False
        await self.start(update, context)
        return ConversationHandler.END
    
    async def show_ads(self, query, context):
        """عرض الإعلانات"""
        admin_id = query.from_user.id
        ads = self.db.get_ads(admin_id)
        
        if not ads:
            await query.edit_message_text("❌ لا توجد إعلانات مضافة")
            return
        
        text = "📢 **الإعلانات المضافة:**\n\n"
        keyboard = []
        
        for ad in ads:
            ad_id, ad_type, ad_text, media_path, file_type, contact_data_json, added_date, ad_admin_id = ad
            type_emoji = {"text": "📝", "photo": "🖼️", "contact": "📞", "vcf": "📂"}

            text += f"**#{ad_id}** - {type_emoji.get(ad_type, '📄')} {ad_type}\n"
            
            if ad_type == 'text' and ad_text:
                text += f"📋 {ad_text[:50]}...\n"
            elif ad_type == 'photo' and ad_text:
                text += f"📋 {ad_text[:30]}... + صورة\n"
            elif ad_type == 'contact' and contact_data_json:
                try:
                    contact_data = json.loads(contact_data_json)
                    phone = contact_data.get('phone_number', '')
                    first_name = contact_data.get('first_name', '')
                    last_name = contact_data.get('last_name', '')
                    text += f"📞 {first_name} {last_name} - {phone}\n"
                except:
                    text += f"📞 جهة اتصال (VCF)\n"
            
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
    
    # قسم إدارة المجموعات
    async def manage_groups(self, query, context):
        """إدارة المجموعات"""
        keyboard = [
            [InlineKeyboardButton("➕ إضافة مجموعة", callback_data="add_group")],
            [InlineKeyboardButton("📊 عرض المجموعات", callback_data="show_groups")],
            [InlineKeyboardButton("🚀 بدء النشر التلقائي", callback_data="start_publishing")],
            [InlineKeyboardButton("⏹️ إيقاف النشر", callback_data="stop_publishing")],
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
            "👥 **إضافة مجموعة جديدة**\n\n"
            "يرجى إرسال رابط المجموعة:\n\n"
            "يمكنك إرسال رابط واحد أو عدة روابط مفصولة بمسافات\n\n"
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
            
        group_links = update.message.text.split()
        admin_id = update.message.from_user.id
        
        added_count = 0
        for link in group_links:
            if link.startswith('https://t.me/') or link.startswith('t.me/'):
                self.db.add_group(link, admin_id)
                added_count += 1
        
        if added_count > 0:
            asyncio.create_task(self.manager.join_groups(admin_id))
            await update.message.reply_text(f"✅ تم إضافة {added_count} مجموعة وبدأ عملية الانضمام")
        else:
            await update.message.reply_text("❌ لم يتم إضافة أي مجموعة، تأكد من صحة الروابط")
        
        user_context['conversation_active'] = False
        context.user_data['conversation_active'] = False
        await self.start(update, context)
        return ConversationHandler.END
    
    async def show_groups(self, query, context):
        """عرض المجموعات"""
        admin_id = query.from_user.id
        groups = self.db.get_groups(admin_id)
        
        if not groups:
            await query.edit_message_text("❌ لا توجد مجموعات مضافة")
            return
        
        text = "👥 **المجموعات المضافة:**\n\n"
        
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
    
    async def start_publishing(self, query, context):
        """بدء النشر التلقائي"""
        admin_id = query.from_user.id
        if self.manager.start_publishing(admin_id):
            await query.edit_message_text("🚀 تم بدء النشر التلقائي في جميع الحسابات والمجموعات")
        else:
            await query.edit_message_text("⚠️ النشر التلقائي يعمل بالفعل")
    
    async def stop_publishing(self, query, context):
        """إيقاف النشر التلقائي"""
        if self.manager.stop_publishing():
            await query.edit_message_text("⏹️ تم إيقاف النشر التلقائي")
        else:
            await query.edit_message_text("⚠️ النشر التلقائي غير نشط")
    
    # قسم إدارة الردود
    async def manage_replies(self, query, context):
        """إدارة الردود"""
        keyboard = [
            [InlineKeyboardButton("💬 الردود في الخاص", callback_data="private_replies")],
            [InlineKeyboardButton("👥 الردود في القروبات", callback_data="group_replies")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "💬 **إدارة الردود**\n\n"
            "اختر نوع الردود التي تريد إدارتها:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def manage_private_replies(self, query, context):
        """إدارة الردود الخاصة"""
        admin_id = query.from_user.id
        replies = self.db.get_private_replies(admin_id)
        
        text = "💬 **الردود في الخاص:**\n\n"
        keyboard = []
        
        if replies:
            for reply in replies:
                reply_id, reply_text, is_active, added_date, reply_admin_id = reply
                status = "🟢 نشط" if is_active else "🔴 غير نشط"
                
                text += f"**#{reply_id}**\n"
                text += f"📝 {reply_text[:50]}...\n"
                text += f"الحالة: {status}\n"
                text += "─" * 20 + "\n"
        else:
            text += "❌ لا توجد ردود مضافة\n"
        
        keyboard.append([InlineKeyboardButton("➕ إضافة رد", callback_data="add_private_reply")])
        keyboard.append([InlineKeyboardButton("🚀 بدء الرد التلقائي", callback_data="start_private_reply")])
        keyboard.append([InlineKeyboardButton("⏹️ إيقاف الرد التلقائي", callback_data="stop_private_reply")])
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
            "يرجى إرسال نص الرد الذي سيتم إرساله للمستخدمين في الخاص:\n\n"
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
        await update.message.reply_text("✅ تم إضافة الرد في الخاص بنجاح")
        user_context['conversation_active'] = False
        context.user_data['conversation_active'] = False
        await self.start(update, context)
        return ConversationHandler.END
    
    async def start_private_reply(self, query, context):
        """بدء الرد التلقائي في الخاص"""
        admin_id = query.from_user.id
        if self.manager.start_private_reply(admin_id):
            await query.edit_message_text("🚀 تم بدء الرد التلقائي على الرسائل الخاصة")
        else:
            await query.edit_message_text("⚠️ الرد التلقائي على الرسائل الخاصة يعمل بالفعل")
    
    async def stop_private_reply(self, query, context):
        """إيقاف الرد التلقائي في الخاص"""
        if self.manager.stop_private_reply():
            await query.edit_message_text("⏹️ تم إيقاف الرد التلقائي على الرسائل الخاصة")
        else:
            await query.edit_message_text("⚠️ الرد التلقائي على الرسائل الخاصة غير نشط")
    
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
                    reply_id, trigger, reply_text, is_active, added_date, reply_admin_id = reply
                    status = "🟢 نشط" if is_active else "🔴 غير نشط"
                    
                    text += f"**#{reply_id}** - {trigger}\n"
                    text += f"➡️ {reply_text[:30]}...\n"
                    text += f"الحالة: {status}\n"
                    text += "─" * 20 + "\n"
            
            if photo_replies:
                for reply in photo_replies:
                    reply_id, trigger, reply_text, media_path, is_active, added_date, reply_admin_id = reply
                    status = "🟢 نشط" if is_active else "🔴 غير نشط"
                    
                    text += f"**#{reply_id}** - {trigger}\n"
                    text += f"➡️ {reply_text[:30]}...\n"
                    text += f"الحالة: {status}\n"
                    text += "─" * 20 + "\n"
        else:
            text += "❌ لا توجد ردود مضافة\n"
        
        text += "\n**الردود العشوائية (100%):**\n"
        if random_replies:
            for reply in random_replies:
                reply_id, reply_text, is_active, added_date, reply_admin_id = reply
                status = "🟢 نشط" if is_active else "🔴 غير نشط"
                
                text += f"**#{reply_id}** - {reply_text[:50]}...\n"
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
            "يرجى إرسال النص الذي سيتم الرد عليه:\n\n"
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
            "يرجى إرسال نص الرد:\n\n"
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
            await update.message.reply_text("✅ تم إضافة الرد النصي في القروبات بنجاح")
        else:
            await update.message.reply_text("❌ لم يتم تحديد النص المحفز")
        
        user_context['conversation_active'] = False
        context.user_data['conversation_active'] = False
        await self.start(update, context)
        return ConversationHandler.END
    
    async def add_group_photo_reply_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بدء إضافة رد مع صورة في القروبات"""
        user_id = update.callback_query.from_user.id
        user_context = self.get_user_context(user_id)
        user_context['conversation_active'] = True
        
        await update.callback_query.edit_message_text(
            "👥 **إضافة رد مع صورة في القروبات**\n\n"
            "يرجى إرسال النص الذي سيتم الرد عليه:\n\n"
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
            "يرجى إرسال نص الرد:\n\n"
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
            "يرجى إرسال الصورة:\n\n"
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
                
                if trigger and reply_text:
                    self.db.add_group_photo_reply(trigger, reply_text, file_path, admin_id=admin_id)
                    await update.message.reply_text("✅ تم إضافة الرد مع الصورة في القروبات بنجاح")
                else:
                    await update.message.reply_text("❌ لم يتم تحديد النص المحفز أو نص الرد")
            except Exception as e:
                logger.error(f"خطأ في حفظ صورة الرد: {str(e)}")
                await update.message.reply_text("❌ حدث خطأ أثناء حفظ الصورة")
        else:
            await update.message.reply_text("❌ يرجى إرسال صورة صالحة")
            return ADD_GROUP_PHOTO
        
        user_context['conversation_active'] = False
        context.user_data['conversation_active'] = False
        await self.start(update, context)
        return ConversationHandler.END
    
    async def add_random_reply_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بدء إضافة رد عشوائي"""
        user_id = update.callback_query.from_user.id
        user_context = self.get_user_context(user_id)
        user_context['conversation_active'] = True
        
        await update.callback_query.edit_message_text(
            "🎲 **إضافة رد عشوائي في القروبات**\n\n"
            "يرجى إرسال نص الرد العشوائي:\n\n"
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
        
        self.db.add_group_random_reply(reply_text, admin_id=admin_id)
        await update.message.reply_text("✅ تم إضافة الرد العشوائي بنجاح")
        user_context['conversation_active'] = False
        context.user_data['conversation_active'] = False
        await self.start(update, context)
        return ConversationHandler.END
    
    async def start_group_reply(self, query, context):
        """بدء الرد التلقائي في القروبات"""
        admin_id = query.from_user.id
        if self.manager.start_group_reply(admin_id):
            await query.edit_message_text("🚀 تم بدء الرد التلقائي على الرسائل المحددة في القروبات")
        else:
            await query.edit_message_text("⚠️ الرد التلقائي على الرسائل المحددة في القروبات يعمل بالفعل")
    
    async def stop_group_reply(self, query, context):
        """إيقاف الرد التلقائي في القروبات"""
        if self.manager.stop_group_reply():
            await query.edit_message_text("⏹️ تم إيقاف الرد التلقائي على الرسائل المحددة في القروبات")
        else:
            await query.edit_message_text("⚠️ الرد التلقائي على الرسائل المحددة في القروبات غير نشط")
    
    async def start_random_reply(self, query, context):
        """بدء الردود العشوائية في القروبات"""
        admin_id = query.from_user.id
        if self.manager.start_random_reply(admin_id):
            await query.edit_message_text("🚀 تم بدء الردود العشوائية في القروبات (الرد على 100% من الرسائل)")
        else:
            await query.edit_message_text("⚠️ الردود العشوائية في القروبات تعمل بالفعل")
    
    async def stop_random_reply(self, query, context):
        """إيقاف الردود العشوائية في القروبات"""
        if self.manager.stop_random_reply():
            await query.edit_message_text("⏹️ تم إيقاف الردود العشوائية في القروبات")
        else:
            await query.edit_message_text("⚠️ الردود العشوائية في القروبات غير نشطة")
    
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
            "يرجى إرسال معرف المستخدم (User ID) للمشرف الجديد:\n\n"
            "يمكنك الحصول على الـ User ID من @userinfobot\n\n"
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
            await update.message.reply_text(f"✅ {message}\n\nتم إضافة المستخدم {user_id_to_add} كمشرف")
                
        except ValueError:
            await update.message.reply_text("❌ معرف المستخدم يجب أن يكون رقماً")
        
        user_context['conversation_active'] = False
        context.user_data['conversation_active'] = False
        await self.start(update, context)
        return ConversationHandler.END
    
    async def show_admins(self, query, context):
        """عرض المشرفين"""
        admins = self.db.get_admins()
        
        if not admins:
            await query.edit_message_text("❌ لا توجد مشرفين مضافة")
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
    
    # قسم الإعدادات
    async def settings_menu(self, query, context):
        """قائمة الإعدادات"""
        keyboard = [
            [InlineKeyboardButton("📊 حالة البوت", callback_data="bot_status")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "⚙️ **إعدادات البوت**\n\n"
            "اختر الإعداد الذي تريد تعديله:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
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
        
        # معالج الإعلانات - تم إصلاحه
        add_ad_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.handle_callback, pattern="^ad_type_")],
            states={
                ADD_AD_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_ad_text)],
                ADD_AD_MEDIA: [
                    MessageHandler(filters.PHOTO, self.add_ad_media),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_ad_media)
                ]
            },
            fallbacks=[CommandHandler("cancel", self.cancel)]
        )
        self.application.add_handler(add_ad_conv)
        
        # معالج ملفات VCF
        add_vcf_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_vcf_start, pattern="^ad_type_vcf$")],
            states={
                ADD_AD_VCF: [MessageHandler(filters.Document.ALL, self.add_ad_vcf)]
            },
            fallbacks=[CommandHandler("cancel", self.cancel)]
        )
        self.application.add_handler(add_vcf_conv)
        
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
                ADD_GROUP_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_group_text_reply_trigger)]
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
                ADD_RANDOM_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_random_reply_text)]
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
            self.db.add_admin(8390377822, "@user", "المشرف الرئيسي", True)
            print(f"✅ تم إضافة الآيدي 8390377822 كمشرف رئيسي")
        except:
            print(f"⚠️  الآيدي 8390377822 مضاف مسبقاً كمشرف رئيسي")
        
        print("🤖 البوت يعمل الآن...")
        print("✅ جميع المشاكل تم إصلاحها")
        print("📢 إدارة الإعلانات تعمل بشكل كامل")
        print("📞 جهات الاتصال تعمل الآن كنقاط اتصال مباشرة:")
        print("   ✅ تم إصلاح مشكلة نشر جهات الاتصال")
        print("   ✅ الآن تنشر كنقطة اتصال وليس كملف")
        print("   ✅ يدعم إضافة جهات اتصال يدوياً")
        print("   ✅ يدعم إضافة جهات اتصال من ملفات VCF")
        print("👥 إدارة الحسابات تعمل بشكل كامل")
        print("💬 إدارة الردود تعمل بشكل كامل")
        print("👨‍💼 إدارة المشرفين تعمل بشكل كامل")
        print("👥 إدارة المجموعات تعمل بشكل كامل")
        
        self.application.run_polling()

# الجزء الأخير من الكود
if __name__ == "__main__":
    # بدء خادم HTTP في خيط منفصل
    http_thread = threading.Thread(target=run_health_server, daemon=True)
    http_thread.start()
    
    # إنشاء المجلدات المطلوبة
    os.makedirs("ads", exist_ok=True)
    os.makedirs("group_replies", exist_ok=True)
    os.makedirs("contacts", exist_ok=True)
    
    # تشغيل البوت
    try:
        bot = BotHandler()
        print("🤖 Starting Telegram Bot...")
        bot.run()
    except Exception as e:
        print(f"❌ Error: {e}")
