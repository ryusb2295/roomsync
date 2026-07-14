import sqlite3

DB_NAME = "sharemate.db"

def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS houses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        location TEXT NOT NULL,
        invite_code TEXT NOT NULL UNIQUE,
        created_by TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS house_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        house_id INTEGER NOT NULL,
        user_name TEXT NOT NULL,
        role TEXT NOT NULL,
        FOREIGN KEY (house_id) REFERENCES houses(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        house_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        assigned_to TEXT NOT NULL,
        due_date TEXT NOT NULL,
        status TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS shopping_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        house_id INTEGER NOT NULL,
        item_name TEXT NOT NULL,
        added_by TEXT NOT NULL,
        status TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settlements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        house_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        debtor TEXT NOT NULL,
        creditor TEXT NOT NULL,
        amount REAL NOT NULL,
        status TEXT NOT NULL
    )
    """)

    conn.commit()
    conn.close()

# -----------------------------
# Chores
# -----------------------------
def get_chores(house_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM chores WHERE house_id = ? ORDER BY id DESC",
        (house_id,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def add_chore(title, assigned_to, due_date, house_id):
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO chores (house_id, title, assigned_to, due_date, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (house_id, title, assigned_to, due_date, "진행 전")
    )
    conn.commit()
    conn.close()

def update_chore_status(chore_id, status):
    conn = get_connection()
    conn.execute(
        "UPDATE chores SET status = ? WHERE id = ?",
        (status, chore_id)
    )
    conn.commit()
    conn.close()

def delete_chore(chore_id):
    conn = get_connection()
    conn.execute(
        "DELETE FROM chores WHERE id = ?",
        (chore_id,)
    )
    conn.commit()
    conn.close()

# -----------------------------
# Shopping Items
# -----------------------------
def get_shopping_items(house_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM shopping_items WHERE house_id = ? ORDER BY id DESC",
        (house_id,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def add_shopping_item(item_name, added_by, house_id):
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO shopping_items (house_id, item_name, added_by, status)
        VALUES (?, ?, ?, ?)
        """,
        (house_id, item_name, added_by, "구매 필요")
    )
    conn.commit()
    conn.close()

def update_shopping_status(item_id, status):
    conn = get_connection()
    conn.execute(
        "UPDATE shopping_items SET status = ? WHERE id = ?",
        (status, item_id)
    )
    conn.commit()
    conn.close()

def delete_shopping_item(item_id):
    conn = get_connection()
    conn.execute(
        "DELETE FROM shopping_items WHERE id = ?",
        (item_id,)
    )
    conn.commit()
    conn.close()

# -----------------------------
# Settlements
# -----------------------------
def get_settlements(house_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM settlements WHERE house_id = ? ORDER BY id DESC",
        (house_id,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def add_settlement(content, debtor, creditor, amount, house_id):
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO settlements (house_id, content, debtor, creditor, amount, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (house_id, content, debtor, creditor, amount, "대기")
    )
    conn.commit()
    conn.close()

def update_settlement_status(settlement_id, status):
    conn = get_connection()
    conn.execute(
        "UPDATE settlements SET status = ? WHERE id = ?",
        (status, settlement_id)
    )
    conn.commit()
    conn.close()

def delete_settlement(settlement_id):
    conn = get_connection()
    conn.execute(
        "DELETE FROM settlements WHERE id = ?",
        (settlement_id,)
    )
    conn.commit()
    conn.close()

# -----------------------------
# Houses
# -----------------------------
def get_houses_by_user(user_name):
    conn = get_connection()
    rows = conn.execute("""
        SELECT h.*
        FROM houses h
        JOIN house_members hm ON h.id = hm.house_id
        WHERE hm.user_name = ?
        ORDER BY h.id DESC
    """, (user_name,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_house_by_invite_code(invite_code):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM houses WHERE invite_code = ?",
        (invite_code,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def create_house(name, location, invite_code, created_by):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO houses (name, location, invite_code, created_by)
        VALUES (?, ?, ?, ?)
        """,
        (name, location, invite_code, created_by)
    )

    house_id = cursor.lastrowid

    cursor.execute(
        """
        INSERT INTO house_members (house_id, user_name, role)
        VALUES (?, ?, ?)
        """,
        (house_id, created_by, "owner")
    )

    conn.commit()
    conn.close()

    return house_id

def join_house(house_id, user_name):
    conn = get_connection()

    existing = conn.execute(
        """
        SELECT *
        FROM house_members
        WHERE house_id = ? AND user_name = ?
        """,
        (house_id, user_name)
    ).fetchone()

    if existing:
        conn.close()
        return False

    conn.execute(
        """
        INSERT INTO house_members (house_id, user_name, role)
        VALUES (?, ?, ?)
        """,
        (house_id, user_name, "member")
    )

    conn.commit()
    conn.close()

    return True

def count_house_members(house_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) as count FROM house_members WHERE house_id = ?",
        (house_id,)
    ).fetchone()
    conn.close()
    return row["count"]

def ensure_default_house(user_name):
    user_houses = get_houses_by_user(user_name)

    if len(user_houses) > 0:
        return

    existing_house = get_house_by_invite_code("SUNNY123")

    if existing_house:
        join_house(existing_house["id"], user_name)
    else:
        create_house(
            name="Sunny House",
            location="Brisbane",
            invite_code="SUNNY123",
            created_by=user_name
        )

def get_house_members(house_id):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT user_name, role
        FROM house_members
        WHERE house_id = ?
        ORDER BY id ASC
        """,
        (house_id,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_house_by_id(house_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM houses WHERE id = ?",
        (house_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None