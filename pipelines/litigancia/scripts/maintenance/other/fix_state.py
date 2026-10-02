import json

from config.paths import COLLECT_ROOT
from config.scripts import DATA_DIR

state_file = DATA_DIR / "recollect_state.json"

with open(state_file, encoding="utf-8") as f:
    state = json.load(f)

original_count = len(state.get("processed_ranges", []))
valid_ranges = []

print("Scanning folders for missing .db files...")

for key in state.get("processed_ranges", []):
    folder_suffix = key.replace("/", "_")
    db_path = (
        COLLECT_ROOT
        / f"coleta_fazenda_{folder_suffix}"
        / "process_grouped_all_assuntos.db"
    )

    if db_path.exists():
        valid_ranges.append(key)
    else:
        print(f"Removing {key} from state (missing DB file)")
        if key in state.get("attempt_tracker", {}):
            state["attempt_tracker"][key] = 0

state["processed_ranges"] = valid_ranges
with open(state_file, "w", encoding="utf-8") as f:
    json.dump(state, f, indent=4)

print(
    f"\nDone! Removed {original_count - len(valid_ranges)} empty folders "
    "from the completion list."
)
print("You can now start your scraper again!")
