"""Shared session selection application service; never persists job state."""
class JobSelectionService:
    def __init__(self):
        self.checked_ids = set()

    def select_visible(self, job_ids, checked):
        if checked:
            self.checked_ids.update(job_ids)
        else:
            self.checked_ids.difference_update(job_ids)
