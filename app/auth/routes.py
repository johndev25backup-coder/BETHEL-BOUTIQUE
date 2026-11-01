"""
Create the first OWNER account (and, optionally, the database tables).

Run from the project root (the folder that contains the `app` folder):

    python create_owner.py                  # just create the owner
    python create_owner.py --create-tables  # create missing tables first

To do this on the live database, set DATABASE_URL in the SAME terminal window
first (see the instructions in chat), then run the command. The password is
typed with getpass, so it is not shown or saved in your shell history.
"""
import getpass
import sys

from app import create_app
from app.auth.routes import _hash_password
from app.extensions import db
from app.models import User, UserRole


def main():
    create_tables = "--create-tables" in sys.argv

    name = input("Owner name: ").strip()
    email = input("Owner email (used to log in): ").strip().lower()
    phone = input("Owner phone (press Enter to skip): ").strip() or None
    password = getpass.getpass("Password (at least 10 characters): ")
    confirm = getpass.getpass("Repeat password: ")

    if not name or not email:
        sys.exit("Name and email are required.")
    if len(password) < 10:
        sys.exit("Password must be at least 10 characters.")
    if password != confirm:
        sys.exit("Passwords do not match.")

    app = create_app()
    with app.app_context():
        if create_tables:
            db.create_all()
            print("Tables checked/created.")

        if User.query.filter_by(email=email).first():
            sys.exit(f"A user with email {email} already exists. Nothing changed.")

        user = User(name=name, email=email, phone=phone, role=UserRole.OWNER)
        user.password_hash = _hash_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"Owner created: {email} (id {user.id}). You can now use staff login.")


if __name__ == "__main__":
    main()
