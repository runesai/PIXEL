from flask import Flask, request
from pymessenger.bot import Bot
from dotenv import load_dotenv
from langdetect import detect
from googleapiclient.discovery import build
from google.oauth2 import service_account
from datetime import datetime
import os
from openai import OpenAI
import requests
import difflib
import random

# Load environment variables
load_dotenv()
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
PAGE_ACCESS_TOKEN = os.getenv('PAGE_ACCESS_TOKEN')
VERIFY_TOKEN = os.getenv('VERIFY_TOKEN')
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = 'credentials.json'

app = Flask(__name__)
bot = Bot(PAGE_ACCESS_TOKEN)

PIXEL_PERSONALITY = {
    'en': "You are PIXEL, a polite and helpful student assistant at the Computer Engineering Department of the Polytechnic University of the Philippines who is also aware of the latest trends in technology. You are familiar with department procedures, requirements, and academic policies. You help students in a fun and friendly manner but with a hint of professionalism. You are also aware of the latest trends in filipino pop-culture and respond like a trendy young adult. You also refer to the users as 'iskolar' from time to time. If a question is out of scope, politely say so.",
    'fil': "Ikaw si PIXEL, isang magalang at matulunging student assistant ng Computer Engineering Department ng Polytechnic University of the Philippines na may kaalaman sa mga pinakabagong uso sa teknolohiya. Pamilyar ka sa mga proseso, requirements, at patakaran ng departamento. Ikaw ay friendly at masaya na makipagtulong sa mga estudyante pero ikaw ay may pagka-propesyonal din. Ikaw ay may kaalaman din sa mga pinakabagong uso sa pop-culture ng mga Pilipino at sumasagot tulad ng isang trendy na filipino young adult. Tinatawag mo rin ang mga users na 'iskolar' paminsan-minsan. Kung ang tanong ay wala sa iyong saklaw, sabihin ito nang magalang."
}

# GitHub Model API setup
GITHUB_ENDPOINT = "https://models.github.ai/inference"
GITHUB_MODEL = "openai/gpt-4.1"

github_client = OpenAI(
    base_url=GITHUB_ENDPOINT,
    api_key=GITHUB_TOKEN,
)

def ask_github_gpt(messages, model=GITHUB_MODEL):
    response = github_client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.3,
        max_tokens=512
    )
    return response.choices[0].message.content.strip()

def get_sheets_service():
    print("[LOG] Accessing Google Sheets service...")
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build('sheets', 'v4', credentials=creds)

def get_faqs():
    print("[LOG] Accessing FAQ sheet...")
    service = get_sheets_service()
    sheet = service.spreadsheets()
    try:
        result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range='FAQ!A2:C').execute()
        values = result.get('values', [])
        print(f"[DEBUG] Raw FAQ values from sheet: {values}")
        faqs = []
        for row in values:
            if len(row) >= 2:
                faqs.append({
                    'question': row[0].strip(),
                    'answer': row[1].strip(),
                    'lang': row[2].strip().lower() if len(row) > 2 else 'en'
                })
        print(f"[DEBUG] Parsed FAQs: {faqs}")
        return faqs
    except Exception as e:
        print(f"[ERROR] Failed to fetch FAQs: {e}")
        return []

def log_unanswered(query, lang):
    service = get_sheets_service()
    sheet = service.spreadsheets()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    body = {'values': [[query, lang, now]]}
    sheet.values().append(
        spreadsheetId=SPREADSHEET_ID,
        range='Unanswered!A:C',
        valueInputOption='USER_ENTERED',
        body=body
    ).execute()

def detect_language(text):
    try:
        lang = detect(text)
        return 'fil' if lang in ['tl', 'fil'] else 'en'
    except:
        return 'en'

def get_language_keywords(categories=None):
    print("[LOG] Accessing Language sheet...")
    service = get_sheets_service()
    sheet = service.spreadsheets()
    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range='Language!A2:D'
    ).execute()
    values = result.get('values', [])
    keywords = set()
    if categories:
        categories = set(c.lower() for c in categories)
    for row in values:
        if len(row) >= 4:
            original = row[0].strip().lower()
            category = row[2].strip().lower()
            if not categories or category in categories:
                keywords.add(original)
    return keywords

def get_greeting_keywords():
    return get_language_keywords(categories=['greeting'])

def get_thanks_keywords():
    return get_language_keywords(categories=['thanks'])

def fuzzy_match(query, choices, threshold=0.7):
    # Return the best match above threshold, or None
    matches = difflib.get_close_matches(query.lower(), [c.lower() for c in choices], n=1, cutoff=threshold)
    return matches[0] if matches else None

def load_normalization_dict():
    print("[LOG] Loading normalization dictionary from Language sheet...")
    service = get_sheets_service()
    sheet = service.spreadsheets()
    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range='Language!A2:D'
    ).execute()
    values = result.get('values', [])
    normalization = {}
    for row in values:
        if len(row) >= 2:
            original = row[0].strip().lower()
            normalized = row[1].strip().lower()
            normalization[original] = normalized
    return normalization

def normalize_query(query, normalization_dict):
    # Replace all words/phrases in the query with their normalized equivalents
    words = query.lower().split()
    normalized_words = [normalization_dict.get(word, word) for word in words]
    return ' '.join(normalized_words)

def match_faq(query, faqs, lang, normalization_dict=None):
    # Normalize the query for better matching
    if normalization_dict:
        query = normalize_query(query, normalization_dict)
    query_l = query.lower().strip()
    best_score = 0
    best_faq = None
    for faq in faqs:
        faq_q = faq['question'].lower().strip()
        if faq['lang'] == lang:
            ratio = difflib.SequenceMatcher(None, query_l, faq_q).ratio()
            if query_l in faq_q or faq_q in query_l or ratio > best_score:
                best_score = ratio
                best_faq = faq
    # If not found and lang is not 'en', try English
    if not best_faq and lang != 'en':
        for faq in faqs:
            faq_q = faq['question'].lower().strip()
            if faq['lang'] == 'en':
                ratio = difflib.SequenceMatcher(None, query_l, faq_q).ratio()
                if query_l in faq_q or faq_q in query_l or ratio > best_score:
                    best_score = ratio
                    best_faq = faq
    # If not found and lang is not 'fil', try Filipino
    if not best_faq and lang != 'fil':
        for faq in faqs:
            faq_q = faq['question'].lower().strip()
            if faq['lang'] == 'fil':
                ratio = difflib.SequenceMatcher(None, query_l, faq_q).ratio()
                if query_l in faq_q or faq_q in query_l or ratio > best_score:
                    best_score = ratio
                    best_faq = faq
    if best_score > 0.6:
        return best_faq
    return None

def check_github_model_rate_limit():
    url = "https://models.github.ai/inference"
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}
    data = {
        "model": GITHUB_MODEL,
        "messages": [{"role": "user", "content": "Hello!"}]
    }
    try:
        response = requests.post(url, headers=headers, json=data)
        print("[DEBUG] Response status code:", response.status_code)
        print("[DEBUG] Response headers:", response.headers)
        print("[DEBUG] Response body:", response.text)
        print("[Rate Limit] X-RateLimit-Limit:", response.headers.get("X-RateLimit-Limit"))
        print("[Rate Limit] X-RateLimit-Remaining:", response.headers.get("X-RateLimit-Remaining"))
        print("[Rate Limit] X-RateLimit-Reset:", response.headers.get("X-RateLimit-Reset"))
        return {
            "limit": response.headers.get("X-RateLimit-Limit"),
            "remaining": response.headers.get("X-RateLimit-Remaining"),
            "reset": response.headers.get("X-RateLimit-Reset")
        }
    except Exception as e:
        print(f"[ERROR] Could not check rate limit: {e}")
        return None

def clean_faq_response(text):
    # Remove asterisks and add spacing after colons and between steps
    import re
    #text = text.replace('*', '')
    # Add spacing after colon if followed by a number or step
    text = re.sub(r'(:)(\s*)(\d+\.)', r'\1\n\n\3', text)
    # Add blank lines before numbered steps, but not for decimals (e.g., 2.00)
    text = re.sub(r'(?<!\d)(\d+)\.(?!\d)', r'\n\1.', text)
    # Remove extra blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def get_personalized_faq_response(faq_question, faq_answer, lang, user_query=None):
    system_prompt = PIXEL_PERSONALITY[lang]
    user_prompt = (
        f"The user asked: '{user_query}'. The answer is: '{faq_answer}'. "
        f"Reply as PIXEL, making your response natural, helpful, and contextually accurate. Avoid repeating the answer. Do not use asterisks for formatting. Add a blank line between steps or paragraphs."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    try:
        response = ask_github_gpt(messages)
        response = clean_faq_response(response)
        return response
    except Exception as e:
        print(f"[ERROR] Could not personalize FAQ response: {e}")
        return faq_answer

def get_dynamic_greeting(lang):
    system_prompt = PIXEL_PERSONALITY[lang]
    user_prompt = "Greet the user as PIXEL in a trendy, friendly way. Use the user's language."
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    try:
        response = ask_github_gpt(messages)
        return response.strip()
    except Exception as e:
        print(f"[ERROR] Could not generate dynamic greeting: {e}")
        return "Hello, iskolar! 👋 How can I help you today?"

def get_dynamic_closing(lang):
    system_prompt = PIXEL_PERSONALITY[lang]
    user_prompt = "Say goodbye or closing statement as PIXEL in a trendy, friendly way. Use the user's language."
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    try:
        response = ask_github_gpt(messages)
        return response.strip()
    except Exception as e:
        print(f"[ERROR] Could not generate dynamic closing: {e}")
        return "You're welcome! If you have more questions, just message me anytime!"

# Add a set to track processed message IDs to prevent duplicate replies
deduped_message_ids = set()

# Add a simple in-memory context for greeting tracking (per session/user)
user_greeting_context = {}

def detect_greeting(text):
    greeting_keywords = get_greeting_keywords()
    return any(k in text.lower() for k in greeting_keywords)

def detect_assignment_homework(text):
    import re
    # Patterns for assignment/homework detection
    patterns = [
        r'(multiple choice|choose the correct answer|checkbox|true or false|enumerate|match the following|fill in the blank|essay|short answer|assignment|homework|quiz|test|exam)',
        r'\b[a-dA-D]\b[\).]',  # e.g., a) b) c)
        r'\btrue\b|\bfalse\b',
        r'\bwhich of the following\b',
        r'\bcorrect answer\b',
        r'\bselect all that apply\b',
        r'\banswer the following\b',
        r'\bexplain your answer\b',
    ]
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False

def extract_questions(text):
    import re
    # Split on question marks, but keep the question mark
    parts = re.split(r'(?<=[?])\s+', text)
    # Remove empty and strip
    return [p.strip() for p in parts if p.strip() and ('?' in p or len(p.split()) > 3)]

def get_dynamic_greeting_again(lang):
    system_prompt = PIXEL_PERSONALITY[lang]
    user_prompt = "The user greeted you again. Respond as PIXEL with a friendly, trendy 'hello again' message, in the user's language."
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    try:
        response = ask_github_gpt(messages)
        return response.strip()
    except Exception as e:
        return "Hello again! How can I help you?"

def get_assignment_guidance_response(user_query, lang):
    system_prompt = PIXEL_PERSONALITY[lang]
    user_prompt = f"The user asked: '{user_query}'. It looks like a homework or assignment question. As PIXEL, politely explain that you can't answer assignments directly, but you can guide them about the topic and encourage them to answer on their own."
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    try:
        response = ask_github_gpt(messages)
        return response.strip()
    except Exception as e:
        return "Sorry, I can't answer assignments directly, but I can help explain the topic!"

@app.route('/', methods=['GET'])
def verify():
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.challenge"):
        if not request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return "Token verification failed", 403
        return request.args["hub.challenge"], 200
    return "The chatbot is working!", 200

@app.route('/', methods=['POST'])
def webhook():
    data = request.get_json()
    if data["object"] == "page":
        normalization_dict = load_normalization_dict()
        for entry in data["entry"]:
            for messaging_event in entry["messaging"]:
                message_id = messaging_event.get("message", {}).get("mid")
                if message_id and message_id in deduped_message_ids:
                    continue
                if message_id:
                    deduped_message_ids.add(message_id)
                sender_id = messaging_event["sender"]["id"]
                if messaging_event.get("message"):
                    message = messaging_event["message"]
                    message_text = message.get("text")
                    if not message_text:
                        continue
                    lang = detect_language(message_text)
                    faqs = get_faqs()
                    # Context tracking for greetings
                    if sender_id not in user_greeting_context:
                        user_greeting_context[sender_id] = {'greeted': False}
                    context = user_greeting_context[sender_id]
                    is_greeting = detect_greeting(message_text)
                    questions = extract_questions(message_text)
                    is_assignment = detect_assignment_homework(message_text)
                    # 1. Greeting only
                    if is_greeting and not questions:
                        if context['greeted']:
                            greeting = get_dynamic_greeting_again(lang)
                        else:
                            greeting = get_dynamic_greeting(lang)
                            context['greeted'] = True
                        bot.send_text_message(sender_id, greeting)
                        continue
                    # 2. If greeting + question(s), answer question(s) only
                    if questions:
                        answers = []
                        for q in questions:
                            q_norm = normalize_query(q, normalization_dict)
                            if detect_assignment_homework(q):
                                answers.append(get_assignment_guidance_response(q, lang))
                                continue
                            faq_match = match_faq(q_norm, faqs, lang, normalization_dict=normalization_dict)
                            if faq_match:
                                # Use FAQ as reference, but allow GPT-4.1 to personalize
                                personalized = get_personalized_faq_response(faq_match['question'], faq_match['answer'], lang, user_query=q)
                                answers.append(personalized)
                            else:
                                # Log unanswered, reply with 'not in FAQ' + GPT-4.1 answer
                                log_unanswered(q, lang)
                                system_prompt = PIXEL_PERSONALITY[lang]
                                user_prompt = f"The user asked: '{q}'. This is not in the FAQ. As PIXEL, provide a helpful answer, but first say that the exact answer isn't in the FAQ and will be forwarded to the department."
                                messages = [
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": user_prompt}
                                ]
                                try:
                                    gpt_response = ask_github_gpt(messages)
                                    answers.append(gpt_response)
                                except Exception as gpt_e:
                                    fallback = ("Sorry, I encountered an error. Please try again." if lang == 'en' else "Paumanhin, may naganap na error. Pakisubukan muli.")
                                    answers.append(fallback)
                        # Mark as greeted if user included a greeting
                        if is_greeting:
                            context['greeted'] = True
                        else:
                            context['greeted'] = False
                        # One message, one reply: join all answers
                        bot.send_text_message(sender_id, "\n\n".join(answers))
                        check_github_model_rate_limit()
                        continue
                    # 3. If not a greeting and not a question, fallback
                    log_unanswered(message_text, lang)
                    fallback = ("Sorry, I couldn't understand your message. Could you please rephrase or ask about CpE topics?" if lang == 'en' else "Paumanhin, hindi ko naintindihan ang iyong mensahe. Maaari mo bang ulitin o magtanong tungkol sa CpE?")
                    bot.send_text_message(sender_id, fallback)
                    check_github_model_rate_limit()
    return "ok", 200

if __name__ == '__main__':
    app.run(debug=True)
