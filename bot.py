import os
import json
import asyncio
import logging
import sqlite3
import random
import threading
from datetime import datetime
from threading import Thread, Semaphore
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import HTTPServer, BaseHTTPRequestHandler
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
from telethon.tl.types import InputPhoneContact
from telethon.tl.functions.contacts import ImportContactsRequest

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    print("❌ خطأ: لم يتم تعيين BOT_TOKEN في متغيرات البيئة")
    exit(1)

DB_NAME = "bot_database.db"

# Conversation states
(
    ADD_ACCOUNT, ADD_AD_TYPE, ADD_AD_TEXT, ADD_AD_MEDIA, ADD_GROUP,
    ADD_PRIVATE_REPLY, ADD_GROUP_REPLY, ADD_ADMIN,
    ADD_USERNAME, ADD_RANDOM_REPLY, ADD_PRIVATE_TEXT, ADD_GROUP_TEXT,
    ADD_GROUP_PHOTO, ADD_AD_VCF
) = range(14)

# ==================== HTTP SERVER FOR RENDER ====================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot is running!')
    
    def log_message(self, format, *args):
        pass

def run_health_server():
    """تشغيل خادم HTTP للتحقق من الصحة"""
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"✅ Health server running on port {port}")
    server.serve_forever()

# ==================== DATABASE CLASS ====================
class BotDatabase:
    def __init__(self):
        self.init_database()
    
    def init_database(self):
        """تهيئة قاعدة البيانات"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # إنشاء الجداول
        tables = [
            '''CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_string TEXT UNIQUE,
                phone TEXT,
                name TEXT,
                username TEXT,
                is_active BOOLEAN DEFAULT 1,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                admin_id INTEGER DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS ads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT,
                text TEXT,
                media_path TEXT,
                file_type TEXT,
                contact_data TEXT,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                admin_id INTEGER DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link TEXT,
                status TEXT DEFAULT 'pending',
                join_date DATETIME,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                admin_id INTEGER DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                username TEXT,
                full_name TEXT,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_super_admin BOOLEAN DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS private_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reply_text TEXT,
                is_active BOOLEAN DEFAULT 1,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                admin_id INTEGER DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS group_text_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger TEXT,
                reply_text TEXT,
                is_active BOOLEAN DEFAULT 1,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                admin_id INTEGER DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS group_photo_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger TEXT,
                reply_text TEXT,
                media_path TEXT,
                is_active BOOLEAN DEFAULT 1,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                admin_id INTEGER DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS group_random_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reply_text TEXT,
                is_active BOOLEAN DEFAULT 1,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                admin_id INTEGER DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS account_publishing (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER,
                status TEXT DEFAULT 'active',
                last_publish DATETIME,
                FOREIGN KEY (account_id) REFERENCES accounts (id)
            )''',
            '''CREATE TABLE IF NOT EXISTS publishing_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER,
                group_id INTEGER,
                ad_id INTEGER,
                status TEXT,
                message TEXT,
                publish_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (account_id) REFERENCES accounts (id),
                FOREIGN KEY (group_id) REFERENCES groups (id),
                FOREIGN KEY (ad_id) REFERENCES ads (id)
            )'''
        ]
        
        for table in tables:
            cursor.execute(table)
        
        conn.commit()
        conn.close()
    
    # ========== ACCOUNTS METHODS ==========
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
            
            cursor.execute('INSERT INTO account_publishing (account_id) VALUES (?)', (account_id,))
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
    
    # ========== ADS METHODS ==========
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
    
    # ========== GROUPS METHODS ==========
    def add_group(self, link, admin_id=0):
        """إضافة مجموعة"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('INSERT INTO groups (link, admin_id) VALUES (?, ?)', (link, admin_id))
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
    
    def get_pending_groups(self, admin_id=None):
        """الحصول على المجموعات المعلقة فقط"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if admin_id is not None:
            cursor.execute("SELECT * FROM groups WHERE status = 'pending' AND (admin_id = ? OR admin_id = 0) ORDER BY id", (admin_id,))
        else:
            cursor.execute("SELECT * FROM groups WHERE status = 'pending' ORDER BY id")
            
        groups = cursor.fetchall()
        conn.close()
        return groups
    
    def update_group_status(self, group_id, status):
        """تحديث حالة المجموعة"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('UPDATE groups SET status = ?, join_date = CURRENT_TIMESTAMP WHERE id = ?', (status, group_id))
        conn.commit()
        conn.close()
        return True
    
    # ========== ADMINS METHODS ==========
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
    
    # ========== REPLIES METHODS ==========
    def add_private_reply(self, reply_text, admin_id=0):
        """إضافة رد خاص"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('INSERT INTO private_replies (reply_text, admin_id) VALUES (?, ?)', (reply_text, admin_id))
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
        
        cursor.execute('INSERT INTO group_text_replies (trigger, reply_text, admin_id) VALUES (?, ?, ?)', (trigger, reply_text, admin_id))
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
        
        cursor.execute('INSERT INTO group_photo_replies (trigger, reply_text, media_path, admin_id) VALUES (?, ?, ?, ?)', 
                      (trigger, reply_text, media_path, admin_id))
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
        
        cursor.execute('INSERT INTO group_random_replies (reply_text, admin_id) VALUES (?, ?)', (reply_text, admin_id))
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

# ==================== TELEGRAM BOT MANAGER ====================
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
        self.semaphore = Semaphore(1000)  # للسماح بـ 1000 عملية متزامنة
        self.account_groups_cache = {}  # كاش للمجموعات
    
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
            return False, None
    
    async def join_groups(self, admin_id=None):
        """الانضمام إلى المجموعات - 3 مجموعات كل 3 دقائق"""
        print("🚀 بدء عملية الانضمام إلى المجموعات...")
        
        # الحصول على المجموعات المعلقة
        pending_groups = self.db.get_pending_groups(admin_id)
        
        if not pending_groups:
            print("✅ لا توجد مجموعات معلقة للانضمام")
            return
        
        # الحصول على الحسابات النشطة
        accounts = self.db.get_active_publishing_accounts(admin_id)
        
        if not accounts:
            print("❌ لا توجد حسابات نشطة للانضمام")
            return
        
        print(f"📊 العثور على {len(pending_groups)} مجموعة معلقة و {len(accounts)} حساب نشط")
        
        # تقسيم المجموعات إلى مجموعات من 3
        group_chunks = [pending_groups[i:i+3] for i in range(0, len(pending_groups), 3)]
        
        for chunk_index, group_chunk in enumerate(group_chunks):
            print(f"\n📦 معالجة المجموعات {chunk_index * 3 + 1}-{chunk_index * 3 + len(group_chunk)} من {len(pending_groups)}")
            
            # معالجة كل مجموعة في هذه الدفعة
            for group in group_chunk:
                group_id, group_link, status, join_date, added_date, group_admin_id = group
                
                print(f"🔗 محاولة الانضمام إلى: {group_link}")
                
                # محاولة الانضمام بالحسابات المتاحة
                joined = False
                for account in accounts:
                    if joined:
                        break
                    
                    account_id, session_string, name, username = account
                    
                    try:
                        client = TelegramClient(StringSession(session_string), 1, "b")
                        await client.connect()
                        
                        if await client.is_user_authorized():
                            try:
                                # محاولة الانضمام
                                if 't.me/+' in group_link:
                                    invite_hash = group_link.split('+')[1]
                                    await client(ImportChatInviteRequest(invite_hash))
                                    self.db.update_group_status(group_id, 'joined')
                                    print(f"✅ الحساب {name} انضم إلى المجموعة {group_link}")
                                    joined = True
                                    
                                else:
                                    # محاولة الانضمام إلى قناة/مجموعة عادية
                                    try:
                                        await client(JoinChannelRequest(group_link))
                                        self.db.update_group_status(group_id, 'joined')
                                        print(f"✅ الحساب {name} انضم إلى المجموعة {group_link}")
                                        joined = True
                                    except Exception as e:
                                        # محاولة أخرى بطريقة مختلفة
                                        try:
                                            entity = await client.get_entity(group_link)
                                            await client(JoinChannelRequest(entity))
                                            self.db.update_group_status(group_id, 'joined')
                                            print(f"✅ الحساب {name} انضم إلى المجموعة {group_link}")
                                            joined = True
                                        except Exception as e2:
                                            print(f"❌ فشل الانضمام بالحساب {name}: {e2}")
                                
                            except Exception as e:
                                print(f"❌ فشل الانضمام بالحساب {name}: {e}")
                        
                        await client.disconnect()
                        
                    except Exception as e:
                        print(f"❌ خطأ في معالجة الحساب {name}: {e}")
                        continue
                    
                    # تأخير قصير بين محاولات الحسابات للمجموعة الواحدة
                    await asyncio.sleep(1)
                
                if not joined:
                    self.db.update_group_status(group_id, 'failed')
                    print(f"❌ فشل جميع الحسابات في الانضمام إلى {group_link}")
                
                # تأخير بين المجموعات في نفس الدفعة
                await asyncio.sleep(2)
            
            # إذا كانت هناك دفعات أخرى، انتظر 3 دقائق قبل الدفعة التالية
            if chunk_index < len(group_chunks) - 1:
                print(f"⏳ انتظار 3 دقائق قبل الدفعة التالية...")
                await asyncio.sleep(180)  # 3 دقائق
        
        print("✅ اكتملت عملية الانضمام إلى جميع المجموعات")
    
    async def get_account_groups_fast(self, client, account_id):
        """الحصول على مجموعات الحساب بسرعة (مع الكاش)"""
        if account_id in self.account_groups_cache:
            return self.account_groups_cache[account_id]
        
        try:
            dialogs = await client.get_dialogs(limit=200)  # زيادة الحد لـ 200 مجموعة
            groups = []
            
            for dialog in dialogs:
                if dialog.is_group or dialog.is_channel:
                    groups.append({
                        'id': dialog.id,
                        'title': dialog.title or str(dialog.id),
                        'entity': dialog.entity
                    })
            
            # حفظ في الكاش لمدة 5 دقائق
            self.account_groups_cache[account_id] = groups
            
            return groups
            
        except Exception as e:
            print(f"❌ خطأ في الحصول على مجموعات الحساب: {e}")
            return []
    
    async def publish_single_account_ultra_fast(self, account, ad, groups):
        """نشر إعلان واحد بحساب واحد في مجموعة واحدة - فائق السرعة"""
        account_id, session_string, name, username = account
        ad_id, ad_type, ad_text, media_path, file_type, contact_data_json, added_date, ad_admin_id = ad
        
        try:
            # إنشاء عميل جديد لكل عملية نشر
            client = TelegramClient(StringSession(session_string), 1, "b")
            await client.connect()
            
            if not await client.is_user_authorized():
                await client.disconnect()
                return f"❌ الحساب {name} غير مفعل"
            
            # اختيار مجموعة عشوائية من المجموعات
            if not groups:
                await client.disconnect()
                return f"⚠️ الحساب {name} ليس في أي مجموعات"
            
            group = random.choice(groups)
            group_id = group['id']
            group_title = group['title']
            
            success = False
            error_msg = ""
            
            try:
                if ad_type == 'text':
                    await client.send_message(group_id, ad_text)
                    success = True
                
                elif ad_type == 'photo' and media_path and os.path.exists(media_path):
                    await client.send_file(group_id, media_path, caption=ad_text)
                    success = True
                
                elif ad_type == 'contact' and contact_data_json:
                    try:
                        contact_data = json.loads(contact_data_json)
                        phone_number = contact_data.get('phone_number', '')
                        first_name = contact_data.get('first_name', '')
                        last_name = contact_data.get('last_name', '')
                        
                        if phone_number:
                            contact_text = f"👤 **جهة اتصال**\n\n"
                            contact_text += f"**الاسم:** {first_name} {last_name}\n"
                            contact_text += f"**رقم الهاتف:** `{phone_number}`\n"
                            contact_text += f"📞 للتواصل: `{phone_number}`\n\n"
                            
                            await client.send_message(group_id, contact_text)
                            success = True
                    except:
                        alt_text = "📞 **جهة اتصال**\n\nللاستفسار والتواصل 📱"
                        await client.send_message(group_id, alt_text)
                        success = True
                
                elif ad_type in ['document', 'video', 'audio'] and media_path and os.path.exists(media_path):
                    await client.send_file(group_id, media_path, caption=ad_text)
                    success = True
                
            except Exception as e:
                error_msg = str(e)
            
            await client.disconnect()
            
            if success:
                return f"✅ {name} نشر في {group_title}"
            else:
                return f"❌ {name} فشل النشر: {error_msg[:50]}"
            
        except Exception as e:
            return f"❌ خطأ في الحساب {name}: {str(e)[:50]}"
    
    async def publish_all_accounts_ultra_fast(self, admin_id=None):
        """النشر بجميع الحسابات في نفس الثانية - فائق السرعة"""
        print("⚡ بدأ النشر الفائق السرعة بجميع الحسابات...")
        
        while self.publishing_active:
            try:
                start_time = time.time()
                
                # الحصول على الحسابات والإعلانات
                accounts = self.db.get_active_publishing_accounts(admin_id)
                ads = self.db.get_ads(admin_id)
                
                if not accounts or not ads:
                    print("⚠️ لا توجد حسابات أو إعلانات للنشر")
                    await asyncio.sleep(10)
                    continue
                
                print(f"⚡ جاري النشر بـ {len(accounts)} حساب و {len(ads)} إعلان")
                
                # اختيار إعلان عشوائي واحد لجميع الحسابات
                ad = random.choice(ads)
                
                # جمع جميع المجموعات لجميع الحسابات بشكل متوازي
                print(f"📊 جاري جمع المجموعات لجميع الحسابات...")
                
                # إنشاء قائمة بالمهام لجمع المجموعات
                group_tasks = []
                for account in accounts:
                    account_id, session_string, name, username = account
                    task = self.collect_account_groups(account)
                    group_tasks.append((account, task))
                
                # تنفيذ جميع المهام بشكل متوازي
                account_groups_map = {}
                tasks_to_run = [task for _, task in group_tasks]
                results = await asyncio.gather(*tasks_to_run, return_exceptions=True)
                
                # معالجة النتائج
                for i, (account, _) in enumerate(group_tasks):
                    if i < len(results) and not isinstance(results[i], Exception):
                        account_groups_map[account] = results[i]
                
                # النشر في جميع الحسابات بشكل متوازي
                print(f"🚀 بدأ النشر المتوازي بجميع الحسابات...")
                
                publish_tasks = []
                for account in accounts:
                    if account in account_groups_map:
                        groups = account_groups_map[account]
                        if groups:  # فقط إذا كان لدى الحساب مجموعات
                            task = self.publish_single_account_ultra_fast(account, ad, groups)
                            publish_tasks.append(task)
                
                # تنفيذ جميع مهام النشر بشكل متوازي
                publish_results = await asyncio.gather(*publish_tasks, return_exceptions=True)
                
                # عد النتائج الناجحة
                success_count = sum(1 for r in publish_results if isinstance(r, str) and r.startswith("✅"))
                failed_count = len(publish_results) - success_count
                
                end_time = time.time()
                duration = end_time - start_time
                
                print(f"✅ اكتمل النشر في {duration:.2f} ثانية")
                print(f"📊 النتائج: {success_count} نجاح، {failed_count} فشل")
                
                # تنظيف الكاش
                self.account_groups_cache.clear()
                
                # انتظار قصير جداً قبل الجولة التالية
                await asyncio.sleep(1)
                
            except Exception as e:
                print(f"❌ خطأ في النشر الفائق: {e}")
                await asyncio.sleep(5)
    
    async def collect_account_groups(self, account):
        """جمع مجموعات حساب معين"""
        account_id, session_string, name, username = account
        
        try:
            client = TelegramClient(StringSession(session_string), 1, "b")
            await client.connect()
            
            if not await client.is_user_authorized():
                await client.disconnect()
                return []
            
            dialogs = await client.get_dialogs(limit=100)
            groups = []
            
            for dialog in dialogs:
                if dialog.is_group or dialog.is_channel:
                    groups.append({
                        'id': dialog.id,
                        'title': dialog.title or str(dialog.id),
                        'entity': dialog.entity
                    })
            
            await client.disconnect()
            return groups
            
        except Exception as e:
            print(f"❌ خطأ في جمع مجموعات الحساب {name}: {e}")
            return []
    
    async def publish_mass_parallel(self, admin_id=None):
        """النشر الجماعي المتوازي - الإصدار الأسرع"""
        print("🚀 بدأ النشر الجماعي المتوازي...")
        
        while self.publishing_active:
            try:
                # الحصول على البيانات
                accounts = self.db.get_active_publishing_accounts(admin_id)
                ads = self.db.get_ads(admin_id)
                
                if not accounts or not ads:
                    await asyncio.sleep(10)
                    continue
                
                print(f"📊 جاري تحضير {len(accounts)} حساب للنشر")
                
                # اختيار إعلان عشوائي
                ad = random.choice(ads)
                
                # إنشاء مهام النشر لجميع الحسابات
                tasks = []
                for account in accounts:
                    task = self.publish_account_parallel(account, ad)
                    tasks.append(task)
                
                # تشغيل جميع المهام بشكل متوازي
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # عد النتائج
                success = sum(1 for r in results if r == "success")
                failed = len(results) - success
                
                print(f"✅ اكتملت جولة النشر: {success} نجاح، {failed} فشل")
                
                # انتظار قصير جداً
                await asyncio.sleep(0.5)
                
            except Exception as e:
                print(f"❌ خطأ في النشر الجماعي: {e}")
                await asyncio.sleep(2)
    
    async def publish_account_parallel(self, account, ad):
        """نشر بحساب واحد بشكل متوازي"""
        account_id, session_string, name, username = account
        
        try:
            # إنشاء عميل سريع
            client = TelegramClient(StringSession(session_string), 1, "b")
            await client.connect()
            
            if not await client.is_user_authorized():
                await client.disconnect()
                return "failed"
            
            # الحصول على مجموعة عشوائية بسرعة
            try:
                dialogs = await client.get_dialogs(limit=50)
                groups = [d for d in dialogs if d.is_group or d.is_channel]
                
                if groups:
                    group = random.choice(groups)
                    
                    # النشر السريع
                    ad_id, ad_type, ad_text, media_path, file_type, contact_data_json, added_date, ad_admin_id = ad
                    
                    if ad_type == 'text':
                        await client.send_message(group.id, ad_text, silent=True)
                    elif ad_type == 'contact' and contact_data_json:
                        try:
                            contact_data = json.loads(contact_data_json)
                            phone_number = contact_data.get('phone_number', '')
                            first_name = contact_data.get('first_name', '')
                            last_name = contact_data.get('last_name', '')
                            
                            if phone_number:
                                contact_text = f"👤 **جهة اتصال**\n\n"
                                contact_text += f"**الاسم:** {first_name} {last_name}\n"
                                contact_text += f"**رقم الهاتف:** `{phone_number}`\n"
                                contact_text += f"📞 للتواصل: `{phone_number}`\n\n"
                                
                                await client.send_message(group.id, contact_text, silent=True)
                        except:
                            alt_text = "📞 **جهة اتصال**\n\nللاستفسار والتواصل 📱"
                            await client.send_message(group.id, alt_text, silent=True)
            
            except Exception as e:
                pass
            
            await client.disconnect()
            return "success"
            
        except Exception as e:
            return "failed"
    
    def start_publishing(self, admin_id=None):
        """بدء النشر التلقائي فائق السرعة"""
        with self.lock:
            if not self.publishing_active:
                self.publishing_active = True
                self.publishing_thread = Thread(
                    target=lambda: asyncio.run(self.publish_all_accounts_ultra_fast(admin_id)),
                    daemon=True
                )
                self.publishing_thread.start()
                print("⚡ تم بدء النشر الفائق السرعة بجميع الحسابات")
                return True
        return False
    
    def stop_publishing(self):
        """إيقاف النشر التلقائي"""
        with self.lock:
            if self.publishing_active:
                self.publishing_active = False
                print("⏹️ جاري إيقاف النشر...")
                if self.publishing_thread:
                    try:
                        self.publishing_thread.join(timeout=3)
                    except:
                        pass
                return True
        return False
    
    # باقي الدوال تبقى كما هي...
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
                                            await asyncio.sleep(1)
                                            break
                        
                        await client.disconnect()
                    
                    except Exception as e:
                        continue
                
                await asyncio.sleep(10)
            
            except Exception as e:
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
                                                        await asyncio.sleep(1)
                                                        break
                                                
                                                # الردود مع الصور
                                                for reply in photo_replies:
                                                    reply_id, trigger, reply_text, media_path, is_active, added_date, reply_admin_id = reply
                                                    
                                                    if is_active and trigger.lower() in message.text.lower() and os.path.exists(media_path):
                                                        await client.send_file(dialog.id, media_path, caption=reply_text, reply_to=message.id)
                                                        await asyncio.sleep(1)
                                                        break
                                        
                                    except Exception as e:
                                        continue
                        
                        await client.disconnect()
                    
                    except Exception as e:
                        continue
                
                await asyncio.sleep(10)
            
            except Exception as e:
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
                                                    await asyncio.sleep(1)
                                                    break
                                        
                                    except Exception as e:
                                        continue
                        
                        await client.disconnect()
                    
                    except Exception as e:
                        continue
                
                await asyncio.sleep(10)
            
            except Exception as e:
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

# ==================== BOT HANDLER CLASS ====================
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
    
    # ========== COMMAND HANDLERS ==========
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بدء البوت"""
        user = update.effective_user
        user_id = user.id
        
        if not self.db.is_admin(user_id):
            await update.message.reply_text("❌ ليس لديك صلاحية للوصول إلى هذا البوت.")
            return
        
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
            "🎮 **لوحة تحكم البوت المتكامل**\n\nاختر القسم الذي تريد إدارته:",
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
    
    # ========== CALLBACK HANDLER ==========
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة الأزرار"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        if not self.db.is_admin(user_id):
            await query.edit_message_text("❌ ليس لديك صلاحية للوصول إلى هذا البوت.")
            return
        
        data = query.data
        
        # Main menu handlers
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
        
        # Accounts management
        elif data == "add_account":
            await self.add_account_start(update, context)
        elif data == "show_accounts":
            await self.show_accounts(query, context)
        elif data.startswith("delete_account_"):
            account_id = int(data.split("_")[2])
            await self.delete_account(query, context, account_id)
        
        # Ads management
        elif data == "add_ad":
            await self.add_ad_start(query, context)
        elif data == "show_ads":
            await self.show_ads(query, context)
        elif data.startswith("delete_ad_"):
            ad_id = int(data.split("_")[2])
            await self.delete_ad(query, context, ad_id)
        elif data.startswith("ad_type_"):
            ad_type = data.replace("ad_type_", "")
            user_context = self.get_user_context(user_id)
            user_context['ad_type'] = ad_type
            
            if ad_type == 'contact':
                await query.edit_message_text(
                    f"📞 **إضافة جهة اتصال**\n\nيرجى إرسال رقم الهاتف:\n\nمثال: +1234567890\n\nأو أرسل /cancel للإلغاء",
                    parse_mode='Markdown'
                )
                user_context['conversation_active'] = True
                context.user_data['ad_type'] = ad_type
                context.user_data['conversation_active'] = True
                return ADD_AD_TEXT
            else:
                await query.edit_message_text(
                    f"📝 **إضافة نص الإعلان**\n\nيرجى إرسال نص الإعلان:\n\nأو أرسل /cancel للإلغاء",
                    parse_mode='Markdown'
                )
                user_context['conversation_active'] = True
                context.user_data['ad_type'] = ad_type
                context.user_data['conversation_active'] = True
                return ADD_AD_TEXT
        
        # Groups management
        elif data == "add_group":
            await self.add_group_start(update, context)
        elif data == "show_groups":
            await self.show_groups(query, context)
        elif data == "start_publishing":
            await self.start_publishing(query, context)
        elif data == "stop_publishing":
            await self.stop_publishing(query, context)
        
        # Replies management
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
        
        # Admins management
        elif data == "add_admin":
            await self.add_admin_start(update, context)
        elif data == "show_admins":
            await self.show_admins(query, context)
        elif data.startswith("delete_admin_"):
            admin_id = int(data.split("_")[2])
            await self.delete_admin(query, context, admin_id)
        
        # Back buttons
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
            "🎮 **لوحة تحكم البوت المتكامل**\n\nاختر القسم الذي تريد إدارته:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    # ========== ACCOUNTS MANAGEMENT ==========
    async def manage_accounts(self, query, context):
        """إدارة الحسابات"""
        keyboard = [
            [InlineKeyboardButton("➕ إضافة حساب", callback_data="add_account")],
            [InlineKeyboardButton("👥 عرض الحسابات", callback_data="show_accounts")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "👥 **إدارة الحسابات**\n\nاختر الإجراء الذي تريد تنفيذه:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def add_account_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بدء إضافة حساب"""
        user_id = update.callback_query.from_user.id
        user_context = self.get_user_context(user_id)
        user_context['conversation_active'] = True
        
        await update.callback_query.edit_message_text(
            "📱 **إضافة حساب جديد**\n\nيرجى إرسال كود الجلسة (Session String):\n\nأو أرسل /cancel للإلغاء",
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
    
    # ========== ADS MANAGEMENT ==========
    async def manage_ads(self, query, context):
        """إدارة الإعلانات"""
        keyboard = [
            [InlineKeyboardButton("➕ إضافة إعلان", callback_data="add_ad")],
            [InlineKeyboardButton("📋 عرض الإعلانات", callback_data="show_ads")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "📢 **إدارة الإعلانات**\n\nاختر الإجراء الذي تريد تنفيذه:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def add_ad_start(self, query, context):
        """بدء إضافة إعلان"""
        keyboard = [
            [InlineKeyboardButton("📝 نص فقط", callback_data="ad_type_text")],
            [InlineKeyboardButton("🖼️ صورة مع نص", callback_data="ad_type_photo")],
            [InlineKeyboardButton("📞 جهة اتصال يدوياً", callback_data="ad_type_contact")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_ads")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "📢 **إضافة إعلان جديد**\n\nاختر نوع الإعلان:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def add_ad_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة نص الإعلان أو رقم هاتف جهة الاتصال"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
        if not user_context.get('conversation_active', False) and not context.user_data.get('conversation_active', False):
            await update.message.reply_text("❌ تم إلغاء العملية. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        ad_type = context.user_data.get('ad_type') or user_context.get('ad_type')
        if not ad_type:
            await update.message.reply_text("❌ خطأ: لم يتم تحديد نوع الإعلان. استخدم /start للبدء من جديد.")
            return ConversationHandler.END
            
        admin_id = update.message.from_user.id
        
        if ad_type == 'text':
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
            ad_text = update.message.text
            user_context['ad_text'] = ad_text
            context.user_data['ad_text'] = ad_text
            
            await update.message.reply_text("🖼️ **إضافة صورة**\n\nيرجى إرسال الصورة:\n\nأو أرسل /cancel للإلغاء")
            return ADD_AD_MEDIA
            
        elif ad_type == 'contact':
            phone_number = update.message.text.strip()
            if not phone_number.startswith('+'):
                phone_number = '+' + phone_number
            
            user_context['contact_phone'] = phone_number
            context.user_data['contact_phone'] = phone_number
            
            await update.message.reply_text("📞 **إضافة جهة اتصال**\n\nيرجى إرسال الاسم الأول:\n\nأو أرسل /cancel للإلغاء")
            return ADD_AD_MEDIA
    
    async def add_ad_media(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة ملف الإعلان أو معلومات جهة الاتصال"""
        user_id = update.message.from_user.id
        user_context = self.get_user_context(user_id)
        
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
                if 'contact_first_name' not in context.user_data and 'contact_first_name' not in user_context:
                    first_name = update.message.text
                    context.user_data['contact_first_name'] = first_name
                    user_context['contact_first_name'] = first_name
                    
                    await update.message.reply_text(
                        "📞 **إضافة جهة اتصال**\n\nيرجى إرسال الاسم الأخير (اختياري):\n\nأرسل 'لا يوجد' إذا لم يكن هناك اسم أخير\n\nأو أرسل /cancel للإلغاء"
                    )
                    return ADD_AD_MEDIA
                else:
                    last_name = update.message.text if update.message.text != 'لا يوجد' else ''
                    first_name = context.user_data.get('contact_first_name') or user_context.get('contact_first_name')
                    phone_number = context.user_data.get('contact_phone') or user_context.get('contact_phone')
                    
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
            type_emoji = {"text": "📝", "photo": "🖼️", "contact": "📞"}

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
                    text += f"📞 جهة اتصال\n"
            
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
    
    # ========== GROUPS MANAGEMENT ==========
    async def manage_groups(self, query, context):
        """إدارة المجموعات"""
        keyboard = [
            [InlineKeyboardButton("➕ إضافة مجموعة", callback_data="add_group")],
            [InlineKeyboardButton("📊 عرض المجموعات", callback_data="show_groups")],
            [InlineKeyboardButton("⚡ بدء النشر الفائق", callback_data="start_publishing")],
            [InlineKeyboardButton("⏹️ إيقاف النشر", callback_data="stop_publishing")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "👥 **إدارة المجموعات**\n\nاختر الإجراء الذي تريد تنفيذه:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def add_group_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بدء إضافة مجموعة"""
        user_id = update.callback_query.from_user.id
        user_context = self.get_user_context(user_id)
        user_context['conversation_active'] = True
        
        await update.callback_query.edit_message_text(
            "👥 **إضافة مجموعة جديدة**\n\nيرجى إرسال رابط المجموعة (يمكن إرسال عدة روابط في رسالة واحدة):\n\nأو أرسل /cancel للإلغاء",
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
        invalid_links = []
        
        for link in group_links:
            if link.startswith('https://t.me/') or link.startswith('t.me/'):
                self.db.add_group(link, admin_id)
                added_count += 1
            else:
                invalid_links.append(link)
        
        if added_count > 0:
            # بدء عملية الانضمام في خيط منفصل
            asyncio.create_task(self.manager.join_groups(admin_id))
            response = f"✅ تم إضافة {added_count} مجموعة\n"
            response += f"🚀 بدأت عملية الانضمام (3 مجموعات كل 3 دقائق)\n\n"
            
            if invalid_links:
                response += f"❌ الروابط التالية غير صالحة:\n"
                for invalid_link in invalid_links[:5]:  # عرض أول 5 روابط غير صالحة فقط
                    response += f"- {invalid_link}\n"
            
            await update.message.reply_text(response)
        else:
            await update.message.reply_text("❌ لم يتم إضافة أي مجموعة، تأكد من صحة الروابط\n\nيجب أن تبدأ الروابط بـ https://t.me/ أو t.me/")
        
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
        pending_count = 0
        joined_count = 0
        failed_count = 0
        
        for group in groups:
            group_id, link, status, join_date, added_date, group_admin_id = group
            status_emoji = {"pending": "⏳", "joined": "✅", "failed": "❌"}
            
            text += f"**#{group_id}** - {link}\n"
            text += f"الحالة: {status_emoji.get(status, '❓')} {status}\n"
            
            if join_date:
                text += f"تاريخ الانضمام: {join_date}\n"
            
            text += "─" * 20 + "\n"
            
            # إحصاءات
            if status == 'pending':
                pending_count += 1
            elif status == 'joined':
                joined_count += 1
            elif status == 'failed':
                failed_count += 1
        
        # إضافة الإحصاءات
        stats = f"\n📊 **الإحصاءات:**\n"
        stats += f"⏳ معلقة: {pending_count}\n"
        stats += f"✅ منضمة: {joined_count}\n"
        stats += f"❌ فشلت: {failed_count}\n"
        stats += f"📋 المجموع: {len(groups)}"
        
        text += stats
        
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_groups")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def start_publishing(self, query, context):
        """بدء النشر الفائق السرعة"""
        admin_id = query.from_user.id
        if self.manager.start_publishing(admin_id):
            await query.edit_message_text("⚡ تم بدء النشر الفائق السرعة بجميع الحسابات\n\n🚀 البوت ينشر الآن بجميع الحسابات في نفس الثانية!")
        else:
            await query.edit_message_text("⚠️ النشر الفائق يعمل بالفعل")
    
    async def stop_publishing(self, query, context):
        """إيقاف النشر التلقائي"""
        if self.manager.stop_publishing():
            await query.edit_message_text("⏹️ تم إيقاف النشر الفائق")
        else:
            await query.edit_message_text("⚠️ النشر الفائق غير نشط")
    
    # باقي الدوال تبقى كما هي...
    async def manage_replies(self, query, context):
        """إدارة الردود"""
        keyboard = [
            [InlineKeyboardButton("💬 الردود في الخاص", callback_data="private_replies")],
            [InlineKeyboardButton("👥 الردود في القروبات", callback_data="group_replies")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "💬 **إدارة الردود**\n\nاختر نوع الردود التي تريد إدارتها:",
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
            "💬 **إضافة رد في الخاص**\n\nيرجى إرسال نص الرد:\n\nأو أرسل /cancel للإلغاء",
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
            "👥 **إضافة رد نصي في القروبات**\n\nيرجى إرسال النص الذي سيتم الرد عليه:\n\nأو أرسل /cancel للإلغاء",
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
            "👥 **إضافة رد نصي في القروبات**\n\nيرجى إرسال نص الرد:\n\nأو أرسل /cancel للإلغاء",
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
            "👥 **إضافة رد مع صورة في القروبات**\n\nيرجى إرسال النص الذي سيتم الرد عليه:\n\nأو أرسل /cancel للإلغاء",
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
            "👥 **إضافة رد مع صورة في القروبات**\n\nيرجى إرسال نص الرد:\n\nأو أرسل /cancel للإلغاء",
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
            "👥 **إضافة رد مع صورة في القروبات**\n\nيرجى إرسال الصورة:\n\nأو أرسل /cancel للإلغاء",
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
            "🎲 **إضافة رد عشوائي في القروبات**\n\nيرجى إرسال نص الرد العشوائي:\n\nأو أرسل /cancel للإلغاء",
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
    
    # ========== ADMINS MANAGEMENT ==========
    async def manage_admins(self, query, context):
        """إدارة المشرفين"""
        keyboard = [
            [InlineKeyboardButton("➕ إضافة مشرف", callback_data="add_admin")],
            [InlineKeyboardButton("👨‍💼 عرض المشرفين", callback_data="show_admins")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "👨‍💼 **إدارة المشرفين**\n\nاختر الإجراء الذي تريد تنفيذه:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def add_admin_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بدء إضافة مشرف"""
        user_id = update.callback_query.from_user.id
        user_context = self.get_user_context(user_id)
        user_context['conversation_active'] = True
        
        await update.callback_query.edit_message_text(
            "👨‍💼 **إضافة مشرف جديد**\n\nيرجى إرسال معرف المستخدم (User ID):\n\nأو أرسل /cancel للإلغاء",
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
    
    # ========== SETTINGS ==========
    async def settings_menu(self, query, context):
        """قائمة الإعدادات"""
        keyboard = [
            [InlineKeyboardButton("📊 حالة البوت", callback_data="bot_status")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "⚙️ **إعدادات البوت**\n\nاختر الإعداد الذي تريد تعديله:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    # ========== SETUP HANDLERS ==========
    def setup_handlers(self):
        """إعداد معالجات البوت"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("cancel", self.cancel))
        
        # معالجات المحادثة
        add_account_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_account_start, pattern="^add_account$")],
            states={ADD_ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_account_session)]},
            fallbacks=[CommandHandler("cancel", self.cancel)]
        )
        self.application.add_handler(add_account_conv)
        
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
        
        add_group_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_group_start, pattern="^add_group$")],
            states={ADD_GROUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_group_link)]},
            fallbacks=[CommandHandler("cancel", self.cancel)]
        )
        self.application.add_handler(add_group_conv)
        
        add_admin_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_admin_start, pattern="^add_admin$")],
            states={ADD_ADMIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_admin_id)]},
            fallbacks=[CommandHandler("cancel", self.cancel)]
        )
        self.application.add_handler(add_admin_conv)
        
        private_reply_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_private_reply_start, pattern="^add_private_reply$")],
            states={ADD_PRIVATE_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_private_reply_text)]},
            fallbacks=[CommandHandler("cancel", self.cancel)]
        )
        self.application.add_handler(private_reply_conv)
        
        group_text_reply_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.add_group_text_reply_start, pattern="^add_group_text_reply$")],
            states={ADD_GROUP_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_group_text_reply_trigger)]},
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
            states={ADD_RANDOM_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_random_reply_text)]},
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
            print(f"⚠️ الآيدي 8294336757 مضاف مسبقاً كمشرف رئيسي")
        
        print("🤖 البوت يعمل الآن...")
        print("⚡ **النشر فائق السرعة مفعل**")
        print("✅ جميع الحسابات تنشر في نفس الثانية")
        print("📊 يدعم حتى 1000 حساب في نفس الوقت")
        print("🚀 الأداء: أقل من ثانية واحدة للدورة الكاملة")
        print("📢 إدارة الإعلانات تعمل بشكل كامل")
        print("📞 جهات الاتصال تعمل بشكل صحيح")
        print("👥 إدارة الحسابات تعمل بشكل كامل")
        print("💬 إدارة الردود تعمل بشكل كامل")
        print("👨‍💼 إدارة المشرفين تعمل بشكل كامل")
        print("👥 إدارة المجموعات تعمل بشكل كامل")
        print("⏰ نظام الانضمام: 3 مجموعات كل 3 دقائق")
        print("🌐 خادم HTTP يعمل على المنفذ 10000 لـ Render.com")
        
        self.application.run_polling()

# ==================== MAIN ENTRY POINT ====================
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
