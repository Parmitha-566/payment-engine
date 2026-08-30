import json
import sqlite3
from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel

DB_FILE = "payments.db"

# ----------------------------------------------------
# DATABASE SETUP
# ----------------------------------------------------
def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS wallets (
        user_id TEXT PRIMARY KEY,
        balance_cents INTEGER NOT NULL CHECK (balance_cents >= 0)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS idempotency_keys (
        key TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        response_code INTEGER,
        response_body TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cursor.execute("INSERT OR IGNORE INTO wallets (user_id, balance_cents) VALUES ('alice', 1000);")
    cursor.execute("INSERT OR IGNORE INTO wallets (user_id, balance_cents) VALUES ('bob', 0);")
    conn.close()

# ----------------------------------------------------
# API & TRANSACTION LOGIC
# ----------------------------------------------------
app = FastAPI(title="Idempotent Payment Engine")

init_db()

class TransferRequest(BaseModel):
    sender_id: str
    receiver_id: str
    amount_cents: int

@app.post("/api/v1/transfer")
def transfer_funds(
    request: TransferRequest,
    idempotency_key: str = Header(..., description="Unique UUID per client attempt")
):
    if request.amount_cents <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero.")
    if request.sender_id == request.receiver_id:
        raise HTTPException(status_code=400, detail="Cannot transfer funds to self.")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 1. Idempotency Check
        cursor.execute("SELECT * FROM idempotency_keys WHERE key = ?", (idempotency_key,))
        existing_record = cursor.fetchone()

        if existing_record:
            if existing_record["status"] == "COMPLETED":
                return json.loads(existing_record["response_body"])
            elif existing_record["status"] == "PROCESSING":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A transaction with this Idempotency-Key is currently in progress."
                )

        cursor.execute(
            "INSERT INTO idempotency_keys (key, status) VALUES (?, 'PROCESSING')",
            (idempotency_key,)
        )

        # 2. Locking & Transfer Execution
        cursor.execute("BEGIN EXCLUSIVE TRANSACTION;")

        cursor.execute("SELECT balance_cents FROM wallets WHERE user_id = ?", (request.sender_id,))
        sender_wallet = cursor.fetchone()

        if not sender_wallet:
            cursor.execute("ROLLBACK;")
            raise HTTPException(status_code=404, detail="Sender not found.")

        if sender_wallet["balance_cents"] < request.amount_cents:
            cursor.execute("ROLLBACK;")
            cursor.execute(
                "UPDATE idempotency_keys SET status = 'COMPLETED', response_code = 400, response_body = ? WHERE key = ?",
                (json.dumps({"detail": "Insufficient funds"}), idempotency_key)
            )
            raise HTTPException(status_code=400, detail="Insufficient funds.")

        cursor.execute(
            "UPDATE wallets SET balance_cents = balance_cents - ? WHERE user_id = ?",
            (request.amount_cents, request.sender_id)
        )
        cursor.execute(
            "UPDATE wallets SET balance_cents = balance_cents + ? WHERE user_id = ?",
            (request.amount_cents, request.receiver_id)
        )

        success_response = {
            "status": "SUCCESS",
            "sender_id": request.sender_id,
            "receiver_id": request.receiver_id,
            "amount_transferred_cents": request.amount_cents,
            "remaining_balance_cents": sender_wallet["balance_cents"] - request.amount_cents
        }

        cursor.execute(
            "UPDATE idempotency_keys SET status = 'COMPLETED', response_code = 200, response_body = ? WHERE key = ?",
            (json.dumps(success_response), idempotency_key)
        )

        cursor.execute("COMMIT;")
        return success_response

    except sqlite3.OperationalError as e:
        cursor.execute("ROLLBACK;")
        raise HTTPException(status_code=500, detail=f"Database lock error: {str(e)}")
    finally:
        conn.close()

@app.get("/api/v1/wallets/{user_id}")
def get_balance(user_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM wallets WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user_id": row["user_id"], "balance_cents": row["balance_cents"]}