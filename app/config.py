import os

# root of the project inside the container or on the host
ROOT_DIR = os.getcwd()
DATA_DIR = os.path.join(ROOT_DIR, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
PARQUET_DIR = os.path.join(DATA_DIR, "parquet")
MODELS_DIR = os.path.join(DATA_DIR, "models")
DB_PATH = os.path.join(DATA_DIR, "lodging.duckdb")
DBT_DIR = os.path.join(ROOT_DIR, "dbt")
UI_DIR = os.path.join(ROOT_DIR, "ui")

# app settings from environment
app_name = os.environ.get("APP_NAME", "Lisbon Lodging Cockpit")
public_url = os.environ.get("PUBLIC_URL", "http://localhost:8001")
user_agent = os.environ.get("USER_AGENT", "lisbon-lodging-cockpit/1.0")
city_lat = float(os.environ.get("HOTEL_CITY_LAT", "38.7223"))
city_lon = float(os.environ.get("HOTEL_CITY_LON", "-9.1393"))
resort_lat = float(os.environ.get("HOTEL_RESORT_LAT", "37.0194"))
resort_lon = float(os.environ.get("HOTEL_RESORT_LON", "-7.9304"))
replay_speed = float(os.environ.get("REPLAY_SPEED", "1.0"))
grafana_url = os.environ.get("GRAFANA_URL", "http://localhost:3000")
