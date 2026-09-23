import os
import re
import time
import json
from typing import Dict, List, Optional
from openai import OpenAI


class NLPExtractor:
    """
    Intelligent NLP module for:
    - Speaker Diarization (Who spoke when, role mapping)
    - Action Items / Tasks extraction (Who, What, Deadline, Priority, Department)
    - Key Moments & Critical Turning Points (Решения, Риски, Финансы, Срывы сроков)
    - Smart Executive Memos (Памятки для руководства и кураторов)
    - Company and Group attribution
    - Multi-language support (RU, KZ, Shala-Kazakh)
    - Cloud LLM (GPT-4o) + Local Astra + Offline Fallback
    """

    SYSTEM_PROMPT = """Ты — ведущий профессиональный секретарь и бизнес-аналитик совещаний.
Твоя задача — проанализировать стенограмму совещания и сформировать структурированный официальный протокол с фиксацией ВСЕХ поручений, КЛЮЧЕВЫХ МОМЕНТОВ и СЛУЖЕБНОЙ ПАМЯТКИ.
Совещание может проходить на русском, казахском или смешанном («шала-казахском») языке в любой компании или отрасли.

ТРЕБОВАНИЯ:
1. ДИАРИЗАЦИЯ И УЧАСТНИКИ:
   - Определи участников совещания, их ФИО и должности строго по контексту диалога (не выдумывай несуществующие данные).
   - Сформируй связный диалог (реплики с таймкодами, ФИО спикера, ролью и текстом).

2. ПОРУЧЕНИЯ (ACTION ITEMS):
   - Зафиксируй КАЖДОЕ поручение руководства без пропусков.
   - Укажи: id, task (четкий инфинитив: "Разработать...", "Подготовить..."), assignee (ФИО и должность, если названы), deadline, priority ("Высокий"/"Средний"/"Обычный"), department, status ("В работе"), source_quote (прямая цитата).

3. КЛЮЧЕВЫЕ МОМЕНТЫ (KEY MOMENTS):
   - Выдели поворотные события с таймкодами:
     * "Решение": утвержденные управленческие решения;
     * "Риск / Инцидент": аварии, проверки, сбои, правовые риски;
     * "Финансы / Бюджет": освоение процентов, сметы, штрафные санкции, бюджет;
     * "Срыв сроков": задержки поставок, неготовность документов;
   - Для каждого укажи: timestamp ("01:15"), type, title, description, importance ("Критическая"/"Высокая"/"Средняя").

4. ПАМЯТКА ДЛЯ РУКОВОДСТВА (EXECUTIVE MEMO):
   - Сформируй краткую служебную записку:
     * urgent_actions: что требует немедленного контроля в ближайшие 48 часов;
     * key_metrics: важнейшие озвученные цифры (проценты, суммы, сроки);
     * risks_alert: критические угрозы (остановка производства, срыв контрактов, кассовые разрывы).

5. ОРГАНИЗАЦИЯ:
   - Определи организацию из контекста диалога; если не названа явно, укажи "Организация".

ВЕРНИ ОТВЕТ ТОЛЬКО В ФОРМАТЕ JSON следующего вида:
{
  "title": "Тема совещания",
  "company_id": "main_org",
  "company": "Название организации",
  "group_id": "general",
  "date": "Дата или период",
  "leader": "ФИО председателя",
  "agenda": ["Пункт 1", "Пункт 2"],
  "participants": [
    {"name": "ФИО", "role": "Должность", "department": "Департамент"}
  ],
  "summary": [
    {
      "topic": "Название блока / темы",
      "key_points": ["тезис 1 с цифрами", "тезис 2"],
      "risks": ["риск 1"],
      "decisions": ["решение 1"]
    }
  ],
  "key_moments": [
    {
      "id": 1,
      "timestamp": "01:20",
      "type": "Решение",
      "title": "Краткий заголовок момента",
      "description": "Что произошло или было решено",
      "importance": "Высокая"
    }
  ],
  "executive_memo": {
    "target": "Для руководства правления АО «Самрук-Қазына Өңдеу»",
    "urgent_actions": ["действие 1", "действие 2"],
    "key_metrics": ["метрика 1", "метрика 2"],
    "risks_alert": ["угроза 1"]
  },
  "dialogue": [
    {
      "speaker": "ФИО спикера",
      "role": "Должность",
      "timestamp": "00:15",
      "text": "Текст реплики"
    }
  ],
  "tasks": [
    {
      "id": 1,
      "task": "Суть поручения",
      "assignee": "ФИО ответственного",
      "deadline": "Срок исполнения",
      "priority": "Высокий",
      "department": "Департамент",
      "status": "В работе",
      "source_quote": "Цитата из стенограммы"
    }
  ]
}
"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if self.api_key:
            self.client = OpenAI(api_key=self.api_key)
        else:
            self.client = None

    def set_api_key(self, api_key: str):
        self.api_key = api_key
        self.client = OpenAI(api_key=api_key)

    def process_transcript(self, transcript_data: Dict, model: str = "gpt-4o") -> Dict:
        """
        Process speech transcript data into a complete structured meeting protocol
        including Key Moments, Action Items, and Executive Memo.
        """
        text = transcript_data.get("text", "")
        segments = transcript_data.get("segments", [])

        # Format transcript with timestamps
        formatted_segments = []
        for s in segments:
            start_m = int(s.get("start", 0) // 60)
            start_s = int(s.get("start", 0) % 60)
            formatted_segments.append(f"[{start_m:02d}:{start_s:02d}] {s.get('text', '')}")
        segmented_text = "\n".join(formatted_segments) if formatted_segments else text

        # 1. Offline Mode: strictly local rule-based engine without any network requests
        if model == "offline":
            print("[NLP] Running in completely Offline (On-Premise) mode...")
            res = self._offline_fallback_extractor(transcript_data)
            res["processed_with"] = "Offline Rule Engine (Air-Gapped)"
            return res

        # 2. Codex Astra Mode: try local Codex CLI first
        if model == "codex-astra":
            print("[NLP] Running via Codex Astra local agent...")
            codex_res = self._extract_via_codex_cli(segmented_text)
            if codex_res:
                codex_res["audio_filename"] = transcript_data.get("filename", "")
                codex_res["audio_duration"] = transcript_data.get("duration", 0)
                codex_res["processed_with"] = "Codex Astra (Local Agent)"
                return codex_res
            print("[NLP] Codex Astra CLI call fallback to offline parser...")
            res = self._offline_fallback_extractor(transcript_data)
            res["processed_with"] = "Offline Rule Engine (Astra Fallback)"
            return res

        # 3. Cloud LLM Mode (GPT-4o)
        if self.client:
            try:
                print(f"[NLP] Extracting tasks, key moments and memos via {model}...")
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": f"Стенограмма совещания с таймкодами:\n\n{segmented_text}"
                        }
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.2
                )
                raw_json = response.choices[0].message.content
                result = json.loads(raw_json)

                # Ensure required fields exist
                default_comp = os.getenv("DEFAULT_COMPANY", "Организация")
                result.setdefault("company", default_comp)
                result.setdefault("company_id", "main_org")
                result.setdefault("title", "Оперативное совещание")
                result.setdefault("tasks", [])
                result.setdefault("key_moments", [])
                result.setdefault("summary", [])
                result.setdefault("participants", [])
                result.setdefault("dialogue", [])
                result.setdefault("executive_memo", {
                    "target": f"Руководство {default_comp}",
                    "urgent_actions": ["Контроль исполнения зафиксированных поручений"],
                    "key_metrics": ["Соблюдение сроков"],
                    "risks_alert": ["Риск срыва операционного плана"]
                })

                result["audio_filename"] = transcript_data.get("filename", "")
                result["audio_duration"] = transcript_data.get("duration", 0)
                result["processed_with"] = model

                return result
            except Exception as e:
                print(f"[NLP] Error in LLM extraction ({e}), falling back to offline parser...")

        return self._offline_fallback_extractor(transcript_data)

    def _extract_via_codex_cli(self, segmented_text: str) -> Optional[Dict]:
        """Runs local Codex CLI to extract protocol JSON without cloud API."""
        import subprocess
        codex_path = r"C:\Users\New\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe"
        if not os.path.exists(codex_path):
            return None
        prompt = (
            f"{self.SYSTEM_PROMPT}\n\n"
            f"Стенограмма:\n{segmented_text[:4000]}\n\n"
            f"Ответь ТОЛЬКО валидным JSON-объектом."
        )
        try:
            cmd = [codex_path, "exec", "--skip-git-repo-check", "--ephemeral", prompt]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=40, stdin=subprocess.DEVNULL)
            if proc.returncode == 0 and proc.stdout:
                # Find JSON block in output
                match = re.search(r"\{.*\}", proc.stdout, re.DOTALL)
                if match:
                    return json.loads(match.group(0))
        except Exception as e:
            print(f"[NLP] Codex CLI error: {e}")
        return None

    def _offline_fallback_extractor(self, transcript_data: Dict) -> Dict:
        """Deterministic offline extractor for air-gapped / local mode without internet."""
        text = transcript_data.get("text", "")
        segments = transcript_data.get("segments", [])

        tasks = []
        key_moments = []
        dialogue = []
        task_id = 1
        moment_id = 1

        assignee_pattern = re.compile(
            r"(?:поручаю|ответственн(?:ый|ая)|назначить|прошу)\s+([А-ЯЁІҢҒҮҰҚӨҺ][а-яёіңғүұқөһ]+(?:\s+[А-ЯЁІҢҒҮҰҚӨҺ]\.?)?)",
            re.IGNORECASE
        )
        deadline_pattern = re.compile(
            r"(?:до|к|в срок до)\s+(\d{1,2}(?:\s+[а-яёіңғүұқөһ]+|\.\d{2}(?:\.\d{2,4})?)|конца\s+(?:недели|месяца|квартала)|понедельника|пятницы)",
            re.IGNORECASE
        )

        known_speakers = set()

        for s in segments:
            t = s.get("text", "").strip()
            start_m = int(s.get("start", 0) // 60)
            start_s = int(s.get("start", 0) % 60)
            time_str = f"{start_m:02d}:{start_s:02d}"

            # Detect speaker if labeled or contextual
            speaker_match = re.match(r"^([А-ЯЁІҢҒҮҰҚӨҺ][а-яёіңғүұқөһ]+(?:\s+[А-ЯЁІҢҒҮҰҚӨҺ]\.?)?):\s*(.*)", t)
            if speaker_match:
                spk = speaker_match.group(1)
                text_clean = speaker_match.group(2)
                known_speakers.add(spk)
            else:
                spk = "Участник совещания"
                text_clean = t

            dialogue.append({
                "speaker": spk,
                "role": "Спикер",
                "timestamp": time_str,
                "text": text_clean
            })

            # Detect key moments
            if any(k in t.lower() for k in ["проблема", "риск", "инцидент", "срыв", "задержк", "штраф", "авари"]):
                key_moments.append({
                    "id": moment_id,
                    "timestamp": time_str,
                    "type": "Риск / Инцидент",
                    "title": text_clean[:60] + ("..." if len(text_clean) > 60 else ""),
                    "description": text_clean,
                    "importance": "Высокая"
                })
                moment_id += 1
            elif any(k in t.lower() for k in ["решили", "договорились", "зафиксируем", "утверждаю", "принято решение"]):
                key_moments.append({
                    "id": moment_id,
                    "timestamp": time_str,
                    "type": "Решение",
                    "title": text_clean[:60] + ("..." if len(text_clean) > 60 else ""),
                    "description": text_clean,
                    "importance": "Высокая"
                })
                moment_id += 1

            # Detect tasks with smart regex
            if any(k in t.lower() for k in ["поручение", "срок", "ответственный", "подготовить", "разработать", "обеспечить", "доложить", "смета", "предоставить"]):
                # Extract assignee
                asm = assignee_pattern.search(t)
                assignee = asm.group(1) if asm else "Ответственный исполнитель"

                # Extract deadline
                dlm = deadline_pattern.search(t)
                deadline = dlm.group(1) if dlm else "По графику"

                priority = "Высокий" if any(w in t.lower() for w in ["срочно", "немедленно", "критичн", "строго"]) else "Средний"

                tasks.append({
                    "id": task_id,
                    "task": text_clean,
                    "assignee": assignee,
                    "deadline": deadline,
                    "priority": priority,
                    "department": "Профильное подразделение",
                    "status": "В работе",
                    "source_quote": t
                })
                task_id += 1

        participants = [{"name": spk, "role": "Участник совещания", "department": "Организация"} for spk in known_speakers]
        if not participants:
            participants = [{"name": "Участники совещания", "role": "Руководители направлений", "department": "Организация"}]

        comp_name = os.getenv("DEFAULT_COMPANY", "Организация")
        cur_date = time.strftime("%d.%m.%Y")

        return {
            "title": "Протокол совещания",
            "company": comp_name,
            "company_id": "main_org",
            "date": cur_date,
            "leader": participants[0]["name"] if participants else "Председатель совещания",
            "agenda": ["Обсуждение ключевых производственных и операционных вопросов"],
            "participants": participants,
            "summary": [
                {
                    "topic": "Итоги совещания",
                    "key_points": ["Зафиксированы доклады участников."],
                    "risks": ["Необходим оперативный контроль поручений."],
                    "decisions": ["Поручения приняты к исполнению."]
                }
            ],
            "key_moments": key_moments,
            "executive_memo": {
                "target": f"Для руководства {comp_name}",
                "urgent_actions": ["Контроль исполнения первоочередных поручений по графикам"],
                "key_metrics": [f"Зафиксировано {len(tasks)} поручений"],
                "risks_alert": ["Контроль соблюдения согласованных сроков"]
            },
            "dialogue": dialogue,
            "tasks": tasks,
            "audio_filename": transcript_data.get("filename", ""),
            "audio_duration": transcript_data.get("duration", 0),
            "processed_with": "Offline Rule-based Engine"
        }
