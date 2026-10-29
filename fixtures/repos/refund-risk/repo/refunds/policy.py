THRESHOLD = 0.95

def decisions(frame, scores):
    return (scores >= THRESHOLD).astype(int)
