import os
import json
import threading
import numpy as np
import pandas as pd
from fastapi import APIRouter
from app import config
from app import db
from app.api.common import records

router = APIRouter(prefix="/api/market")
search_lock = threading.Lock()
search_state = {"model": None, "vectors": None, "texts": None}


# json file exported by a notebook, empty dict when missing
def load_json(name):
    path = os.path.join(config.MODELS_DIR, name)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


# lazily load the sentence model and the review vectors
def ensure_search():
    with search_lock:
        if search_state["model"] is not None:
            return True
        vec_path = os.path.join(config.MODELS_DIR, "review_vectors.npy")
        txt_path = os.path.join(config.MODELS_DIR, "review_texts.parquet")
        if not os.path.exists(vec_path) or not os.path.exists(txt_path):
            return False
        from sentence_transformers import SentenceTransformer
        topics = load_json("review_topics.json")
        search_state["model"] = SentenceTransformer(topics.get("model", "sentence-transformers/all-MiniLM-L6-v2"), device="cpu")
        search_state["vectors"] = np.load(vec_path)
        search_state["texts"] = pd.read_parquet(txt_path)
        return True


# accents stripped in the inside airbnb export, restored for display
accent_fixes = {"Misericrdia": "Misericórdia", "Santo Antnio": "Santo António", "Parque das Naes": "Parque das Nações", "Belm": "Belém", "Lourinh e Atalaia": "Lourinhã e Atalaia", "So Joo das Lampas e Terrugem": "São João das Lampas e Terrugem", "Oeiras e S.Julio da Barra, Pao de Arcos e Caxias": "Oeiras e S.Julião da Barra, Paço de Arcos e Caxias", "So Miguel, S.Martinho, S.Pedro Penaferrim": "São Miguel, S.Martinho, S.Pedro Penaferrim", "Penha de Frana": "Penha de França", "So Domingos de Benfica": "São Domingos de Benfica", "So Vicente": "São Vicente", "Alcntara": "Alcântara", "Alcabideche": "Alcabideche", "So Joo das Lampas": "São João das Lampas", "Sintra (Santa Maria e So Miguel, So Martinho e So Pedro de Penaferrim)": "Sintra (Santa Maria e São Miguel, São Martinho e São Pedro de Penaferrim)", "Queluz e Belas": "Queluz e Belas", "Mafra": "Mafra", "Algueiro-Mem Martins": "Algueirão-Mem Martins", "Carvoeira": "Carvoeira", "Santo Isidoro": "Santo Isidoro", "Ericeira": "Ericeira", "Colares": "Colares"}


def fix_name(name):
    return accent_fixes.get(name, name)


# lisbon airbnb market overview from the elasticity notebook and the listings table
@router.get("/overview")
def overview():
    el = load_json("elasticity.json")
    for n in el.get("airbnb", {}).get("neighbourhoods", []):
        n["neighbourhood_cleansed"] = fix_name(n["neighbourhood_cleansed"])
    summary = db.query_df(
        "select count(*) as listings, round(median(price_num), 0) as median_price, round(avg(case when room_type = 'Entire home/apt' then 1.0 else 0.0 end), 3) as entire_home_share, round(avg(try_cast(review_scores_rating as double)), 2) as rating "
        "from raw.airbnb_listings where price_num between 20 and 1500")
    room_types = db.query_df("select room_type, count(*) as listings, round(median(price_num), 0) as median_price from raw.airbnb_listings where price_num between 20 and 1500 group by 1 order by 2 desc")
    from app.feeds import airbnb
    return {"summary": records(summary)[0], "room_types": records(room_types), "snapshot_date": airbnb.snapshot_date(), "airbnb": el.get("airbnb", {}), "demand": el.get("demand", {}), "demand_by_season": el.get("demand_by_season", []), "cancellation": el.get("cancellation", {})}


# review topics and the neighbourhood by topic table
@router.get("/topics")
def topics():
    return load_json("review_topics.json")


# semantic search over the embedded reviews
@router.get("/search")
def search(q: str = "", limit: int = 10):
    if q.strip() == "" or not ensure_search():
        return {"available": search_state["model"] is not None, "results": []}
    qv = search_state["model"].encode([q], normalize_embeddings=True)[0]
    sims = search_state["vectors"] @ qv
    idx = np.argsort(sims)[::-1][:limit]
    texts = search_state["texts"].iloc[idx].copy()
    texts["similarity"] = sims[idx].round(3)
    topics = load_json("review_topics.json").get("topics", [])
    words = {t["topic"]: t["words"] for t in topics}
    texts["topic_words"] = texts["topic"].map(words)
    total = len(search_state["vectors"])
    share_neg = float((search_state["texts"].iloc[np.argsort(sims)[::-1][:200]]["sentiment"] < 0).mean())
    return {"available": True, "query": q, "results": records(texts[["date", "text", "topic", "topic_words", "sentiment", "similarity"]]), "negative_share_top200": round(share_neg, 3), "corpus": total}


# review aspects from keyword lexicons, negative share per aspect and per neighbourhood
@router.get("/aspects")
def aspects():
    out = load_json("review_aspects.json")
    for r in out.get("by_neighbourhood", []):
        r["neighbourhood_cleansed"] = fix_name(r["neighbourhood_cleansed"])
    return out
