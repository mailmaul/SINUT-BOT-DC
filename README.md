# Discord Transfer OCR Bot

A Discord bot that extracts bank transfer data from screenshots using **free, local OCR** (no API costs).

## Features

✅ **Zero Cost OCR** - Uses EasyOCR (completely free, open-source)  
✅ **Local Processing** - No external API calls (instant, private)  
✅ **Automatic Extraction** - Extracts: amount, bank, name, date  
✅ **Google Sheets Integration** - Saves data directly to your spreadsheet  
✅ **Security** - Credentials stored in `.env` file (not hardcoded)

## Cost Comparison

| Method | Cost per Image | Speed | Privacy |
|--------|---|---|---|
| **This Bot (OCR)** | **$0** | Instant (local) | ✅ Private |
| OpenAI Vision API | $0.0025 | Seconds | ❌ Cloud |
| Google Cloud Vision | $0.0015 | Seconds | ❌ Cloud |

## Setup

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Credentials

#### Discord Token
1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application → Bot
3. Copy the token

#### Google Sheets Setup
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a service account and download JSON credentials
3. Share your Google Sheet with the service account email
4. Copy the sheet ID from the URL: `https://docs.google.com/spreadsheets/d/{SHEET_ID}`

#### Environment Variables
```bash
# Copy the example file
cp .env.example .env

# Edit .env with your credentials
DISCORD_TOKEN=your_discord_bot_token
SPREADSHEET_ID=your_google_sheet_id
```

### 3. Add Google Service Account Credentials
Save the JSON file from Google Cloud as `creds.json` in the project root.

### 4. Run the Bot
```bash
python bot.py
```

## Usage

1. Upload a bank transfer screenshot to a Discord channel where the bot has access
2. Bot automatically extracts:
   - 💰 Amount
   - 🏦 Bank name
   - 👤 Recipient name
   - 📅 Date
3. Data is saved to your Google Sheet

## Customization

### Adjust OCR Patterns

Edit the `extract_transfer_data()` function in `bot.py` to match your specific transfer format. The regex patterns can be customized for:
- Different currency formats
- Different bank names
- Different date formats
- Different transfer templates

### Enable GPU Acceleration

For faster OCR with large volumes:
```python
ocr_reader = easyocr.Reader(['en'], gpu=True)
```
(Requires CUDA-compatible GPU)

### Support Multiple Languages

Add language codes to the reader:
```python
ocr_reader = easyocr.Reader(['en', 'id', 'zh'], gpu=False)  # English, Indonesian, Chinese
```

## Troubleshooting

**"ModuleNotFoundError: No module named 'easyocr'"**
```bash
pip install easyocr
```

**Poor OCR accuracy**
- Upload clearer, higher-resolution screenshots
- Adjust lighting and contrast
- Ensure text is straight (not rotated)

**Google Sheets authentication error**
- Verify `creds.json` is in the project root
- Check service account has access to the spreadsheet
- Ensure email in JSON is added as editor to the sheet

## Architecture

```
Discord Upload
    ↓
Download Image
    ↓
EasyOCR (Local Processing) ← FREE ✅
    ↓
Extract Transfer Data (Regex Pattern Matching)
    ↓
Save to Google Sheets
    ↓
Confirm to User
```

## Requirements

- Python 3.8+
- Discord.py
- EasyOCR
- Google Sheets API credentials
- Pillow (image processing)

See `requirements.txt` for exact versions.

