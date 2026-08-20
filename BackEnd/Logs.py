Logs = {
    "Token Info": [],
    "Scan Info": [],
    "Progress Percentage": int(),
    "Harness Layer1": [],
    "Harness Layer2": [],
}


def reset_Logs():
    # Mutate the existing dict in place (clear + update) rather than
    # rebinding the name - modules that did `from Logs import Logs` hold
    # their own reference to this same dict object, and a plain
    # reassignment here wouldn't be visible to them.
    Logs.clear()
    Logs.update({
        "Token Info": [],
        "Scan Info": [],
        "Progress Percentage": int(),
        "Harness Layer1": [],
        "Harness Layer2": [],
    })