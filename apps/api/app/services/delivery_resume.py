"""Detect suspension or a clock jump without persisting outside portable data."""
import time


class ResumeDetector:
    def __init__(self,threshold=30):
        self.threshold=threshold
        self.previous=None

    def sample(self,wall=None,monotonic=None):
        current=(time.time() if wall is None else wall,time.monotonic() if monotonic is None else monotonic)
        previous,self.previous=self.previous,current
        if previous is None:
            return False
        wall_gap,elapsed=current[0]-previous[0],current[1]-previous[1]
        return elapsed>self.threshold or wall_gap>self.threshold or wall_gap<0 or abs(wall_gap-elapsed)>self.threshold
