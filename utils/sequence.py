class Sequence:
    def __init__(self, start=1, step=1):
        self.start = start
        self.step = step
        self.now = start - step
    
    @property
    def next(self):
        self.now += self.step
        return self.now
    
    def reset(self):
        self.now = self.start - self.step
