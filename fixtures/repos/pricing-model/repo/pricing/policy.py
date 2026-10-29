THRESHOLD = 0.5

def decisions(frame, scores):
    return (scores >= THRESHOLD).astype(int)
