import threading
import uuid
import requests

API_URL = "http://127.0.0.1:8000/api/v1/transfer"

def send_transfer(idempotency_key, results_list):
    headers = {"Idempotency-Key": idempotency_key}
    payload = {
        "sender_id": "alice",
        "receiver_id": "bob",
        "amount_cents": 1000
    }
    try:
        response = requests.post(API_URL, json=payload, headers=headers)
        results_list.append((response.status_code, response.json()))
    except Exception as e:
        results_list.append((500, str(e)))

print("--- TEST 1: NETWORK RETRIES (IDEMPOTENCY TEST) ---")
shared_key = str(uuid.uuid4())
retry_results = []

for i in range(3):
    send_transfer(shared_key, retry_results)

for idx, res in enumerate(retry_results, 1):
    print(f"Attempt {idx}: Status {res[0]} -> {res[1]}")

print("\n--- TEST 2: RACE CONDITION / DOUBLE-SPENDING TEST ---")
from main import get_db_connection
conn = get_db_connection()
conn.execute("UPDATE wallets SET balance_cents = 1000 WHERE user_id = 'alice'")
conn.execute("UPDATE wallets SET balance_cents = 0 WHERE user_id = 'bob'")
conn.execute("DELETE FROM idempotency_keys")
conn.close()

concurrent_results = []
threads = []

for _ in range(10):
    unique_key = str(uuid.uuid4())
    t = threading.Thread(target=send_transfer, args=(unique_key, concurrent_results))
    threads.append(t)
    t.start()

for t in threads:
    t.join()

successful_transfers = sum(1 for status_code, _ in concurrent_results if status_code == 200)
failed_transfers = sum(1 for status_code, _ in concurrent_results if status_code == 400)

print(f"Total Requests: 10")
print(f"Successful (200 OK): {successful_transfers} (Expected: 1)")
print(f"Rejected (400 Insufficient Funds): {failed_transfers} (Expected: 9)")

alice_bal = requests.get("http://127.0.0.1:8000/api/v1/wallets/alice").json()
bob_bal = requests.get("http://127.0.0.1:8000/api/v1/wallets/bob").json()
print(f"Alice's Final Balance: {alice_bal['balance_cents']} paise (Rs {alice_bal['balance_cents']/100})")
print(f"Bob's Final Balance: {bob_bal['balance_cents']} paise (Rs {bob_bal['balance_cents']/100})")