import os
import json
import time
from datetime import datetime
from typing import Dict, List, Optional


class MeetingManager:
    """
    Manages meeting histories, structured protocols, action items, and task statuses.
    """

    def __init__(self, data_dir: str = "storage/meetings"):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)

    def save_meeting(self, meeting_data: Dict) -> str:
        """Save meeting data as JSON."""
        meeting_id = meeting_data.get("id") or f"meet_{int(time.time())}"
        meeting_data["id"] = meeting_id
        meeting_data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if "created_at" not in meeting_data:
            meeting_data["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        meeting_data.setdefault("saved_at", datetime.now().astimezone().isoformat(timespec='seconds'))

        file_path = os.path.join(self.data_dir, f"{meeting_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(meeting_data, f, ensure_ascii=False, indent=2)
        print(f"[MeetingManager] Saved meeting {meeting_id} to {file_path}")
        return meeting_id

    def get_meeting(self, meeting_id: str) -> Optional[Dict]:
        """Load single meeting by id."""
        file_path = os.path.join(self.data_dir, f"{meeting_id}.json")
        if not os.path.exists(file_path):
            return None
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_meetings(self) -> List[Dict]:
        """List all saved meetings sorted by date descending."""
        meetings = []
        for fname in os.listdir(self.data_dir):
            if fname.endswith(".json"):
                fpath = os.path.join(self.data_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        meetings.append({
                            "id": data.get("id", fname.replace(".json", "")),
                            "title": data.get("title", "Без названия"),
                            "date": data.get("date", ""),
                            "created_at": data.get("created_at", ""),
                            "saved_at": data.get("recording_saved_at") or data.get("saved_at") or data.get("created_at", ""),
                            "protocol_saved_at": data.get("saved_at", data.get("created_at", "")),
                            "company_id": data.get("company_id", "main_org"),
                            "leader": data.get("leader", ""),
                            "tasks_count": len(data.get("tasks", [])),
                            "participants_count": len(data.get("participants", [])),
                            "duration": data.get("audio_duration", 0),
                            "audio_filename": data.get("audio_filename", "")
                        })
                except Exception as e:
                    print(f"[MeetingManager] Error reading {fname}: {e}")

        # Sort newest first
        meetings.sort(key=lambda m: m.get("saved_at", ""), reverse=True)
        return meetings

    def update_task_status(self, meeting_id: str, task_id: int, new_status: str) -> bool:
        """Update the status of a specific action item."""
        meeting = self.get_meeting(meeting_id)
        if not meeting:
            return False
        updated = False
        for t in meeting.get("tasks", []):
            if t.get("id") == task_id:
                t["status"] = new_status
                updated = True
                break
        if updated:
            self.save_meeting(meeting)
        return updated
