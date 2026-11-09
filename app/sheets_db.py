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
    """Returns all rows of a worksheet/tab as a list of dicts."""
    try:
        wb = get_sheets_client()
        worksheet = get_or_create_worksheet(wb, table_name)
        return worksheet.get_all_records()
    except Exception:
        return []

def find_record_by(table_name: str, key: str, value):
    """Robust raw-value spreadsheet lookup immune to dictionary mapping failures."""
    try:
        wb = get_sheets_client()
        worksheet = get_or_create_worksheet(wb, table_name)
        
        all_values = worksheet.get_all_values()
        if not all_values or len(all_values) < 2:
            return None

        # Standardize header names
        headers = [str(h).strip().lower() for h in all_values[0]]
        key_lower = str(key).strip().lower()

        if key_lower not in headers:
            return None

        key_index = headers.index(key_lower)
        target_val = str(value).strip().lower()

        # Search rows starting from row 2
        for row in all_values[1:]:
            if len(row) > key_index:
                cell_val = str(row[key_index]).strip().lower()
                if cell_val == target_val:
                    # Map row values back to headers
                    return {headers[i]: (row[i].strip() if i < len(row) else "") for i in range(len(headers))}
        return None
    except Exception as e:
        print(f"Error in find_record_by: {e}")
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
    
    row_values = [str(data.get(h, "")) for h in headers]
    worksheet.append_row(row_values)

def update_record_by(table_name: str, key: str, value, updates: dict):
    wb = get_sheets_client()
    worksheet = get_or_create_worksheet(wb, table_name)
    cell = worksheet.find(str(value))
    if cell:
        row_num = cell.row
        headers = worksheet.row_values(1)
        for col_name, new_val in updates.items():
            if col_name in headers:
                col_num = headers.index(col_name) + 1
                worksheet.update_cell(row_num, col_num, str(new_val))
