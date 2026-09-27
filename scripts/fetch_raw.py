import os
import sys
import httpx

sys.path.insert(0, os.getcwd())
from app import config

# public mirror of the hotel booking demand dataset (antonio, de almeida and nunes, 2019, cc by 4.0)
SOURCE_URL = "https://raw.githubusercontent.com/rfordatascience/tidytuesday/master/data/2020/2020-02-11/hotels.csv"
RAW_CSV = os.path.join(config.RAW_DIR, "hotel_bookings.csv")


# download the raw bookings csv when it is not present yet
def fetch():
    if os.path.exists(RAW_CSV) and os.path.getsize(RAW_CSV) > 1_000_000:
        print("raw csv present: " + RAW_CSV)
        return
    os.makedirs(config.RAW_DIR, exist_ok=True)
    with httpx.Client(headers={"User-Agent": config.user_agent}, timeout=120.0, follow_redirects=True) as client:
        response = client.get(SOURCE_URL)
        response.raise_for_status()
    with open(RAW_CSV, "wb") as f:
        f.write(response.content)
    print("downloaded " + str(len(response.content)) + " bytes to " + RAW_CSV)


if __name__ == "__main__":
    fetch()
