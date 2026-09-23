"""Conservative local extraction without network clients or cloud fallback."""
import os
import re
from datetime import datetime
from typing import Dict, Optional


class NLPExtractor:
    """Rule-based RU/KK draft protocol consuming acoustic IDs and reviewable names."""

    SYSTEM_PROMPT = """Ты — секретарь совещания любой организации.
Сохраняй факты и цитаты из стенограммы на русском, казахском или смешанном языке.
Выделяй поручения, ответственных, сроки, решения и риски. Не выдумывай имена,
даты или решения. Неизвестные поля отмечай как «Не указан»."""

    NAME = r"[А-ЯЁӘІҢҒҮҰҚӨҺA-Z][а-яёәіңғүұқөһa-z]+(?:[- ][А-ЯЁӘІҢҒҮҰҚӨҺA-Z][а-яёәіңғүұқөһa-z]+){0,2}"
    ACTION = re.compile(
        r"\b(?:поручаю|поручено|прошу|необходимо|нужно|обязую|назначить|"
        r"подготов(?:ить|ьте|лю|им)|разработ(?:ать|айте|аю|аем)|обеспеч(?:ить|ьте|у|им)|"
        r"долож(?:ить|ите|у|им)|предостав(?:ить|ьте|лю|им)|провер(?:ить|ьте|ю|им)|"
        r"отправ(?:ить|ьте|лю|им)|соглас(?:овать|уйте|ую|уем)|ответственн\w*|"
        r"дайында\w*|әзірле\w*|тапсыр\w*|жібер\w*|ұсын\w*|тексер\w*|"
        r"жауапты|орындау|қамтамасыз\s+ет\w*)\b", re.I
    )
    DEADLINE = re.compile(
        r"\b(?:до|к|не\s+позднее|в\s+срок\s+до)\s+"
        r"(?:\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?|\d{1,2}\s+[а-яё]+(?:\s+\d{4})?|"
        r"конца\s+(?:дня|недели|месяца|квартала)|понедельник[ау]?|вторник[ау]?|"
        r"сред[уы]|четверг[ау]?|пятниц[ыа]|суббот[ыа]|воскресень[яе])|"
        r"\b(?:сегодня|завтра|послезавтра|через\s+\d+\s+(?:дн\w*|недел\w*|час\w*))\b|"
        r"\b(?:\d{1,2}\s+)?(?:қаңтар|ақпан|наурыз|сәуір|мамыр|маусым|шілде|тамыз|"
        r"қыркүйек|қазан|қараша|желтоқсан)\w*\s+дейін|"
        r"\b(?:бүгін\w*|ертең\w*|бүрсігүні|дүйсенбі\w*|сейсенбі\w*|сәрсенбі\w*|"
        r"бейсенбі\w*|жұма\w*|сенбі\w*|жексенбі\w*|апта\s+соңына)(?:\s+дейін)?", re.I
    )
    RISK = re.compile(r"\b(?:риск\w*|инцидент\w*|срыв\w*|задержк\w*|штраф\w*|авари\w*|тәуекел\w*|қауіп\w*|кешіг\w*)", re.I)
    DECISION = re.compile(r"\b(?:решили|договорились|утверждаю|принято\s+решение|шешім\s+қабылда\w*|келістік|бекітіл\w*)", re.I)
    URGENT = re.compile(r"\b(?:срочно|немедленно|критич\w*|шұғыл|дереу|жедел)", re.I)

    def __init__(self, api_key: Optional[str] = None):
        # Retained for compatibility with older settings; deliberately unused.
        pass

    def set_api_key(self, api_key: str):
        pass

    def _offline_fallback_extractor(self, transcript_data: Dict) -> Dict:
        return self.process_transcript(transcript_data)

    @classmethod
    def _assignee(cls, text, speaker):
        patterns = (
            rf"(?i:ответственн(?:ый|ая|ые)|жауапты)\s*[:—–-]?\s*({cls.NAME})",
            rf"(?i:поручаю|прошу|поручить)\s+({cls.NAME})",
            rf"^({cls.NAME}),\s*",
            rf"^({cls.NAME})\s+(?i:жауапты)\b",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        if speaker != "Говорящий не определён" and re.search(r"\b(?:я\s+(?:подготовлю|сделаю|отправлю)|мен\s+.*(?:дайындаймын|жіберемін))", text, re.I):
            return speaker
        return "Не указан"

    def process_transcript(self, transcript_data: Dict, model: str = "offline") -> Dict:
        # Even stale UI requests for gpt-4o/codex-astra stay on this computer.
        text = str(transcript_data.get("text") or "").strip()
        segments = transcript_data.get("segments") or ([{"text": text, "start": 0}] if text else [])
        dialogue, tasks, moments, names, sentences = [], [], [], [], []
        profiles = transcript_data.get('speakers', [])
        speaker_meta = {s.get('speaker'): s for s in segments if s.get('speaker_id')}
        pending, pending_stamp, pending_speaker = "", "00:00", "Говорящий не определён"

        def consume(sentence, stamp, speaker):
            sentence = sentence.strip()
            if not sentence:
                return
            sentences.append(sentence)
            kind = "Риск / Инцидент" if self.RISK.search(sentence) else "Решение" if self.DECISION.search(sentence) else None
            if kind:
                moments.append({"id": len(moments) + 1, "timestamp": stamp, "type": kind,
                                "title": sentence[:90], "description": sentence, "importance": "Высокая"})
            if self.ACTION.search(sentence):
                deadline = self.DEADLINE.search(sentence)
                assignee = self._assignee(sentence, speaker)
                tasks.append({
                    "id": len(tasks) + 1, "task": sentence, "assignee": assignee,
                    "deadline": deadline.group(0) if deadline else "Не указан",
                    "priority": "Высокий" if self.URGENT.search(sentence) else "Обычный",
                    "department": "Не указано", "status": "В работе", "source_quote": sentence,
                    "timestamp": stamp, "needs_review": True,
                })
                identity = speaker_meta.get(speaker) if assignee == speaker else None
                if identity:
                    tasks[-1].update(assignee_speaker_id=identity['speaker_id'],
                                     assignee_name_status=identity.get('name_status', 'unresolved'))

        for segment in segments:
            segment_text = str(segment.get("text") or "").strip()
            if not segment_text:
                continue
            start = max(0, float(segment.get("start") or 0))
            stamp = f"{int(start // 60):02d}:{int(start % 60):02d}"
            label = re.match(rf"^({self.NAME}):\s*(.+)", segment_text)
            speaker = str(segment.get("speaker") or (label.group(1) if label else "Говорящий не определён"))
            clean = label.group(2) if label else segment_text
            if speaker != "Говорящий не определён" and speaker not in names:
                names.append(speaker)
            dialogue.append({"speaker": speaker, "role": "", "timestamp": stamp, "text": clean,
                             'start': start, 'end': segment.get('end', start), 'speaker_id': segment.get('speaker_id'),
                             'speaker_name': segment.get('speaker_name'), 'name_status': segment.get('name_status', 'unresolved'),
                             'overlap': bool(segment.get('overlap'))})
            if pending and speaker != pending_speaker:
                consume(pending, pending_stamp, pending_speaker)
                pending = ""
            if not pending:
                pending_stamp, pending_speaker = stamp, speaker
            pending = (pending + " " + clean).strip()
            # Preserve dates such as 25.09.2026.
            pieces = re.split(r"(?<=[.!?;])\s+", pending)
            for sentence in pieces[:-1]:
                consume(sentence, pending_stamp, pending_speaker)
            pending = pieces[-1]
            if re.search(r"[.!?;]$", pending):
                consume(pending, pending_stamp, pending_speaker)
                pending = ""
        consume(pending, pending_stamp, pending_speaker)

        risks = [m["description"] for m in moments if m["type"] == "Риск / Инцидент"]
        decisions = [m["description"] for m in moments if m["type"] == "Решение"]
        # Extractive summary: actual utterances, never an invented conclusion.
        ranked = sorted(enumerate(sentences), key=lambda pair: (
            -int(bool(self.DECISION.search(pair[1]) or self.RISK.search(pair[1]) or self.ACTION.search(pair[1]))), pair[0]))
        selected = sorted(ranked[:6])
        company = os.getenv("DEFAULT_COMPANY", "Организация").strip() or "Организация"
        warnings = ["Черновик: проверьте распознавание, поручения, ответственных и сроки."]
        if transcript_data.get("diarization") != "performed":
            warnings.append("Автоматическая диаризация голосов не выполнена; имена берутся только из явных меток стенограммы.")
            if transcript_data.get('speaker_error'):
                warnings.append(transcript_data['speaker_error'])
        else:
            warnings.append('Голоса разделены локальной моделью. Имена «по контексту» — предположения; подтвердите их в карточках голосов. Похожие голоса и одновременная речь могут распознаваться неточно.')
        if not text and not dialogue:
            warnings.append("Речь не обнаружена. Проверьте звук и выбранный источник записи.")
        return {
            "title": "Протокол совещания", "company": company, "company_id": "main_org", "group_id": "general",
            "date": datetime.now().strftime("%d.%m.%Y"), "leader": "Не указан", "agenda": [],
            "participants": [{"name": name, "role": "Не указана", "department": ""} for name in names],
            "summary": [{"topic": "Ключевые фрагменты совещания", "key_points": [s for _, s in selected],
                         "risks": risks, "decisions": decisions}],
            "key_moments": moments,
            "executive_memo": {"target": f"Для руководства: {company}",
                               "urgent_actions": [t["task"] for t in tasks if t["priority"] == "Высокий"],
                               "key_metrics": [f"Найдено кандидатов в поручения: {len(tasks)}"], "risks_alert": risks},
            "dialogue": dialogue, "tasks": tasks, "transcript": text or " ".join(d["text"] for d in dialogue),
            "audio_filename": transcript_data.get("filename", ""), "audio_duration": transcript_data.get("duration", 0),
            "processed_with": "Локальные правила RU/KK", "processing_location": "local", "warnings": warnings,
            "diarization": transcript_data.get("diarization", "not_performed"),
            'speakers': profiles, 'speaker_turns': transcript_data.get('speaker_turns', []),
            'speaker_engine': transcript_data.get('speaker_engine', ''),
        }
