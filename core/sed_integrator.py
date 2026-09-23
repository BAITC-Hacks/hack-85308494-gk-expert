import os
import json
import time
import xml.etree.ElementTree as ET
from xml.dom import minidom
from typing import Dict, List, Optional


class SEDIntegrator:
    """
    Integration module for Corporate Electronic Document Management Systems (СЭД):
    - Documentolog (used widely in Kazakhstan public and corporate sector)
    - 1C:Документооборот КОРП
    - Directum RX
    Exports protocol cards and individual task/assignment cards in standardized JSON & XML.
    """

    def __init__(self, export_dir: str = "storage/sed_exports"):
        self.export_dir = export_dir
        os.makedirs(self.export_dir, exist_ok=True)

    def export_to_sed_json(self, protocol_data: Dict, filename: Optional[str] = None) -> str:
        """
        Generate standardized SED document card with embedded tasks in JSON.
        """
        reg_number = f"ПР-{time.strftime('%Y%m%d')}-{protocol_data.get('id', '001')}"
        if not filename:
            filename = f"SED_Card_{reg_number}.json"
        output_path = os.path.join(self.export_dir, filename)

        sed_card = {
            "sed_system": "Documentolog / 1C:Enterprise / Directum SED Standard v2.4",
            "document_type": "Протокол оперативного совещания",
            "registration_number": reg_number,
            "registration_date": protocol_data.get("date", time.strftime("%Y-%m-%d")),
            "organization": protocol_data.get("company", "АО «Самрук-Қазына Өңдеу»"),
            "author": protocol_data.get("leader", "Председатель совещания"),
            "theme": protocol_data.get("title", "Оперативное совещание"),
            "status": "Зарегистрирован",
            "access_level": "Для служебного пользования (ДСП)",
            "agenda_items": protocol_data.get("agenda", []),
            "participants": protocol_data.get("participants", []),
            "tasks_count": len(protocol_data.get("tasks", [])),
            "tasks": []
        }

        for t in protocol_data.get("tasks", []):
            task_card = {
                "task_id": f"{reg_number}/П-{t.get('id', 1)}",
                "content": t.get("task", ""),
                "responsible_person": t.get("assignee", ""),
                "department": t.get("department", "Профильное подразделение"),
                "control_deadline": t.get("deadline", ""),
                "priority": t.get("priority", "Обычный"),
                "state": t.get("status", "В работе"),
                "source_transcript_quote": t.get("source_quote", ""),
                "reminder_days_before": [3, 1]
            }
            sed_card["tasks"].append(task_card)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(sed_card, f, ensure_ascii=False, indent=2)

        print(f"[SED] Exported SED JSON card: {output_path}")
        return output_path

    def export_to_sed_xml(self, protocol_data: Dict, filename: Optional[str] = None) -> str:
        """
        Generate standard GOST/1C SED XML package.
        """
        reg_number = f"ПР-{time.strftime('%Y%m%d')}-{protocol_data.get('id', '001')}"
        if not filename:
            filename = f"SED_Package_{reg_number}.xml"
        output_path = os.path.join(self.export_dir, filename)

        root = ET.Element("SED_MeetingProtocol")
        root.set("schemaVersion", "2.1")
        root.set("exportDate", time.strftime("%Y-%m-%dT%H:%M:%S"))

        # Header Info
        header = ET.SubElement(root, "DocumentHeader")
        ET.SubElement(header, "RegistrationNumber").text = reg_number
        ET.SubElement(header, "Date").text = protocol_data.get("date", time.strftime("%Y-%m-%d"))
        ET.SubElement(header, "Company").text = protocol_data.get("company", "АО «Самрук-Қазына Өңдеу»")
        ET.SubElement(header, "Title").text = protocol_data.get("title", "")
        ET.SubElement(header, "Leader").text = protocol_data.get("leader", "")

        # Agenda
        agenda_el = ET.SubElement(root, "Agenda")
        for item in protocol_data.get("agenda", []):
            ET.SubElement(agenda_el, "Item").text = item

        # Tasks List
        tasks_el = ET.SubElement(root, "Assignments")
        for t in protocol_data.get("tasks", []):
            assignment = ET.SubElement(tasks_el, "Assignment")
            assignment.set("id", f"{reg_number}/П-{t.get('id', 1)}")
            ET.SubElement(assignment, "TaskDescription").text = t.get("task", "")
            ET.SubElement(assignment, "Assignee").text = t.get("assignee", "")
            ET.SubElement(assignment, "Deadline").text = t.get("deadline", "")
            ET.SubElement(assignment, "Priority").text = t.get("priority", "Обычный")
            ET.SubElement(assignment, "Department").text = t.get("department", "")
            ET.SubElement(assignment, "Status").text = t.get("status", "В работе")
            ET.SubElement(assignment, "SourceQuote").text = t.get("source_quote", "")

        rough_string = ET.tostring(root, 'utf-8')
        reparsed = minidom.parseString(rough_string)
        pretty_xml = reparsed.toprettyxml(indent="  ", encoding="utf-8")

        with open(output_path, "wb") as f:
            f.write(pretty_xml)

        print(f"[SED] Exported SED XML package: {output_path}")
        return output_path
