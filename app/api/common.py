import datetime
import math
import pandas as pd


# convert a dataframe to json safe records
def records(frame):
    out = []
    for row in frame.to_dict("records"):
        clean = {}
        for k, v in row.items():
            if isinstance(v, (pd.Timestamp, datetime.datetime)):
                clean[k] = str(v)[:10] if (v.hour == 0 and v.minute == 0 and v.second == 0 and v.microsecond == 0) else str(v)[:19]
            elif isinstance(v, datetime.date):
                clean[k] = str(v)
            elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                clean[k] = None
            elif hasattr(v, "item"):
                clean[k] = v.item()
            else:
                clean[k] = v
        out.append(clean)
    return out
