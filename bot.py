import discord
import logging
import os
import re
import json
import easyocr
import gspread
import requests
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv
from PIL import Image
from io import BytesIO

# Load environment variables
load_dotenv()

# ==============================
# 🔧 CONFIG
# ==============================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

# Validate credentials
if not DISCORD_TOKEN or not SPREADSHEET_ID:
    raise ValueError("Missing DISCORD_TOKEN or SPREADSHEET_ID in environment variables")

# ==============================
# 📜 LOGGING SETUP
# ==============================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# ==============================
# 🤖 DISCORD SETUP
# ==============================
intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

# ==============================
# 🔍 OCR SETUP (FREE - EasyOCR)
# ==============================
# Initialize OCR reader with English language support
# GPU support available if CUDA is installed
ocr_reader = easyocr.Reader(['en'], gpu=False)

# ==============================
# 📊 GOOGLE SHEETS SETUP
# ==============================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds = ServiceAccountCredentials.from_json_keyfile_name("creds.json", scope)
client_gs = gspread.authorize(creds)

sheet = client_gs.open_by_key(SPREADSHEET_ID).sheet1

# ==============================
# 🧠 PROCESS IMAGE WITH OCR
# ==============================
async def process_image(image_url):
    try:
        logger.info(f"Processing image with OCR: {image_url}")

        # Download image from URL with proper headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(image_url, headers=headers, timeout=30)
        response.raise_for_status()  # Raise exception for bad status codes
        
        image_data = Image.open(BytesIO(response.content))

        # Convert to RGB if necessary
        if image_data.mode != 'RGB':
            image_data = image_data.convert('RGB')

        # Perform OCR
        ocr_result = ocr_reader.readtext(image_data, detail=0)
        extracted_text = "\n".join(ocr_result)
        
        logger.info(f"Extracted text:\n{extracted_text}")

        # Extract structured data from OCR text
        data = extract_transfer_data(extracted_text)
        
        if data:
            logger.info(f"Extracted data: {data}")
            return data
        else:
            logger.warning("Could not extract structured data from OCR text")
            return None

    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP Error downloading image: {e.response.status_code} - {e}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading image: {e}")
        return None
    except Exception as e:
        logger.error(f"Error processing image: {e}")
        return None

# ==============================
# 🔍 EXTRACT TRANSFER DATA FROM TEXT
# ==============================
def extract_transfer_data(text):
    """
    Extract amount, bank, name, date, and timestamp from OCR text.
    Optimized for BCA and BRI transfer receipts.
    """
    try:
        data = {}

        # Detect bank type
        bank_type = None
        if 'BCA' in text.upper():
            bank_type = 'BCA'
            data['bank'] = 'BCA'
        elif 'BRI' in text.upper():
            bank_type = 'BRI'
            data['bank'] = 'BRI'
        else:
            # Try generic bank detection
            bank_patterns = [
                r'\b(BCA|BRI|MANDIRI|CIMB|PERMATA|OVO|GOPAY|DANA|JAGO)\b',
            ]
            for pattern in bank_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    data['bank'] = match.group(1).strip().upper()
                    break

        # ============ BCA FORMAT ============
        if bank_type == 'BCA':
            # Extract amount - look for "IDR" followed by numbers with commas
            amount_patterns = [
                r'(?:Nominal\s+)?(?:IDR|Rp)\s+([\d,.]+)',
                r'IDR\s+([\d,.]+)',
            ]
            for pattern in amount_patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                if matches:
                    amounts = [m.replace(',', '') for m in matches]
                    data['amount'] = max(amounts, key=lambda x: float(x.split('.')[0]))
                    break

            # Extract recipient name for BCA
            name_patterns = [
                r'Nama\s+Penerima\s+([A-Z][A-Z0-9\s]+?)(?:\n|Rekening|$)',
            ]
            for pattern in name_patterns:
                match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
                if match:
                    name = match.group(1).strip()
                    name = ' '.join(name.split())
                    data['name'] = name
                    break

            # Extract date for BCA (DD Mon YYYY)
            date_patterns = [
                r'(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})',
            ]
            for pattern in date_patterns:
                match = re.search(pattern, text)
                if match:
                    data['date'] = match.group(1).strip()
                    break

            # Extract timestamp for BCA (HH.MM.SS)
            timestamp_patterns = [
                r'(\d{1,2})[:.]\d{1,2}[:.]\d{2}(?=\d)',
                r'(\d{1,2})[:.]\d{1,2}[:.]\d{2}\b',
            ]
            for pattern in timestamp_patterns:
                match = re.search(pattern, text)
                if match:
                    full_match = re.search(r'(\d{1,2})[:.]{1}(\d{2})[:.]{1}(\d{2})', text)
                    if full_match:
                        data['timestamp'] = f"{full_match.group(1)}.{full_match.group(2)}.{full_match.group(3)}"
                    break

        # ============ BRI FORMAT ============
        elif bank_type == 'BRI':
            # Extract amount - look for "Nominal Rp" followed by numbers
            amount_patterns = [
                r'Nominal\s+Rp([\d.s]+)',
                r'Rp([\d.s]+)',
            ]
            for pattern in amount_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    amount_str = match.group(1).strip()
                    # Clean up OCR errors (s -> 5, o -> 0)
                    amount_str = amount_str.replace('s', '5').replace('S', '5')
                    amount_str = amount_str.replace('o', '0').replace('O', '0')
                    # Remove dots that are thousands separators, keep last dot for decimals
                    parts = amount_str.split('.')
                    if len(parts) > 1 and len(parts[-1]) == 3:
                        # Last part has 3 digits, likely decimal
                        amount_str = ''.join(parts[:-1]) + '.' + parts[-1]
                    else:
                        amount_str = ''.join(parts)
                    data['amount'] = amount_str
                    break

            # Extract recipient name for BRI (from "Tujuan" section)
            name_patterns = [
                r'Tujuan\s+([A-Z][A-Z0-9\s]+?)(?:\n|BANK|$)',
                r'(?:recipient|penerima|tujuan)[:\s]+([^\n,]+)',
            ]
            for pattern in name_patterns:
                match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
                if match:
                    name = match.group(1).strip()
                    name = ' '.join(name.split())
                    data['name'] = name
                    break

            # Extract date for BRI (DD Bulan YYYY format)
            date_patterns = [
                r'(\d{1,2}\s+[A-Za-z]+\s+\d{4})',
            ]
            for pattern in date_patterns:
                match = re.search(pattern, text)
                if match:
                    data['date'] = match.group(1).strip()
                    break

            # Extract timestamp for BRI (HH.MM.SS from WIB line)
            timestamp_patterns = [
                r'(\d{1,2})[.,](\d{2})[.,](\d{2})\s+WIB',
            ]
            for pattern in timestamp_patterns:
                match = re.search(pattern, text)
                if match:
                    data['timestamp'] = f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
                    break

        logger.info(f"Bank type detected: {bank_type}")
        logger.info(f"Extraction details - Raw data: {data}")
        return data if data else None

    except Exception as e:
        logger.error(f"Error extracting transfer data: {e}")
        return None

# ==============================
# 📊 SAVE TO GOOGLE SHEETS
# ==============================
def save_to_sheets(data):
    try:
        logger.info(f"Saving to sheet: {data}")

        # Convert dict to list format for Google Sheets
        # Format: [Timestamp, Amount, Bank, Name, Date]
        if isinstance(data, dict):
            row = [
                data.get('timestamp', ''),
                data.get('amount', ''),
                data.get('bank', ''),
                data.get('name', ''),
                data.get('date', '')
            ]
        else:
            row = [data]

        sheet.append_row(row)
        logger.info("Data saved successfully")

    except Exception as e:
        logger.error(f"Error saving to sheets: {e}")

# ==============================
# 📩 EVENT: MESSAGE
# ==============================
@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.attachments:
        for attachment in message.attachments:
            if any(attachment.filename.lower().endswith(ext) for ext in ["png", "jpg", "jpeg"]):
                
                logger.info(f"Image detected from {message.author}: {attachment.url}")

                await message.channel.send("📸 Processing your screenshot with OCR...")

                data = await process_image(attachment.url)

                if data:
                    save_to_sheets(data)
                    
                    # Format response message
                    response_msg = "✅ Data extracted and saved:\n"
                    response_msg += f"⏰ Timestamp: {data.get('timestamp', 'N/A')}\n"
                    response_msg += f"💰 Amount: {data.get('amount', 'N/A')}\n"
                    response_msg += f"🏦 Bank: {data.get('bank', 'N/A')}\n"
                    response_msg += f"👤 Name: {data.get('name', 'N/A')}\n"
                    response_msg += f"📅 Date: {data.get('date', 'N/A')}"
                    
                    await message.channel.send(response_msg)
                else:
                    await message.channel.send("❌ Failed to extract data from image. Try a clearer image.")

# ==============================
# 🚀 BOT READY
# ==============================
@client.event
async def on_ready():
    logger.info(f"Logged in as {client.user}")
    print(f"Bot is ready: {client.user}")

# ==============================
# ▶️ RUN BOT
# ==============================
if __name__ == "__main__":
    logger.info("Starting bot...")
    client.run(DISCORD_TOKEN)