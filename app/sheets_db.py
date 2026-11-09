import os
import json
import gspread
from google.oauth2.service_account import Credentials

def get_sheets_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    
    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not creds_json:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON environment variable is missing or empty.")
    
    try:
        creds_dict = json.loads(creds_json.strip())
    except json.JSONDecodeError as e:
        raise ValueError(f"GOOGLE_SERVICE_ACCOUNT_JSON contains invalid JSON format: {e}")
    
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    
    sheet_name = os.environ.get("GOOGLE_SHEET_NAME", "bethel_boutique.db")
    return client.open(sheet_name)
