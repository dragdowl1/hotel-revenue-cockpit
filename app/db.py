import threading
import duckdb
from app import config

# one process wide lock so scheduler jobs, dbt and api requests never write concurrently
db_lock = threading.RLock()


# duckdb memory cap per connection, below the container limit
memory_limit = "1GB"


# open a connection to the project database with a memory cap
def connect(read_only=False):
    con = duckdb.connect(config.DB_PATH, read_only=read_only)
    con.execute("set memory_limit = '" + memory_limit + "'")
    con.execute("set threads = 2")
    return con


# run one query and return a pandas dataframe
def query_df(sql, params=None):
    with db_lock:
        con = connect()
        try:
            if params is None:
                return con.execute(sql).df()
            return con.execute(sql, params).df()
        finally:
            con.close()


# run one statement without returning rows
def execute(sql, params=None):
    with db_lock:
        con = connect()
        try:
            if params is None:
                con.execute(sql)
            else:
                con.execute(sql, params)
        finally:
            con.close()
