THRESHOLD = 0.99

def decisions(frame, scores):
    return (scores >= THRESHOLD).astype(int)
