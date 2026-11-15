
import os

import requests
from dotenv import load_dotenv

load_dotenv()

def test_fred():
    print("\n--- TESTING FRED ---")
    key = os.getenv("FRED_API_KEY")

    if not key:
        print("MISSING: FRED_API_KEY")
        return

    try:
        response = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": "CPIAUCSL",
                "api_key": key,
                "file_type": "json",
                "limit": 5,
                "sort_order": "desc"
            },
            timeout=15
        )

        if response.status_code == 200:
            data = response.json()
            observations = data.get("observations", [])
            print("SUCCESS: FRED connected!")
            print("CPI records received:", len(observations))
            for item in observations[:3]:
                print(item["date"], item["value"])
        else:
            print("FAILED: HTTP", response.status_code)
            print("Check FRED key and permissions.")

    except requests.RequestException as exc:
        print("Connection error:", type(exc).__name__)


def test_nessie():
    print("\n--- TESTING CAPITAL ONE NESSIE ---")
    key = os.getenv("NESSIE_API_KEY")

    if not key:
        print("MISSING: NESSIE_API_KEY")
        return

    base = os.getenv(
        "NESSIE_BASE_URL",
        "https://api.nessieisreal.com"
    ).rstrip("/")

    try:
        response = requests.get(
            f"{base}/customers",
            params={"key": key},
            timeout=15
        )

        if response.status_code == 200:
            data = response.json()
            # Nessie answers GETs with 200 even for an invalid key; only a write validates it.
            print("REACHABLE: Nessie responded (this does NOT prove the key is valid;")
            print("  run `python -m decisiondesk.nessie_sandbox init` / a write to verify).")
            if isinstance(data, list):
                print("Customers received:", len(data))
            else:
                print("Response received successfully.")
        else:
            print("FAILED: HTTP", response.status_code)
            print("Check Nessie key, endpoint and permissions.")

    except requests.RequestException as exc:
        print("Connection error:", type(exc).__name__)


if __name__ == "__main__":
    test_fred()
    test_nessie()
