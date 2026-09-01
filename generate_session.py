"""
Run this ONCE on your own computer (not on Railway) to log in with your
API_ID / API_HASH / phone number and generate a SESSION_STRING.

Steps:
    1. pip install -r requirements.txt
    2. python generate_session.py
    3. Enter your API_ID, API_HASH and phone number when asked.
    4. Enter the login code Telegram sends you (and your 2FA password if you
       have one set).
    5. Copy the printed SESSION_STRING into your .env file (locally) and into
       Railway's Environment Variables (for deployment).

Keep this string secret. Anyone with it can log in as you without needing
the OTP code again.
"""

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

print("=== Telegram Userbot — One-Time Login ===\n")

api_id = input("API_ID: ").strip()
api_hash = input("API_HASH: ").strip()

with TelegramClient(StringSession(), int(api_id), api_hash) as client:
    session_string = client.session.save()
    print("\nLogin successful!\n")
    print("Copy the line below into your .env file / Railway variables:\n")
    print(f"SESSION_STRING={session_string}\n")
    print("Also save API_ID and API_HASH the same way you entered them above.")
