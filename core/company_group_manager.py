import os
import json
import time
from typing import Dict, List, Optional


class CompanyGroupManager:
    """
    Manages:
    - Companies and holding entities (e.g. Samruk-Kazyna Ondeu, Pavlodar Plant, etc.)
    - Custom user groups (e.g. "Химический кластер", "Инвестиционный комитет", "Подрядчики")
    - Real-time and post-meeting notes & memos linked to meetings and timestamps.
    """

    DEFAULT_COMPANIES = [{"id": "main_org", "name": "Организация", "type": "Организация", "code": "ORG", "color": "#38bdf8"}]
    DEFAULT_GROUPS = [{"id": "general", "name": "Общие совещания", "company_id": "main_org"}]

    def __init__(self, data_dir: str = "storage"):
        self.data_dir = data_dir
        self.companies_file = os.path.join(data_dir, "companies.json")
        self.notes_file = os.path.join(data_dir, "meeting_notes.json")
        os.makedirs(data_dir, exist_ok=True)
        self._init_storage()

    def _init_storage(self):
        if not os.path.exists(self.companies_file):
            data = {
                "companies": self.DEFAULT_COMPANIES,
                "groups": self.DEFAULT_GROUPS
            }
            with open(self.companies_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        if not os.path.exists(self.notes_file):
            with open(self.notes_file, "w", encoding="utf-8") as f:
                json.dump({"notes": [], "memos": []}, f, ensure_ascii=False, indent=2)

    def get_companies(self) -> List[Dict]:
        with open(self.companies_file, "r", encoding="utf-8") as f:
            return json.load(f).get("companies", [])

    def get_groups(self, company_id: Optional[str] = None) -> List[Dict]:
        with open(self.companies_file, "r", encoding="utf-8") as f:
            groups = json.load(f).get("groups", [])
            if company_id:
                return [g for g in groups if g.get("company_id") == company_id]
            return groups

    def add_company(self, name: str, comp_type: str = "Дочерняя компания", code: str = "") -> Dict:
        comp_id = f"comp_{int(time.time())}"
        new_comp = {
            "id": comp_id,
            "name": name,
            "type": comp_type,
            "code": code or name[:4].upper(),
            "color": "#06b6d4"
        }
        with open(self.companies_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("companies", []).append(new_comp)
        with open(self.companies_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return new_comp

    def add_group(self, name: str, company_id: str) -> Dict:
        grp_id = f"grp_{int(time.time())}"
        new_grp = {
            "id": grp_id,
            "name": name,
            "company_id": company_id
        }
        with open(self.companies_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("groups", []).append(new_grp)
        with open(self.companies_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return new_grp

    def add_note(self, meeting_id: str, text: str, timestamp_str: str = "00:00", category: str = "Заметка", author: str = "Секретарь") -> Dict:
        """Add a real-time note taken during meeting."""
        with open(self.notes_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        note_id = int(time.time() * 1000)
        note = {
            "id": note_id,
            "meeting_id": meeting_id,
            "text": text,
            "timestamp": timestamp_str,
            "category": category,
            "author": author,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        data.setdefault("notes", []).append(note)

        with open(self.notes_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return note

    def get_notes(self, meeting_id: str) -> List[Dict]:
        with open(self.notes_file, "r", encoding="utf-8") as f:
            notes = json.load(f).get("notes", [])
            return [n for n in notes if n.get("meeting_id") == meeting_id]

    def save_memo(self, meeting_id: str, memo_data: Dict) -> Dict:
        """Save a generated executive memo / action briefing."""
        with open(self.notes_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        memo_data["meeting_id"] = meeting_id
        memo_data["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        data.setdefault("memos", []).append(memo_data)

        with open(self.notes_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return memo_data

    def get_memos(self, meeting_id: str) -> List[Dict]:
        with open(self.notes_file, "r", encoding="utf-8") as f:
            memos = json.load(f).get("memos", [])
            return [m for m in memos if m.get("meeting_id") == meeting_id]
