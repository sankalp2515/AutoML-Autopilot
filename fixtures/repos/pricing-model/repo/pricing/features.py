import numpy as np

def matrix(frame):
    ratio = frame.quote_amount.to_numpy() / frame.list_price.to_numpy()
    return np.column_stack([ratio, frame.aux.to_numpy()])
