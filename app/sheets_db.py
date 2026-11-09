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
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON environment variable is missing.")
    
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    
    sheet_name = os.environ.get("GOOGLE_SHEET_NAME", "bethel_boutique.db")
    return client.open(sheet_name)

def get_or_create_worksheet(wb, table_name: str, rows=100, cols=20):
    try:
        return wb.worksheet(table_name)
    except gspread.exceptions.WorksheetNotFound:
        return wb.add_worksheet(title=table_name, rows=rows, cols=cols)

def get_table_records(table_name: str):
    try:
        wb = get_sheets_client()
        worksheet = get_or_create_worksheet(wb, table_name)
        return worksheet.get_all_records()
    except Exception:
        return []

def find_record_by(table_name: str, key: str, value):
    records = get_table_records(table_name)
    for row in records:
        if str(row.get(key)) == str(value):
            return row
    return None

def insert_record(table_name: str, data: dict):
    wb = get_sheets_client()
    worksheet = get_or_create_worksheet(wb, table_name)
    try:
        headers = worksheet.row_values(1)
    except Exception:
        headers = []
        
    if not headers:
        headers = list(data.keys())
        worksheet.append_row(headers)
    
    row_values = [data.get(h, "") for h in headers]
    worksheet.append_row(row_values)
