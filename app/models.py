import json
import os
import time
from typing import Optional

import gspread
from flask_login import UserMixin
from google.oauth2.service_account import Credentials
from werkzeug.security import check_password_hash, generate_password_hash

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Simple per-instance cache to reduce cold-start / rate-limit pressure on Vercel
_CACHE_TTL_SECONDS = 30
_users_cache: dict[str, object] = {"expires_at": 0.0, "rows": []}


def _get_gspread_client() -> gspread.Client:
    raw_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON environment variable is not set.")

    creds_dict = json.loads(raw_json)
    if "private_key" in creds_dict and isinstance(creds_dict["private_key"], str):
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")

    credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(credentials)


def _get_users_worksheet() -> gspread.Worksheet:
    client = _get_gspread_client()
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "").strip()
    sheet_name = os.environ.get("GOOGLE_SHEET_NAME", "").strip()
    worksheet_name = os.environ.get("GOOGLE_USERS_WORKSHEET", "Users").strip()

    if sheet_id:
        spreadsheet = client.open_by_key(sheet_id)
    elif sheet_name:
        spreadsheet = client.open(sheet_name)
    else:
        raise RuntimeError("Set either GOOGLE_SHEET_ID or GOOGLE_SHEET_NAME in environment variables.")

    return spreadsheet.worksheet(worksheet_name)


def fetch_all_user_rows(force_refresh: bool = False) -> list[dict]:
    now = time.monotonic()
    if not force_refresh and now < float(_users_cache["expires_at"]):
        return list(_users_cache["rows"])  # type: ignore[arg-type]

    worksheet = _get_users_worksheet()
    records = worksheet.get_all_records()
    normalized_rows = [
        {str(k).strip().lower(): v for k, v in row.items()}
        for row in records
        if any(str(v).strip() for v in row.values())
    ]

    _users_cache["rows"] = normalized_rows
    _users_cache["expires_at"] = now + _CACHE_TTL_SECONDS
    return normalized_rows


def invalidate_users_cache() -> None:
    _users_cache["expires_at"] = 0.0
    _users_cache["rows"] = []


class SheetUser(UserMixin):
    """Flask-Login user model backed by a Google Sheets row."""

    def __init__(self, row: dict):
        normalized = {str(k).strip().lower(): v for k, v in (row or {}).items()}
        self.id = str(normalized.get("id", "")).strip()
        self.email = str(normalized.get("email", "")).strip().lower()
        self.name = str(normalized.get("name", "")).strip()
        self.role = str(normalized.get("role", "staff")).strip().lower() or "staff"
        self.password_hash = str(normalized.get("password_hash", "")).strip()

        raw_active = normalized.get("is_active", True)
        if isinstance(raw_active, bool):
            self._is_active = raw_active
        else:
            self._is_active = str(raw_active).strip().lower() not in {"false", "0", "no", "inactive"}

    def get_id(self) -> str:
        return self.id

    @property
    def is_active(self) -> bool:
        return self._is_active

    def check_password(self, plain_password: str) -> bool:
        if not self.password_hash or not plain_password:
            return False
        return check_password_hash(self.password_hash, plain_password)

    @staticmethod
    def hash_password(plain_password: str) -> str:
        return generate_password_hash(plain_password)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "role": self.role,
            "is_active": self.is_active,
        }

    @classmethod
    def get_by_id(cls, user_id: str, force_refresh: bool = False) -> Optional["SheetUser"]:
        target_id = str(user_id).strip()
        if not target_id:
            return None

        for row in fetch_all_user_rows(force_refresh=force_refresh):
            if str(row.get("id", "")).strip() == target_id:
                return cls(row)

        if not force_refresh:
            for row in fetch_all_user_rows(force_refresh=True):
                if str(row.get("id", "")).strip() == target_id:
                    return cls(row)

        return None

    @classmethod
    def get_by_email(cls, email: str, force_refresh: bool = False) -> Optional["SheetUser"]:
        target_email = str(email).strip().lower()
        if not target_email:
            return None

        for row in fetch_all_user_rows(force_refresh=force_refresh):
            if str(row.get("email", "")).strip().lower() == target_email:
                return cls(row)

        if not force_refresh:
            for row in fetch_all_user_rows(force_refresh=True):
                if str(row.get("email", "")).strip().lower() == target_email:
                    return cls(row)

        return None
