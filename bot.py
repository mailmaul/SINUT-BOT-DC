import os
import re
import asyncio
import logging
from io import BytesIO

import discord
import easyocr
import gspread
import requests
import numpy as np
from PIL import Image
from dotenv import load_dotenv
from oauth2client.service_account import ServiceAccountCredentials

# ==============================
# 🔧 CONFIG
# ==============================
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

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
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ==============================
# 🤖 DISCORD SETUP
# ==============================
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ==============================
# 🔍 OCR SETUP (EasyOCR, Indonesian + English, local & free)
# ==============================
ocr_reader = easyocr.Reader(["en", "id"], gpu=False)

# ==============================
# 📊 GOOGLE SHEETS SETUP
# ==============================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
creds = ServiceAccountCredentials.from_json_keyfile_name("creds.json", scope)
client_gs = gspread.authorize(creds)
sheet = client_gs.open_by_key(SPREADSHEET_ID).sheet1

# ==============================
# 🧾 EXTRACTION HELPERS
# ==============================
MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05",
    "mei": "05", "jun": "06", "jul": "07", "aug": "08", "agu": "08",
    "sep": "09", "oct": "10", "okt": "10", "nov": "11", "dec": "12",
    "des": "12",
}

# Bank/e-wallet signatures -> canonical display name (checked in order).
BANK_SIGNATURES = [
    ("wondr", "BNI"), ("bni", "BNI"),
    ("bank bri", "BRI"), ("bri", "BRI"),
    ("bca", "BCA"),
    ("livin", "Bank Mandiri"), ("mandiri", "Bank Mandiri"),
    ("betangmobile", "Bank Kalteng"), ("kalteng", "Bank Kalteng"),
    ("seabank", "SeaBank"),
    ("sinarmas", "Bank Sinarmas"),
    ("flip", "Flip"),
    ("dana", "DANA"),
]

# Total-amount labels, most specific first. Bare "total"/"nominal" last.
TOTAL_LABELS = [
    "total transaksi", "total bayar", "jumlah total", "total kirim",
    "jumlah transfer", "total", "nominal",
]

# Recipient anchors, most reliable first. Each returns the next name-like line(s).
RECIPIENT_ANCHORS = [
    lambda l: "nama penerima" in l,
    lambda l: "rekening tujuan" in l,
    lambda l: l == "tujuan",
    lambda l: "detail penerima" in l,
    lambda l: l == "penerima",
    lambda l: l == "ke",
]

# Words that mark a line as a label/metadata rather than a person's name.
_NAME_STOPWORDS = (
    "nama", "akun", "bank", "rekening", "penerima", "tujuan", "sumber",
    "dana", "detail", "metode", "saldo", "nominal", "total", "biaya",
    "transaksi", "transfer", "ref", "wib", "wita", "wit", "catatan",
    "keterangan", "jumlah", "pengirim", "dari", "berita", "no ", "sesama",
    "bni", "bri", "bca", "idr",
)


def _parse_money(frag):
    """Parse a money fragment to int, handling both '1.234.567' (ID) and
    '1,234,567.00' (BCA/IDR) grouping, OCR 'o'->'0', and decimal cents."""
    frag = frag.replace("o", "0").replace("O", "0")
    frag = re.sub(r"[^\d.,]", "", frag)
    if not frag:
        return None
    frag = re.sub(r"[.,]\d{2}$", "", frag)  # drop trailing decimal cents
    digits = re.sub(r"\D", "", frag)
    return int(digits) if digits else None


def _amount_in_line(line):
    """Parse a monetary value from a line (Rp or IDR), tolerating OCR errors."""
    n = line.lower().replace("o", "0")
    m = re.search(r"(?:rp|idr)\.?\s*([\d][\d.,\s]*\d|\d)", n)
    if m:
        return _parse_money(m.group(1))
    m = re.search(r"\b\d{1,3}(?:[.,\s]\d{3})+(?:[.,]\d{2})?\b", n)  # grouped, no prefix
    if m:
        return _parse_money(m.group(0))
    return None


def _fmt_amount(n):
    return "Rp{:,}".format(n).replace(",", ".") if n is not None else None


def _is_name(line):
    l = line.strip()
    if len(l) < 3 or re.search(r"\d", l):
        return False
    low = l.lower()
    if any(w in low for w in _NAME_STOPWORDS):
        return False
    return bool(re.fullmatch(r"[A-Za-z .'-]+", l))


def _extract_bank(lines, low_text):
    # Explicit recipient bank (e.g. FLIP -> "Bank Sinarmas").
    for i, l in enumerate(lines):
        if l.strip().lower() == "bank penerima":
            for j in range(i + 1, min(i + 5, len(lines))):
                cand = lines[j].strip()
                if len(cand) >= 3 and re.search(r"[A-Za-z]{3,}", cand):
                    return cand
    for key, name in BANK_SIGNATURES:
        if key in low_text:
            return name
    return None


def _extract_total(lines):
    for label in TOTAL_LABELS:
        for i, l in enumerate(lines):
            if label in l.lower():
                for j in range(i, min(i + 3, len(lines))):  # label line + next 2
                    amt = _amount_in_line(lines[j])
                    if amt:
                        return amt
    # Fallback: largest Rp/IDR amount anywhere (handles FLIP with no total label).
    amounts = [
        a for a in (_amount_in_line(l) for l in lines
                    if "rp" in l.lower() or "idr" in l.lower()) if a
    ]
    return max(amounts) if amounts else None


def _extract_recipient(lines):
    for anchor in RECIPIENT_ANCHORS:
        for i, l in enumerate(lines):
            if anchor(l.strip().lower()):
                parts = []
                for j in range(i + 1, min(i + 6, len(lines))):
                    if _is_name(lines[j]):
                        parts.append(lines[j].strip())
                    elif parts:
                        break  # name block ended
                    # else: leading non-name noise ('Nama', '1', 'o') -> skip
                if parts:
                    return " ".join(parts[:3])
    # Inline "... ke <Name>" (DANA: "Kirim Uang Rp200.000 ke Hartutik").
    for l in lines:
        m = re.search(r"\bke\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)", l)
        if m and _is_name(m.group(1)):
            return m.group(1).strip()
    return None


def _extract_date(text, lines):
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)  # ISO: KALTENG
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b", text)  # dd Mon yyyy
    if m:
        mon = MONTHS.get(m.group(2)[:3].lower())
        if mon:
            return f"{m.group(3)}-{mon}-{int(m.group(1)):02d}"
        return m.group(0)
    # Fallback: day / month / year scattered by OCR (e.g. BCA header).
    for i, l in enumerate(lines):
        ym = re.search(r"\b(20\d{2})\b", l)
        if not ym:
            continue
        mt = re.search(r"\b(" + "|".join(MONTHS) + r")", text, re.I)
        day = None
        if i > 0 and re.fullmatch(r"\d{1,2}", lines[i - 1].strip()):
            day = int(lines[i - 1].strip())
        if mt and day:
            return f"{ym.group(1)}-{MONTHS[mt.group(1)[:3].lower()]}-{day:02d}"
        break
    return None


def _extract_time(text):
    # Prefer a time explicitly tagged with an Indonesian timezone.
    m = re.search(r"\b(\d{1,2})[.:](\d{2})(?:[.:]?(\d{2}))?\s*(WIB|WITA|WIT)\b", text, re.I)
    if not m:
        m = re.search(r"\b(\d{1,2})[.:](\d{2})(?:[.:](\d{2}))?", text)
    if not m:
        return None
    parts = [f"{int(m.group(1)):02d}", m.group(2)]
    if m.group(3):
        parts.append(m.group(3))
    out = ":".join(parts)
    if m.lastindex and m.group(m.lastindex) and m.group(m.lastindex).upper() in ("WIB", "WITA", "WIT"):
        out += " " + m.group(m.lastindex).upper()
    return out


def extract_transfer_data(lines):
    """
    Extract the 5 required fields from OCR lines of a transfer receipt:
    Waktu Kirim, Total Kirim, Nama BANK, Nama Penerima, Tanggal.
    Works across BNI, BRI, DANA, FLIP, KALTENG, MANDIRI, SEABANK and
    degrades gracefully on unseen formats. Returns dict or None.
    """
    try:
        lines = [l.strip() for l in lines if l and l.strip()]
        text = "\n".join(lines)
        low_text = text.lower()

        total = _extract_total(lines)
        data = {
            "tanggal": _extract_date(text, lines),
            "waktu_kirim": _extract_time(text),
            "nama_bank": _extract_bank(lines, low_text),
            "nama_penerima": _extract_recipient(lines),
            "total_kirim": _fmt_amount(total),
        }
        logger.info("Extracted fields: %s", data)

        # Require at least one substantive field to consider it a receipt.
        if not (data["total_kirim"] or data["nama_penerima"] or data["nama_bank"]):
            return None
        return data
    except Exception as e:
        logger.error(f"Error extracting transfer data: {e}")
        return None


# ==============================
# 🧠 OCR PIPELINE
# ==============================
_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
_OCR_MAX_SIDE = 1600  # downscale large screenshots to bound OCR latency


def _run_ocr(content):
    """Blocking OCR of raw image bytes -> list of text lines (runs in a thread)."""
    img = Image.open(BytesIO(content))
    if img.mode != "RGB":
        img = img.convert("RGB")
    if max(img.size) > _OCR_MAX_SIDE:
        img.thumbnail((_OCR_MAX_SIDE, _OCR_MAX_SIDE))
    return ocr_reader.readtext(np.array(img), detail=0, paragraph=False)


async def process_image(image_url):
    try:
        logger.info(f"Processing image with OCR: {image_url}")
        resp = requests.get(image_url, headers=_HTTP_HEADERS, timeout=30)
        resp.raise_for_status()

        # Keep the Discord event loop responsive: OCR off-thread.
        lines = await asyncio.to_thread(_run_ocr, resp.content)
        logger.info("OCR lines: %s", lines)

        return extract_transfer_data(lines)
    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading image: {e}")
        return None
    except Exception as e:
        logger.error(f"Error processing image: {e}")
        return None


# ==============================
# 📊 SAVE TO GOOGLE SHEETS
# ==============================
def save_to_sheets(data):
    try:
        logger.info(f"Saving to sheet: {data}")
        row = [
            data.get("waktu_kirim", "") or "",
            data.get("total_kirim", "") or "",
            data.get("nama_bank", "") or "",
            data.get("nama_penerima", "") or "",
            data.get("tanggal", "") or "",
        ]
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

    if not message.attachments:
        return

    for attachment in message.attachments:
        if not attachment.filename.lower().endswith((".png", ".jpg", ".jpeg")):
            continue

        logger.info(f"Image detected from {message.author}: {attachment.url}")
        await message.channel.send("📸 Memproses bukti transfer dengan OCR...")

        data = await process_image(attachment.url)
        if data:
            save_to_sheets(data)
            na = "N/A"
            msg = (
                "✅ Data berhasil diambil & disimpan:\n"
                f"⏰ Waktu Kirim: {data.get('waktu_kirim') or na}\n"
                f"💰 Total Kirim: {data.get('total_kirim') or na}\n"
                f"🏦 Nama Bank: {data.get('nama_bank') or na}\n"
                f"👤 Nama Penerima: {data.get('nama_penerima') or na}\n"
                f"📅 Tanggal: {data.get('tanggal') or na}"
            )
            await message.channel.send(msg)
        else:
            await message.channel.send(
                "❌ Gagal membaca data dari gambar. Coba unggah gambar yang lebih jelas."
            )


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
