import time
import threading
import functools

store = {}
lock = threading.Lock()
ttl_seconds = 600


# cache the result of an api function for a short time, keyed by its arguments
def cached(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        key = (fn.__module__, fn.__name__, args, tuple(sorted(kwargs.items())))
        now = time.time()
        with lock:
            hit = store.get(key)
            if hit is not None and now - hit[0] < ttl_seconds:
                return hit[1]
        value = fn(*args, **kwargs)
        with lock:
            store[key] = (now, value)
        return value
    return wrapper


# drop every cached result, called after scores or marts change
def clear():
    with lock:
        store.clear()
