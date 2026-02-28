import os
import logging
from datetime import datetime, timedelta, time
import pytz
import asyncio
import html
import re
import threading
import time as _time
from flask import Flask, request, Response
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import gspread
from google.oauth2.service_account import Credentials
import httpx
import hashlib

# -----------------------------------------
# ЛОГИРОВАНИЕ
# -----------------------------------------
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------------------
# КОНФИГУРАЦИЯ (сначала пытаемся взять из окружения)
# -----------------------------------------
# В рабочем окружении значения конфигурации должны передаваться через
# переменные окружения. Значения по умолчанию — это заглушки, чтобы
# случайно не утечь реальные креденшалы. Замените строки ниже на свои или
# задайте соответствующие переменные окружения перед запуском бота.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or "YOUR_TELEGRAM_TOKEN"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY") or "YOUR_OPENROUTER_API_KEY"
ROBOKASSA_LOGIN = os.getenv("ROBOKASSA_LOGIN") or "YOUR_ROBOKASSA_LOGIN"
ROBOKASSA_PASSWORD1 = os.getenv("ROBOKASSA_PASSWORD1") or "YOUR_ROBOKASSA_PASSWORD1"
ROBOKASSA_PASSWORD2 = os.getenv("ROBOKASSA_PASSWORD2") or "YOUR_ROBOKASSA_PASSWORD2"
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE") or "credentials.json"  # keep this file out of VCS
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME") or "Nuna(Database)"

ADMIN_IDS = [int(x) for x in (os.getenv("ADMIN_IDS") or "").split(',') if x.strip().isdigit()]

# Переменные кнопок
BUTTON_HOW_IT_WORKS = "Как это работает?"
BUTTON_CHAT = "Чат"
BUTTON_TRUST = "Почему мне можно доверять?"
BUTTON_BACK = "◀️ Назад"

# -----------------------------------------
# SYSTEM PROMPT Nuna
# -----------------------------------------
AI_SYSTEM_PROMPT = """
You are Nuna, don't medical, AI assistant for parents of infants and young children (0-3 years). Your main task is to help parents when their child is sick, feeling unwell, or when questions arise about care, development, and parenting of infants and young children.

STRICT LIMITATIONS:
You ONLY answer questions related to:
- infant and child health (0-3 years)
- newborn and infant care
- child development (physical, emotional, cognitive)
- feeding (breastfeeding, formula, complementary foods)
- infant and child sleep
- child safety
- parental anxieties and stress
- toddler behavior and parenting

IF a question is NOT about infancy, children, or parenting, respond:
"Извините, я специализируюсь только на вопросах здоровья и развития младенцев и детей раннего возраста. Я не могу помочь с этим вопросом. Если у вас есть вопросы о вашем малыше - я с радостью помогу! 💛"

YOUR KNOWLEDGE BASE includes:

TEMPERATURE AND FEVER:
- up to 38°C in infants over 3 months - observation, plenty of fluids, light clothing
- 38-38.5°C - can give fever reducer paracetamol/ibuprofen by age
- above 38.5°C - mandatory fever reducer, observation
- above 39°C or fever in child under 3 months - URGENT doctor or emergency
- seizures, lethargy, refusal to drink with fever - IMMEDIATE emergency

RUNNY NOSE AND COUGH:
- clear discharge in infant - saline in nose, air humidification, aspirator if needed
- green/yellow thick discharge more than 7-10 days - to pediatrician
- dry cough - air humidification, warm drinks if child over 6 months
- wet cough with phlegm - observation if no fever and breathing difficulty
- barking cough, wheezing, shortness of breath - URGENT doctor or emergency

VOMITING AND DIARRHEA:
- single vomiting without other symptoms - observation, fractional drinking
- multiple vomiting - dehydration risk, give water in small portions every 10-15 minutes
- vomiting plus fever plus lethargy - to doctor within hours
- projectile vomiting in infant after each feeding - to pediatrician
- diarrhea liquid stool more than 3-5 times per day - plenty of fluids, watch for dehydration signs
- blood in stool, black stool - URGENT doctor
- dehydration signs dry lips, rare urination, lethargy, sunken fontanelle - IMMEDIATE doctor or emergency

COLIC AND CRYING:
- colic normal up to 3-4 months, peak at 6-8 weeks
- help with colic: warmth on tummy, holding upright, white noise, rocking, tummy massage clockwise
- inconsolable crying more than 3 hours straight - to doctor to rule out other causes
- crying plus fever/vomiting/lethargy - URGENT doctor

FEEDING:
- breastfeeding on demand, frequent latching is normal
- spitting up after feeding normal if baby gaining weight
- refusal of breast/bottle plus lethargy - to doctor
- complementary foods start at 4-6 months, readiness signs sits with support, interest in food, fading of tongue-thrust reflex
- first complementary foods vegetable purees or cereals, one product at a time, observe reaction 3-5 days

SLEEP:
- newborns 16-18 hours per day, wake every 2-3 hours
- 3-6 months 14-16 hours, start sleeping longer at night
- 6-12 months 12-14 hours, 2-3 daytime naps
- sleep regressions at 4, 8-10, 12, 18 months normal and temporary
- safe sleep on back, firm mattress, no pillows/blankets until one year

EMERGENCY SITUATIONS - IMMEDIATE EMERGENCY:
- difficulty breathing, bluish lips/face
- loss of consciousness
- seizures
- temperature above 39°C in infant under 3 months
- severe dehydration sunken fontanelle, no urine more than 8 hours
- head injury with loss of consciousness or vomiting
- swallowing foreign object or chemical substance
- severe allergic reaction facial swelling, difficulty breathing

CHILD DEVELOPMENT:
- 0-3 months focus gaze, smile, holding head
- 3-6 months rolling over, grasping toys, cooing
- 6-9 months sitting, crawling, babbling
- 9-12 months standing with support, first steps, first words
- 12-18 months walking, 5-20 words, pointing gesture
- 18-24 months running, 2-word phrases, playing with other children
- developmental delay reason for pediatrician consultation, but each child develops at own pace

SAFETY:
- never leave infant unattended on high surfaces
- sleep on back to prevent SIDS
- car seat from birth, rear-facing until 2 years
- protect outlets, corners, stairs after crawling starts
- bath water temperature 36-37°C
- no small objects within reach choking risk

MEDICATIONS:
- fever reducers paracetamol from 3 months, ibuprofen from 6 months dosage by weight
- NEVER give aspirin to children
- any medications only after doctor consultation
- antibiotics only by doctor prescription, not for viral infections

Each Nuna response should consist of four blocks:

1) Calmness + support (1–2 sentences)

The goal is to immediately reduce anxiety.

Phrases:

“I understand that this can be scary. Let's figure it out together.”

“You're doing everything right. The fact that you wrote this shows that you care about your baby.”

“Good job asking. Let's calmly figure out the situation.”

This is key — the mother needs to feel that she is not alone.

2) A short and clear answer to “what is happening” (explanation)

Explain in simple, everyday language, without medical jargon.

For example:

“This often happens with babies in the first few months.”

“This looks like a typical reaction of the body.”

“Most often it is associated with...”

The goal is to name the phenomenon and normalize it.

3) A clear list of actions: “What to do right now”

This is a must-have.
The mother wants specifics.

The format is always the same:
a bulleted list of 3-5 points, without complex terms.

For example:

Check the temperature.

Gently lift the baby's head.

Give them some time to calm down.

Monitor their breathing and behavior.

This makes the response “quick to act on.”

4) When to see a doctor (if necessary)

Very gently, without intimidation.
Optional if the situation is potentially risky.

Format:

"If you notice this → it's best to seek help:
— ...
— ..."

The tone should be caring, not frightening.

5) Final encouragement (very short)

End the response with warmth.

Phrases:

“You're doing great ❤️”

“If anything changes, let me know, I'm here for you.”

“We'll figure this out together.”

This creates attachment to Nuna.

🌸 Example of a complete response (structure in action)
Request:

“My child's temperature has risen to 38.4. What should I do?”

Nuna's response (based on the structure):

1) Support
I understand how scary it can be when a fever rises. You're doing the right thing by asking — let's figure this out together.

2) Brief explanation
Babies often develop a fever when they have a virus or in response to stress — this is a normal reaction of the body.

3) Clear steps
Here's what to do now:
• Take their temperature again in 10–15 minutes.
• Give your baby small sips of water.
• Remove any extra clothing to make them more comfortable.
• You can give them fever reducers if they are very lethargic or clearly unwell.

4) When to see a doctor
If you notice:
— the temperature stays above 38.5 for more than 2–3 hours,
— the baby is not drinking well or is crying a lot,
— a rash or difficulty breathing has appeared — it is better to seek help.

5) Closing
You are doing everything right, really ❤️
Write if you want to clarify anything — I'm here for you.

COMMUNICATION PRINCIPLES:

1. BREVITY AND CLARITY - answer briefly, 3-5 sentences, without complex medical terms, in simple understandable language

2. SPECIFIC RECOMMENDATIONS - what to do RIGHT NOW, step-by-step instructions, concrete actions not general advice

3. URGENCY ASSESSMENT - clearly indicate how urgent the situation is: can wait until morning vs need doctor today vs urgent emergency

4. CALM AND SUPPORT - parents are stressed, be their support, calm but do not minimize the problem, empathy and understanding

5. DO NOT DIAGNOSE - you help orient but do not replace doctor, say this could be instead of this is definitely, when in doubt recommend doctor consultation

6. ALWAYS IN RUSSIAN LANGUAGE - all answers only in Russian

7. WITHOUT JUDGMENT - no phrases like you are doing it wrong, support not criticism, let's try instead of you must

YOUR VOICE AND TONE:
You are a calm, knowledgeable, supportive adult nearby. You do not panic, do not frighten, do not judge. You give parents the feeling: I can handle this. I am not alone. They will help me.

🧩 Authorization module (minimal)

Authorization logic:

If a person writes: 
Login: admin
Password: admin
then they can communicate without restrictions, ask the bot what requests there were, and generally use the admin panel.

If the login and password match the specified values → the bot considers the user authorized.

An authorized user gets full access to the bot's functions and responds to parents' requests in normal mode, without restrictions.

If the login or password is incorrect:

The bot responds politely:
“It seems that the data does not match. Please try again.”

And again asks for the login and password.

After successful authorization:

The bot says:
“Done! Now I can communicate with you in full mode.”

The bot then works as usual — responding, helping, analyzing.

Session:

Authorization is retained until the administrator types “logout.”

After logging out, the login and password must be re-entered.

REMEMBER:
You are Nuna. You are here not to replace a doctor. You are here to be support when parents are scared and confused. You help make the right decision here and now. Your goal is for parents to feel calmer and understand what to do.

Answer ONLY questions about infants, children, and parenting. Everything else politely decline. 💛
"""

# Состояния
WAITING_FOR_QUERY = 1
WAITING_FOR_FEEDBACK_RATING = 2
WAITING_FOR_FEEDBACK_REASON = 3

MSK = pytz.timezone('Europe/Moscow')

# In-memory cache for user data to reduce Google Sheets reads
USER_CACHE = {}
# seconds
CACHE_TTL = int(os.getenv("USER_CACHE_TTL", "60"))

# -----------------------------------------
# GOOGLE SHEETS ИНИЦИАЛИЗАЦИЯ
# -----------------------------------------
def init_google_sheets():
    """Инициализирует подключение к Google Sheets"""
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE, scopes=scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open(SPREADSHEET_NAME)
        return spreadsheet
    except Exception as e:
        logger.exception("Failed to init Google Sheets")
        raise

gs = None
users_sheet = None
feedback_sheet = None
payments_sheet = None

# Telegram Bot helper for sending notifications from webhook
BOT = Bot(token=TELEGRAM_TOKEN)

# -----------------------------------------
# USERS TABLE ФУНКЦИИ
# -----------------------------------------
def get_user_data(user_id):
    """Получает данные пользователя из Google Sheets"""
    if users_sheet is None:
        logger.error("users_sheet is None in get_user_data")
        return None
    # Try cache first
    try:
        key = str(user_id)
        entry = USER_CACHE.get(key)
        if entry and (_time.time() - entry.get('ts', 0)) < CACHE_TTL:
            return entry.get('data')
    except Exception:
        # cache shouldn't break the flow
        logger.exception("Cache read error")
    try:
        # Search only in the first column (user_id column) to avoid accidental matches
        col = users_sheet.col_values(1)
        try:
            idx = col.index(str(user_id)) + 1
        except ValueError:
            return None
        row = users_sheet.row_values(idx)
        # Normalize row length
        while len(row) < 8:
            row.append('')
        result = {
            'user_id': row[0],
            'username': row[1] or '',
            'tokens_balance': int(row[2]) if row[2] else 3,
            'subscription_status': row[3].upper() == 'TRUE' if row[3] else False,
            'subscription_end_date': row[4] or '',
            'last_token_reset': row[5] or '',
            'chat_history': row[6] or '',
            'chat_history_answer': row[7] or ''
        }
        try:
            USER_CACHE[str(user_id)] = {'data': result, 'ts': _time.time()}
        except Exception:
            logger.exception("Cache write error")
        return result
    except Exception as e:
        logger.exception(f"Error reading user data for {user_id}")
    return None

def create_user(user_id, username):
    """Создает новый профиль пользователя"""
    if users_sheet is None:
        logger.error("users_sheet is None in create_user")
        return
    try:
        today = datetime.now(MSK).strftime('%Y-%m-%d')
        # При создании нового пользователя подписка не активна и поле конца подписки пустое
        users_sheet.append_row([
            str(user_id),
            username or '',
            3,  # tokens_balance
            'FALSE',  # subscription_status
            '',  # subscription_end_date
            today,  # last_token_reset
            '',  # chat_history
            ''   # chat_history_answer
        ], value_input_option='USER_ENTERED')
        logger.info(f"Created user {user_id}")
        try:
            USER_CACHE[str(user_id)] = {
                'data': {
                    'user_id': str(user_id),
                    'username': username or '',
                    'tokens_balance': 3,
                    'subscription_status': False,
                    'subscription_end_date': '',
                    'last_token_reset': today,
                    'chat_history': '',
                    'chat_history_answer': ''
                },
                'ts': _time.time()
            }
        except Exception:
            logger.exception("Cache write error in create_user")
    except Exception as e:
        logger.exception(f"Error creating user: {e}")

def update_tokens(user_id, tokens):
    """Обновляет количество токенов"""
    if users_sheet is None:
        logger.error("users_sheet is None in update_tokens")
        return
    try:
        cell = users_sheet.find(str(user_id))
        if cell:
            users_sheet.update_cell(cell.row, 3, tokens)
            # update cache if present
            try:
                key = str(user_id)
                entry = USER_CACHE.get(key)
                if entry and isinstance(entry.get('data'), dict):
                    entry['data']['tokens_balance'] = int(tokens)
                    entry['ts'] = _time.time()
            except Exception:
                logger.exception("Cache update error in update_tokens")
    except Exception as e:
        logger.exception(f"Error updating tokens for {user_id}")

def update_subscription(user_id, status, end_date=None):
    """Обновляет статус подписки"""
    if users_sheet is None:
        logger.error("users_sheet is None in update_subscription")
        return
    try:
        cell = users_sheet.find(str(user_id))
        if cell:
            users_sheet.update_cell(cell.row, 4, 'TRUE' if status else 'FALSE')
            if end_date:
                users_sheet.update_cell(cell.row, 5, end_date)
            if status:
                users_sheet.update_cell(cell.row, 3, 999999)
            # update cache
            try:
                key = str(user_id)
                entry = USER_CACHE.get(key)
                if entry and isinstance(entry.get('data'), dict):
                    entry['data']['subscription_status'] = bool(status)
                    if end_date:
                        entry['data']['subscription_end_date'] = end_date
                    if status:
                        entry['data']['tokens_balance'] = 999999
                    entry['ts'] = _time.time()
            except Exception:
                logger.exception("Cache update error in update_subscription")
    except Exception as e:
        logger.exception(f"Error updating subscription for {user_id}")

def add_to_history(user_id, query, answer):
    """Добавляет запрос и ответ в историю чата"""
    if users_sheet is None:
        logger.error("users_sheet is None in add_to_history")
        return
    try:
        cell = users_sheet.find(str(user_id))
        if cell:
            row = users_sheet.row_values(cell.row)
            # Ensure length
            while len(row) < 8:
                row.append('')
            timestamp = datetime.now(MSK).strftime('%Y-%m-%d %H:%M:%S')
            new_q = (row[6] or '') + f"\n[{timestamp}] {query}"
            new_a = (row[7] or '') + f"\n[{timestamp}] {answer}"
            users_sheet.update_cell(cell.row, 7, new_q.strip())
            users_sheet.update_cell(cell.row, 8, new_a.strip())
            # update cache
            try:
                key = str(user_id)
                entry = USER_CACHE.get(key)
                if entry and isinstance(entry.get('data'), dict):
                    entry['data']['chat_history'] = (entry['data'].get('chat_history') or '') + f"\n[{timestamp}] {query}"
                    entry['data']['chat_history_answer'] = (entry['data'].get('chat_history_answer') or '') + f"\n[{timestamp}] {answer}"
                    entry['ts'] = _time.time()
            except Exception:
                logger.exception("Cache update error in add_to_history")
    except Exception as e:
        logger.exception(f"Error saving history for {user_id}")

def reset_tokens_for_user(user_id):
    """Сбрасывает токены для пользователя (ежедневно)"""
    if users_sheet is None:
        logger.error("users_sheet is None in reset_tokens_for_user")
        return
    try:
        cell = users_sheet.find(str(user_id))
        if cell:
            data = get_user_data(user_id)
            if not data:
                return
            # Сбрасываем только если подписка неактивна и это новый день
            if not data['subscription_status']:
                today = datetime.now(MSK).strftime('%Y-%m-%d')
                if data.get('last_token_reset') != today:
                    users_sheet.update_cell(cell.row, 3, 3)  # 3 токена
                    users_sheet.update_cell(cell.row, 6, today)  # Обновляем дату сброса
    except Exception as e:
        logger.exception(f"Error resetting tokens for user {user_id}")

def check_and_reset_all_tokens():
    """Ежедневный сброс токенов для всех пользователей"""
    if users_sheet is None:
        logger.error("users_sheet is None in check_and_reset_all_tokens")
        return
    try:
        all_records = users_sheet.get_all_records()
        today = datetime.now(MSK).strftime('%Y-%m-%d')
        for i, record in enumerate(all_records, start=2):
            try:
                sub_status = record.get('subscription_status', 'FALSE')
                if isinstance(sub_status, bool):
                    sub_active = sub_status
                else:
                    sub_active = str(sub_status).upper() == 'TRUE'
                if sub_active:
                    end_date = record.get('subscription_end_date', '')
                    if end_date:
                        try:
                            end_dt = datetime.strptime(end_date.split()[0], '%Y-%m-%d')
                            if end_dt <= datetime.now(MSK):
                                users_sheet.update_cell(i, 4, 'FALSE')
                                users_sheet.update_cell(i, 3, 3)
                        except Exception:
                            pass
                else:
                    last_reset = record.get('last_token_reset', '')
                    if last_reset != today:
                        users_sheet.update_cell(i, 3, 3)
                        users_sheet.update_cell(i, 6, today)
            except Exception:
                logger.exception("Error processing record during token reset loop")
    except Exception as e:
        logger.exception("Error in check_and_reset_all_tokens")


def get_users_stats():
    """Собирает базовую статистику по пользователям из Google Sheets"""
    if users_sheet is None:
        logger.error("users_sheet is None in get_users_stats")
        return None
    try:
        all_records = users_sheet.get_all_records()
        total = len(all_records)
        subscribers = 0
        active_subs = 0
        zero_tokens = 0
        tokens = []
        from_date = datetime.now(MSK)
        today = from_date.strftime('%Y-%m-%d')
        new_today = 0
        for rec in all_records:
            sub_status = rec.get('subscription_status', 'FALSE')
            if isinstance(sub_status, bool):
                sub_active = sub_status
            else:
                sub_active = str(sub_status).upper() == 'TRUE'
            if sub_active:
                subscribers += 1
                end_date = rec.get('subscription_end_date', '')
                if end_date:
                    try:
                        end_dt = datetime.strptime(end_date.split()[0], '%Y-%m-%d')
                        if end_dt >= datetime.now(MSK).date():
                            active_subs += 1
                    except Exception:
                        pass
            try:
                tb = int(rec.get('tokens_balance') or 0)
            except Exception:
                tb = 0
            tokens.append(tb)
            if tb <= 0 and not sub_active:
                zero_tokens += 1
            last_reset = rec.get('last_token_reset', '')
            if last_reset == today:
                new_today += 1

        avg_tokens = int(sum(tokens) / len(tokens)) if tokens else 0
        return {
            'total': total,
            'subscribers': subscribers,
            'active_subscriptions': active_subs,
            'zero_tokens': zero_tokens,
            'avg_tokens': avg_tokens,
            'new_today': new_today
        }
    except Exception:
        logger.exception("Error collecting users stats")
        return None


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if ADMIN_IDS and user.id not in ADMIN_IDS:
        await update.message.reply_text("Доступ запрещён.")
        return
    stats = get_users_stats()
    if not stats:
        await update.message.reply_text("Статистика недоступна — проверьте подключение к Google Sheets.")
        return
    text = (
        f"📊 Статистика пользователей:\n"
        f"Всего: {stats['total']}\n"
        f"Подписчиков (всего): {stats['subscribers']}\n"
        f"Активных подписок: {stats['active_subscriptions']}\n"
        f"Пользователей с 0 токенов: {stats['zero_tokens']}\n"
        f"Средний баланс токенов: {stats['avg_tokens']}\n"
        f"Новых сегодня: {stats['new_today']}\n"
    )
    await update.message.reply_text(text)

# -----------------------------------------
# FEEDBACK TABLE
# -----------------------------------------
def save_feedback(user_id, username, choice, user_answer=""):
    """Сохраняет фидбек в отдельную таблицу"""
    if feedback_sheet is None:
        logger.error("feedback_sheet is None in save_feedback")
        return
    try:
        now = datetime.now(MSK).strftime('%Y-%m-%d %H:%M:%S')
        feedback_sheet.append_row([
            str(user_id),
            username or '',
            choice,  # Yes or No
            user_answer,
            now
        ], value_input_option='USER_ENTERED')
        logger.info(f"Saved feedback from {user_id}: {choice}")
    except Exception as e:
        logger.exception(f"Error saving feedback: {e}")

# -----------------------------------------
# AI RESPONSE (асинхронно, не блокирует loop)
# -----------------------------------------
async def get_ai_response(query):
    """Асинхронное обращение к OpenRouter через httpx AsyncClient"""
    start_ts = datetime.now(MSK)
    logger.info("AI request started (httpx)")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "x-ai/grok-4.1-fast",
                    "messages": [
                        {"role": "system", "content": AI_SYSTEM_PROMPT},
                        {"role": "user", "content": query}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 1000
                }
            )
        dur = (datetime.now(MSK) - start_ts).total_seconds()
        logger.info(f"AI request finished in {dur:.2f}s")
        if resp.status_code == 200:
            data = resp.json()
            if "choices" in data and len(data["choices"]) > 0:
                choice = data["choices"][0]
                if isinstance(choice, dict):
                    if 'message' in choice and isinstance(choice['message'], dict) and 'content' in choice['message']:
                        return choice['message']['content']
                    if 'content' in choice:
                        return choice['content']
                return str(choice)
            return "AI вернул пустой ответ. Попробуйте ещё раз."
        else:
            logger.error(f"OpenRouter API error: {resp.status_code} - {getattr(resp, 'text', '')}")
            return "Произошла ошибка при обработке. Пожалуйста, попробуйте позже."
    except Exception as e:
        logger.exception("AI error (httpx)")
        return "Произошла техническая ошибка. Попробуйте позже."

# -----------------------------------------
# ROBOKASSA LINK
# -----------------------------------------
def generate_payment_link(user_id, amount=500):
    """Генерирует ссылку для оплаты подписки"""
    try:
        inv_id = int(datetime.now().timestamp() * 1000)
        signature_str = f"{ROBOKASSA_LOGIN}:{amount}:{inv_id}:{ROBOKASSA_PASSWORD1}"
        signature = hashlib.md5(signature_str.encode()).hexdigest()
        link = (
            f"https://auth.robokassa.ru/Merchant/Index.aspx?"
            f"MerchantLogin={ROBOKASSA_LOGIN}&OutSum={amount}&InvId={inv_id}"
            f"&Description=Подписка+Nuna+на+месяц&SignatureValue={signature}&Shp_user_id={user_id}"
        )
        return link
    except Exception as e:
        logger.exception("Error generating payment link")
        return None

# -----------------------------------------
# КЛАВИАТУРЫ
# -----------------------------------------
def kb_main():
    return ReplyKeyboardMarkup([
        [BUTTON_HOW_IT_WORKS],
        [BUTTON_CHAT],
        [BUTTON_TRUST]
    ], resize_keyboard=True)

def kb_back():
    return ReplyKeyboardMarkup([[BUTTON_BACK]], resize_keyboard=True)

def kb_feedback():
    return ReplyKeyboardMarkup([["👍 Да", "👎 Нет"]], resize_keyboard=True)

# -----------------------------------------
# HANDLERS
# -----------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Быстрая отправка приветствия пользователю — без ожидания операций с Google Sheets
    welcome_text = (
        "Привет, <b>я — Nuna 💛</b>\n"
        "И я здесь, чтобы быть твоей опорой, когда становится тревожно.\n\n"
        "<b>Первые месяцы с малышом — это испытание даже для самых сильных.</b>\n"
        "Ты можешь быть уставшей, испуганной, растерянной.\n"
        "Это нормально.\n"
        "<b>Никто не рождается \"подготовленной мамой\".</b>\n\n"
        "Когда малыш плачет, чихает, кашляет, не спит, срыгивает, когда температура растёт —\n"
        "всё внутри сжимается.\n"
        "В такие моменты хочется только одного:\n"
        "<b>чтобы рядом был кто-то спокойный, уверенный, кто подскажет, что делать.</b>\n\n"
        "И я именно для этого здесь.\n\n"
        "<b>Nuna — это не просто ответы.</b>\n"
        "Это чувство:\n"
        "\"Я справлюсь. Я не одна. Мне подскажут сейчас, не через час\".\n\n"
        "<b>✨ Как пользоваться:</b>\n"
        " — нажми кнопку <b>«Чат»</b>, чтобы задать вопрос;\n"
        " — каждый день у тебя есть <b>3 бесплатных ответа;</b>\n"
        " — если нужно больше — можно оформить <b>подписку</b>, но это необязательно.\n"
        "\n"
        "Пиши, что случилось.\n"
        "<b>Давай пройдём это вместе 💛</b>"
       
    )
    try:
        await update.message.reply_photo(
            photo="https://i.postimg.cc/gJfJN0zL/Privetstvennoe-soobsenie.jpg",
            caption=welcome_text,
            parse_mode='HTML',
            reply_markup=kb_main()
        )
    except Exception:
        logger.exception("Error sending welcome message")
        await update.message.reply_text(welcome_text, parse_mode='HTML', reply_markup=kb_main())

    # Фоновые операции: убедиться, что пользователь есть в таблице, и сбросить токены при необходимости
    async def bg_user_setup():
        try:
            try:
                if not get_user_data(user.id):
                    create_user(user.id, user.username)
            except Exception:
                logger.exception("Background: error ensuring user exists")
            try:
                reset_tokens_for_user(user.id)
            except Exception:
                logger.exception("Background: error resetting tokens for user")
        except Exception:
            logger.exception("Unexpected error in bg_user_setup")

    try:
        asyncio.create_task(bg_user_setup())
    except Exception:
        # в крайнем случае выполнить синхронно (и залогировать)
        try:
            if not get_user_data(user.id):
                create_user(user.id, user.username)
            reset_tokens_for_user(user.id)
        except Exception:
            logger.exception("Fallback: error running user setup synchronously")

async def how_it_works(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>Очень просто.</b>\n\n"
        "Ты пишешь, что происходит: симптом, тревога, ситуация.\n\n"
        "Я разбираю твоё сообщение и отвечаю так, как помогает здесь и сейчас.\n\n"
        "<b>Только самое важное:</b>\n"
        "— что значит ситуация,\n"
        "— что тебе сделать прямо сейчас,\n"
        "— как понять, что это нормально,\n"
        "— когда стоит показать малыша врачу.\n\n"
        "Без осуждения, без давления, без \"мамы делают неправильно\".\n\n"
        "<b>Это как иметь рядом спокойного взрослого,\n"
        "который знает, что делать, когда у тебя внутри буря.</b>\n\n"
        "Nuna — это не про информацию.\n"
        "Это про поддержку и уверенность.\n\n"
        "<b>Нажми кнопку «Чат» и просто напиши, что волнует твоего малыша или тебя💛</b>"
    )
    try:
        await update.message.reply_photo("https://i.postimg.cc/rwFh3kvf/Frame-1.jpg",
                                        caption=text, parse_mode="HTML", reply_markup=kb_back())
    except Exception:
        logger.exception("Error sending how_it_works")
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb_back())

async def why_trust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Ты можешь опираться на меня, потому что:\n\n"
        "<b>✨ Я создана специально для родителей малышей — я не отвечаю про всё на свете, как обычные чат-боты.</b>\n\n"
        "<b>✨ Я обучена на сотнях реальных ситуаций, с которыми сталкиваются молодые родители</b>\n\n"
        "<b>✨ Я говорю человеческим языком, без переумничания и сложных терминов.</b>\n\n"
        "<b>✨ Я всегда на твоей стороне — не оцениваю, не пугаю, не критикую.</b>\n\n"
        "<b>✨ Моя цель — чтобы тебе стало спокойнее, а не чтобы показать, как \"правильно\".</b>\n\n"
        "Это не про идеальное родительство.\n"
        "Это про то, чтобы <b>ты не была одна с тревогой и незнанием что делать.</b>"
    )
    try:
        await update.message.reply_photo("https://i.postimg.cc/1XQBHhG7/Frame-3.jpg",
                                        caption=text, parse_mode="HTML", reply_markup=kb_back())
    except Exception:
        logger.exception("Error sending why_trust")
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb_back())

async def chat_intro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = get_user_data(user.id)
    if not data:
        create_user(user.id, user.username)
        data = get_user_data(user.id)
    reset_tokens_for_user(user.id)
    data = get_user_data(user.id)
    tokens_info = f"Ваш баланс: {data['tokens_balance']} 🪙" if data else "Ваш баланс недоступен"
    if data and data.get('subscription_status'):
        tokens_info = "✨ У вас активная подписка - безлимит! ✨"
    text = "Напиши что случилось — и Nuna подскажет, что делать <b>прямо сейчас.</b>\n\n" + tokens_info
    try:
        await update.message.reply_photo("https://i.postimg.cc/J0rJ013v/variant-2.jpg",
                                        caption=text, parse_mode="HTML", reply_markup=kb_back())
    except Exception:
        logger.exception("Error sending chat_intro")
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb_back())
    return WAITING_FOR_QUERY

async def handle_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    if text == BUTTON_BACK:
        await start(update, context)
        return ConversationHandler.END
    data = get_user_data(user.id)
    if not data:
        create_user(user.id, user.username)
        data = get_user_data(user.id)
    if not data:
        await update.message.reply_text("Произошла ошибка с профилем — попробуйте /start")
        return ConversationHandler.END
    if data["tokens_balance"] <= 0 and not data["subscription_status"]:
        pay_link = generate_payment_link(user.id)
        if pay_link:
            pay_text = (
                f"<b>Бесплатные ответы закончились 💛</b>\n"
                "Но твоя забота о малыше — <b>нет.</b>\n\n"
                "Подписка Nuna за <b>199 ₽ / месяц</b> даёт тебе:\n\n"
                "<b>💛 Ответы в момент тревоги</b>\n"
                "<b>💛 Поддержку ночью,</b> когда никто не отвечает\n"
                "<b>💛 Пошаговые инструкции</b> при симптомах\n"
                "💛 <b>Помощь,</b> когда малыш плачет, не спит или температурит\n"
                "💛 <b>Уверенность,</b> что ты всё делаешь правильно\n\n"
                "<b>1 вопрос может снять панику.</b>\n"
                "А подписка — это <b>спокойствие каждый день.</b>\n\n"
                f"👉 <a href=\"{pay_link}\">Оформить подписку — 199 ₽/мес</a>"
            )
            await update.message.reply_text(pay_text, parse_mode="HTML")
        else:
            await update.message.reply_text("⚠️ У вас закончились токены! Свяжитесь с поддержкой.")
        return ConversationHandler.END
    await update.message.reply_text("⏳ Обрабатываю ваш запрос...")
    answer = await get_ai_response(text)

    # Обновление токенов и сохранение истории выполняем в фоне,
    # чтобы не блокировать отправку ответа пользователю (Google Sheets может быть медленным).
    async def bg_updates():
        try:
            if not data["subscription_status"]:
                try:
                    update_tokens(user.id, data["tokens_balance"] - 1)
                except Exception:
                    logger.exception("Error decrementing tokens")
            try:
                add_to_history(user.id, text, answer)
            except Exception:
                logger.exception("Error saving history")
        except Exception:
            logger.exception("Unexpected error in bg_updates")

    try:
        asyncio.create_task(bg_updates())
    except Exception:
        # Fallback to synchronous if create_task fails
        try:
            update_tokens(user.id, data["tokens_balance"] - 1)
            add_to_history(user.id, text, answer)
        except Exception:
            logger.exception("Error during fallback updates")
    # Отправляем ответ от AI как plain text (без HTML-парсинга), чтобы избежать проблем с встраиваемыми тегами
    safe_answer = str(answer).strip()
    try:
        await update.message.reply_text(safe_answer)
    except Exception:
        logger.exception("Error sending AI answer")
        await update.message.reply_text("Извините, не удалось отправить ответ. Попробуйте позже.")
    await update.message.reply_text("👇 Помог ли вам мой ответ?", reply_markup=kb_feedback())
    return WAITING_FOR_FEEDBACK_RATING

async def handle_feedback_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message.text
    if msg == "👍 Да":
        save_feedback(user.id, user.username, "Yes")
        await update.message.reply_text(
            "🙏 <b>Спасибо за обратную связь!</b>\nРада, что смогла помочь 💛\n\nЧем еще я могу помочь?",
            parse_mode="HTML",
            reply_markup=kb_main()
        )
        return ConversationHandler.END
    elif msg == "👎 Нет":
        await update.message.reply_text(
            "Мне жаль, что ответ не помог 😔\n\nПожалуйста, скажи подробнее - что было не так? Это поможет мне стать лучше 💛",
            reply_markup=ReplyKeyboardRemove()
        )
        return WAITING_FOR_FEEDBACK_REASON
    if msg == BUTTON_BACK:
        await start(update, context)
        return ConversationHandler.END
    await update.message.reply_text("Пожалуйста, нажмите одну из кнопок: 👍 или 👎", reply_markup=kb_feedback())
    return WAITING_FOR_FEEDBACK_RATING

async def handle_feedback_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    save_feedback(user.id, user.username, "No", text)
    await update.message.reply_text(
        "🙏 <b>Спасибо за честный отзыв!</b> 💛\n\nЯ обязательно использую это, чтобы улучшить свои ответы.\nДавай попробуем еще раз!",
        parse_mode="HTML",
        reply_markup=kb_main()
    )
    return ConversationHandler.END

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == BUTTON_HOW_IT_WORKS:
        await how_it_works(update, context)
        return ConversationHandler.END
    elif text == BUTTON_TRUST:
        await why_trust(update, context)
        return ConversationHandler.END
    elif text == BUTTON_BACK:
        await start(update, context)
        return ConversationHandler.END
    elif text == BUTTON_CHAT:
        return await chat_intro(update, context)
    


async def reset_tokens_daily(context):
    try:
        logger.info("🔄 Выполняется ежедневный сброс токенов...")
        check_and_reset_all_tokens()
        logger.info("✅ Сброс токенов завершен")
    except Exception:
        logger.exception("Error in reset_tokens_daily")


# -----------------------------------------
# ROBOKASSA WEBHOOK (Flask) - module level
# -----------------------------------------
flask_app = Flask(__name__)


@flask_app.route('/robokassa/result', methods=['GET', 'POST'])
def robokassa_result():
    try:
        # Robokassa sends parameters OutSum, InvId, SignatureValue and optionally Shp_user_id
        out_sum = request.values.get('OutSum')
        inv_id = request.values.get('InvId')
        signature = request.values.get('SignatureValue')
        shp_user_id = request.values.get('Shp_user_id')
        logger.info(f"Robokassa result received: OutSum={out_sum}, InvId={inv_id}, Shp_user_id={shp_user_id}")
        if not out_sum or not inv_id or not signature or not shp_user_id:
            logger.warning("Robokassa: missing parameters")
            return Response("Bad Request", status=400)

        # Validate signature using Password2
        sig_str = f"{out_sum}:{inv_id}:{ROBOKASSA_PASSWORD2}"
        expected = hashlib.md5(sig_str.encode()).hexdigest()
        if expected.lower() != signature.lower():
            logger.warning(f"Robokassa: invalid signature. expected={expected} got={signature}")
            return Response("Invalid signature", status=400)

        # Optional: verify amount (OutSum) matches expected subscription price
        try:
            expected_amount = float(500)
            if float(out_sum) != expected_amount:
                logger.warning(f"Robokassa: payment amount {out_sum} differs from expected {expected_amount}")
        except Exception:
            logger.exception("Robokassa: couldn't parse OutSum")

        # Ensure user exists in Users sheet; create if missing
        try:
            if get_user_data(shp_user_id) is None:
                logger.info(f"Robokassa: user {shp_user_id} not found in Users sheet - creating user row")
                try:
                    # create_user will append a new row; username unknown at payment time
                    create_user(shp_user_id, '')
                except Exception:
                    logger.exception("Robokassa: failed to create user row")
        except Exception:
            logger.exception("Robokassa: error checking/creating user")

        # Protect against duplicate processing: check Payments sheet for InvId
        try:
            if payments_sheet is not None:
                # search first column (InvId)
                col = payments_sheet.col_values(1)
                if str(inv_id) in col:
                    logger.info(f"Robokassa: InvId {inv_id} already processed, ignoring duplicate")
                    return Response(f"OK{inv_id}", status=200)
        except Exception:
            logger.exception("Robokassa: error checking Payments sheet for duplicate InvId")

        # Mark subscription active and set end date +30 days. Retry a few times if Google Sheets temporarily fails.
        attempts = 0
        success = False
        while attempts < 3 and not success:
            try:
                end_date = (datetime.now(MSK) + timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
                update_subscription(shp_user_id, True, end_date)
                logger.info(f"Robokassa: subscription enabled for {shp_user_id} until {end_date}")
                success = True
            except Exception:
                attempts += 1
                logger.exception("Robokassa: error updating subscription, retrying")
                _time.sleep(1)

        if not success:
            logger.error("Robokassa: failed to update subscription after retries")
            return Response("Internal error", status=500)

        # Record the payment in Payments sheet and notify the user
        try:
            if payments_sheet is not None:
                now = datetime.now(MSK).strftime('%Y-%m-%d %H:%M:%S')
                payments_sheet.append_row([
                    str(inv_id),
                    str(shp_user_id),
                    str(out_sum),
                    now,
                    'OK'
                ], value_input_option='USER_ENTERED')
        except Exception:
            logger.exception("Robokassa: failed to append payment record to Payments sheet")

        # Try to notify the user in Telegram that subscription is active
        try:
            try:
                chat_id = int(shp_user_id)
                BOT.send_message(chat_id=chat_id, text=f"✅ Оплата получена, подписка активирована до {end_date}.")
            except Exception:
                logger.exception("Robokassa: failed to send Telegram notification to user")
        except Exception:
            logger.exception("Robokassa: notification error")

        # According to Robokassa, respond with OK{InvId}
        return Response(f"OK{inv_id}", status=200)
    except Exception:
        logger.exception("Unexpected error in Robokassa result handler")
        return Response("Internal error", status=500)

# -----------------------------------------
# MAIN
# -----------------------------------------
def main():
    global gs, users_sheet, feedback_sheet
    try:
        gs = init_google_sheets()
        users_sheet = gs.worksheet("Users")
        feedback_sheet = gs.worksheet("Feedback")
        # Ensure Payments sheet exists; if not, create it with header
        try:
            global payments_sheet
            try:
                payments_sheet = gs.worksheet("Payments")
            except Exception:
                logger.info("Payments sheet not found — creating new Payments sheet")
                payments_sheet = gs.add_worksheet(title="Payments", rows=1000, cols=10)
                payments_sheet.append_row(["InvId", "UserId", "OutSum", "Timestamp", "Status"], value_input_option='USER_ENTERED')
        except Exception:
            logger.exception("Failed to ensure Payments sheet exists")
        logger.info("✅ Google Sheets инициализирована успешно")
    except Exception:
        logger.exception("❌ Ошибка инициализации Google Sheets")
        return

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Start Flask server in a background thread to receive Robokassa callbacks
    def run_flask():
        try:
            # Listen on port 5000 by default; change if needed
            flask_app.run(host='0.0.0.0', port=5000)
        except Exception:
            logger.exception("Failed to start Flask server for Robokassa webhook")

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask server for Robokassa webhook started on port 5000 (background thread)")

    # ConversationHandler только для Чата и обратной связи
    # Use escaped regex for button texts which may contain special characters
    button_chat_re = f"^{re.escape(BUTTON_CHAT)}$"
    buttons_group_re = f"^({re.escape(BUTTON_HOW_IT_WORKS)}|{re.escape(BUTTON_TRUST)}|{re.escape(BUTTON_BACK)}|{re.escape(BUTTON_CHAT)})$"

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(button_chat_re), chat_intro)],
        states={
            WAITING_FOR_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_query),
            ],
            WAITING_FOR_FEEDBACK_RATING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_feedback_rating),
            ],
            WAITING_FOR_FEEDBACK_REASON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_feedback_reason),
            ],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    # Основные команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(conv)

    # Единый обработчик кнопок вне ConversationHandler
    app.add_handler(
        MessageHandler(
             filters.TEXT & filters.Regex(buttons_group_re),
        handle_buttons
        )
    )

    # Планирование ежедневного сброса токенов
    job_queue = app.job_queue
    job_queue.run_daily(
        reset_tokens_daily,
        time=time(hour=0, minute=0, tzinfo=MSK),
        name="reset_tokens_job"
    )

    logger.info("⏰ Задача ежедневного сброса токенов запланирована на 00:00 МСК")
    logger.info("🚀 Бот Nuna запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
