import numpy as np

FEATURES = ["signal", "aux"]

def matrix(frame):
    return frame[FEATURES].fillna(0).to_numpy(dtype=float)
