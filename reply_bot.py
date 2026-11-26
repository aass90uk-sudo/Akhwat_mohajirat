import json
import os
import time
import requests
from groq import Groq

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

if not TELEGRAM_TOKEN:
    raise ValueError("المتغير TELEGRAM_TOKEN غير موجود")
if not CHANNEL_ID:
    raise ValueError("المتغير CHANNEL_ID غير موجود")
if not GROQ_API_KEY:
    raise ValueError("المتغير GROQ_API_KEY غير موجود")

if not CHANNEL_ID.startswith("@") and not CHANNEL_ID.startswith("-100"):
    CHANNEL_ID = f"@{CHANNEL_ID}"

client = Groq(api_key=GROQ_API_KEY)

DATA_DIR = "/data" if os.path.exists("/data") else "."
SESSIONS_FILE = os.path.join(DATA_DIR, "reply_sessions.json")
WELCOME_MESSAGE = "أهلاً بك 🤍\n\nأنا بوت ريحانة المغرب للردود الذكية، اسألني أي سؤال يخص الوعي الزوجي أو الصحة النفسية أو العلاقات الأسرية وسأجيبك."

MODELS_TO_USE = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]

SYSTEM_PROMPT = """أنت مساعد ذكي متخصص في الوعي الزوجي، الصحة النفسية والجسدية في الزواج، والعلاقات الأسرية، تعمل ضمن قناة ريحانة المغرب.

مهمتك: الرد على أسئلة واستفسارات المشتركين التي تأتي عبر تعليقاتهم تحت منشورات القناة.

الشروط:
- رد باللهجة السعودية أو الخليجية البيضاء، بأسلوب راقٍ ومحافظ ودافئ وقريب من القلب.
- اجعل الرد مختصراً ومركزاً (من سطر إلى ثلاثة أسطر) لأنه رد تعليق، وليس مقالاً.
- إن كان السؤال خارج تخصصك، اعتذر بلطف واقترح أن يطرح سؤالاً في مجال الوعي الزوجي أو النفسي.
- تجنب أي ألفاظ خادشة للحياء، وتجنب اللغة الأكاديمية الجافة.
- لا تستخدم تنسيق HTML أو Markdown، اكتب نصاً عادياً فقط.
- لا تكتب أي مقدمة مثل "بالطبع" أو "حسناً"، اكتب الرد مباشرة.
"""

SESSION_TIMEOUT = 60 * 60
MAX_MESSAGE_LENGTH = 4000


def load_sessions():
    if not os.path.exists(SESSIONS_FILE):
        return {}
    try:
        with open(SESSIONS_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception as error:
        print(f"تعذر تحميل الجلسات السابقة: {error}")
        return {}


def save_sessions(sessions):
    try:
        with open(SESSIONS_FILE, "w", encoding="utf-8") as file:
            json.dump(sessions, file, ensure_ascii=False, indent=2)
    except Exception as error:
        print(f"تعذر حفظ الجلسات: {error}")


def prune_sessions(sessions, now):
    fresh = {}
    for key, messages in sessions.items():
        kept = [m for m in messages if now - m.get("ts", 0) < SESSION_TIMEOUT]
        if kept:
            fresh[key] = kept
    return fresh


def build_session_key(chat_id, topic_id):
    return f"{chat_id}:{topic_id if topic_id else 'general'}"


def generate_reply(user_name, question, history):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in history:
        role = "assistant" if item.get("role") == "assistant" else "user"
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        messages.append({"role": role, "content": content[:2000]})

    user_content = f"السائل اسمه: {user_name}\n\nالتعليق: {question}"
    messages.append({"role": "user", "content": user_content})

    last_error = None
    for model_name in MODELS_TO_USE:
        try:
            print(f"جاري توليد الرد باستخدام النموذج: {model_name}")
            completion = client.chat.completions.create(
                messages=messages,
                model=model_name,
                temperature=0.8,
                max_tokens=500,
            )
            if not completion.choices:
                raise ValueError("لم يعد النموذج أي نص")
            generated_text = completion.choices[0].message.content
            if not generated_text or not generated_text.strip():
                raise ValueError("النص الناتج فارغ")
            return generated_text.strip()
        except Exception as error:
            last_error = error
            print(f"تعذر استخدام {model_name}: {str(error)[:200]}")

    raise RuntimeError(f"فشلت جميع نماذج Groq: {last_error}")


def call_telegram(method, payload):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    try:
        response = requests.post(url, json=payload, timeout=90)
        try:
            response_data = response.json()
        except ValueError:
            response_data = {"ok": False, "description": response.text}
        if response.status_code == 200 and response_data.get("ok") is True:
            return True, response_data
        return False, response_data
    except requests.RequestException as error:
        return False, {"ok": False, "description": f"خطأ في الاتصال بتيليجرام: {error}"}


def split_message(text):
    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]
    return [text[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(text), MAX_MESSAGE_LENGTH)]


def reply_to_comment(chat_id, message_id, text, thread_id=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
        "reply_to_message_id": message_id,
        "allow_sending_without_reply": True,
    }
    if thread_id:
        payload["message_thread_id"] = thread_id
    return call_telegram("sendMessage", payload)


def is_welcome_trigger(text):
    if not text:
        return False
    stripped = text.strip()
    if stripped == "/start":
        return True
    return "أهلاً" in stripped or "اهلا" in stripped


def extract_linked_channel(message):
    """يعيد معرف قناة النقاش المرتبطة إن كانت المجموعة مربوطة بقناة."""
    sender_chat = message.get("sender_chat") or {}
    if sender_chat.get("type") != "channel":
        return None
    return sender_chat.get("id")


def handle_update(update, sessions):
    message = update.get("message")
    if not message:
        return sessions

    chat = message.get("chat") or {}

    # 1) تجاهل أي رسالة قادمة من القناة نفسها (منشورات القناة)
    if chat.get("type") == "channel":
        return sessions

    # 2) تجاهل الرسائل المنشورة تلقائياً نيابة عن القناة
    if extract_linked_channel(message):
        return sessions

    # 3) تجاهل الرسائل الموجهة تلقائياً من القناة إلى مجموعة النقاش
    if message.get("is_automatic_forward"):
        return sessions

    # قبول المجموعات ومجموعات النقاش suppergroup والخاص فقط
    chat_type = chat.get("type")
    if chat_type not in ("group", "supergroup", "private"):
        return sessions

    text = message.get("text") or message.get("caption") or ""
    if not text.strip():
        return sessions

    chat_id = chat.get("id")
    if chat_id is None:
        return sessions

    message_id = message.get("message_id")
    message_thread_id = message.get("message_thread_id")
    from_user = message.get("from") or {}
    user_name = (
        from_user.get("first_name")
        or from_user.get("username")
        or "صديقنا"
    )

    if is_welcome_trigger(text):
        if chat_type == "private":
            success, response_info = call_telegram("sendMessage", {
                "chat_id": chat_id,
                "text": WELCOME_MESSAGE,
            })
            if not success:
                print(f"فشل إرسال رسالة الترحيب: {response_info}")
        return sessions

    question = text.strip()
    session_key = build_session_key(chat_id, message_thread_id)
    sessions = prune_sessions(sessions, int(time.time()))
    history = sessions.get(session_key, [])

    try:
        answer = generate_reply(user_name, question, history)
    except Exception as error:
        print(f"تعذر توليد الرد: {error}")
        return sessions

    for chunk in split_message(answer):
        success, response_info = reply_to_comment(chat_id, message_id, chunk, message_thread_id)
        if not success:
            print(f"فشل إرسال الرد: {response_info}")
            return sessions

    new_history = history + [
        {"role": "user", "content": question, "ts": int(time.time())},
        {"role": "assistant", "content": answer, "ts": int(time.time())},
    ]
    sessions[session_key] = new_history[-10:]
    save_sessions(sessions)
    return sessions


def main():
    print("بوت الردود بدأ العمل")
    sessions = load_sessions()
    offset = None
    while True:
        payload = {"timeout": 30, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        success, response = call_telegram("getUpdates", payload)
        if not success:
            print(f"تعذر جلب التحديثات: {response}")
            time.sleep(5)
            continue

        for update in response.get("result", []):
            offset = update["update_id"] + 1
            try:
                sessions = handle_update(update, sessions)
            except Exception as error:
                print(f"خطأ أثناء معالجة التحديث: {error}")
        save_sessions(sessions)


if __name__ == "__main__":
    main()
      
