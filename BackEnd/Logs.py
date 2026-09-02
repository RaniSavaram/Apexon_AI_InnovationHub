class ObservableList(list):
    def __init__(self, seq=(), log_type="Scan Info"):
        super().__init__(seq)
        self.log_type = log_type

    def append(self, item):
        if not self or self[-1] != item:
            super().append(item)
            try:
                from Migrator.views import update_scan_job_state, thread_local
                scan_id = getattr(thread_local, "active_scan_id", None)
                if scan_id:
                    # Direct update to job-specific logs, bypass global replication
                    update_scan_job_state(scan_id, log_entry=item, log_type=self.log_type, skip_global=True)
            except Exception:
                pass

    def extend(self, seq):
        super().extend(seq)
        for item in seq:
            try:
                from Migrator.views import update_scan_job_state, thread_local
                scan_id = getattr(thread_local, "active_scan_id", None)
                if scan_id:
                    update_scan_job_state(scan_id, log_entry=item, log_type=self.log_type, skip_global=True)
            except Exception:
                pass


Logs = {
    "Token Info": ObservableList(log_type="Token Info"),
    "Scan Info": ObservableList(log_type="Scan Info"),
    "Progress Percentage": int(),
    "Harness Layer1": ObservableList(log_type="Harness Layer1"),
    "Harness Layer2": ObservableList(log_type="Harness Layer2")
}


def reset_Logs():
    Logs.clear()
    Logs.update({
        "Token Info": ObservableList(log_type="Token Info"),
        "Scan Info": ObservableList(log_type="Scan Info"),
        "Progress Percentage": int(),
        "Harness Layer1": ObservableList(log_type="Harness Layer1"),
        "Harness Layer2": ObservableList(log_type="Harness Layer2")
    })