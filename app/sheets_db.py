import os
import json
import gspread
from google.oauth2.service_account import Credentials

def get_sheets_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    
    # Load credentials from environment variable
    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not creds_json:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON environment variable is missing.")
    
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    
    sheet_name = os.environ.get("GOOGLE_SHEET_NAME", "AppDatabase")
    return client.open(sheet_name)

def get_table_records(table_name: str):
    """Returns all rows of a worksheet/tab as a list of dicts."""
    try:
        wb = get_sheets_client()
        worksheet = wb.worksheet(table_name)
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
    worksheet = wb.worksheet(table_name)
    # If table is empty, write headers first
    headers = worksheet.row_values(1)
    if not headers:
        headers = list(data.keys())
        worksheet.append_row(headers)
    
    # Append row matching header order
    row_values = [data.get(h, "") for h in headers]
    worksheet.append_row(row_values)

def update_record_by(table_name: str, key: str, value, updates: dict):
    wb = get_sheets_client()
    worksheet = wb.worksheet(table_name)
    cell = worksheet.find(str(value))
    if cell:
        row_num = cell.row
        headers = worksheet.row_values(1)
        for col_name, new_val in updates.items():
            if col_name in headers:
                col_num = headers.index(col_name) + 1
                worksheet.update_cell(row_num, col_num, new_val)
