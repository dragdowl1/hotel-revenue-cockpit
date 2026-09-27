import os
import glob
import json
import joblib
from app import config

CHAMPION_FILE = os.path.join(config.MODELS_DIR, "champion.json")


# list available cancellation model versions
def versions():
    paths = glob.glob(os.path.join(config.MODELS_DIR, "cancellation_v*.joblib"))
    names = [os.path.basename(p).replace(".joblib", "") for p in paths]
    return sorted(names, key=lambda n: int(n.split("_v")[1]))


# name of the champion model, the pointer file or the highest version
def champion_name():
    if os.path.exists(CHAMPION_FILE):
        with open(CHAMPION_FILE) as f:
            return json.load(f)["version"]
    names = versions()
    if len(names) == 0:
        return None
    return names[-1]


# set the champion pointer
def set_champion(name):
    with open(CHAMPION_FILE, "w") as f:
        json.dump({"version": name}, f)


# load one artifact by name
def load(name):
    return joblib.load(os.path.join(config.MODELS_DIR, name + ".joblib"))


# load the champion artifact or none
def load_champion():
    name = champion_name()
    if name is None:
        return None
    return load(name)


# load the metrics file of one version
def metrics(name):
    path = os.path.join(config.MODELS_DIR, name + "_metrics.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


# load the learning curve and importance file of one version
def curves(name):
    path = os.path.join(config.MODELS_DIR, name + "_curves.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


# next free version name
def next_version():
    names = versions()
    if len(names) == 0:
        return "cancellation_v1"
    return "cancellation_v" + str(int(names[-1].split("_v")[1]) + 1)
