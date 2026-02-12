import os
import re
from datetime import datetime, timezone

import requests
from fastapi import HTTPException

import fetch_survey as survey_parsing
from app import BASE_URL


class QualtricsImport:
    @staticmethod
    def normalizeText(value):
        return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', value.lower())).strip()

    @staticmethod
    def buildChoiceMap(questions):
        choice_map = {}
        multi_select_qids = set()
        slider_qids = set()
        required_qids = set()
        default_choice_by_qid = {}
        supported_qids = set()
        for qid, q_data in questions.items():
            q_type = q_data.get('questionType', {})
            if q_type.get('type') == 'MC' and q_type.get('selector') == 'MAVR':
                multi_select_qids.add(qid)
            if q_type.get('type') == 'Slider':
                slider_qids.add(qid)
            if q_data.get('validation', {}).get('doesForceResponse', False):
                required_qids.add(qid)
            q_type_code = q_type.get('type')
            if q_type_code in {"MC", "TE", "Slider"}:
                supported_qids.add(qid)

            choices = {}
            normalized = {}
            id_set = set()
            default_choice = None

            for choice_id, choice in q_data.get('choices', {}).items():
                text = survey_parsing.stripTags(choice.get('choiceText', '')).strip()
                if text:
                    choices[text] = choice_id
                    normalized[QualtricsImport.normalizeText(text)] = choice_id
                    if default_choice is None:
                        default_choice = choice_id
                id_set.add(str(choice_id))

            for answer_id, answer in q_data.get('answers', {}).items():
                text = survey_parsing.stripTags(answer.get('answerText', '')).strip()
                if text:
                    choices[text] = answer_id
                    normalized[QualtricsImport.normalizeText(text)] = answer_id
                    if default_choice is None:
                        default_choice = answer_id
                id_set.add(str(answer_id))

            if default_choice is not None:
                default_choice_by_qid[qid] = default_choice
            if choices:
                choice_map[qid] = {
                    "raw": choices,
                    "normalized": normalized,
                    "ids": id_set
                }

        return (
            choice_map,
            multi_select_qids,
            slider_qids,
            required_qids,
            default_choice_by_qid,
            supported_qids
        )

    @staticmethod
    def mapResponseValue(qid, value, choice_map, multi_select_qids, slider_qids):
        if value is None:
            return None

        if qid in slider_qids:
            return QualtricsImport.coerceNumeric(value)

        choices = choice_map.get(qid, {})
        if not choices:
            return QualtricsImport.coerceNumeric(value)
        raw_choices = choices.get("raw", {})
        norm_choices = choices.get("normalized", {})
        id_set = choices.get("ids", set())

        if isinstance(value, list):
            mapped = []
            for item in value:
                if isinstance(item, str):
                    key = survey_parsing.stripTags(item).strip()
                    found = raw_choices.get(key, norm_choices.get(QualtricsImport.normalizeText(key), None))
                    if found is None:
                        if key.isdigit() and key in id_set:
                            found = int(key)
                        else:
                            try:
                                if key.count(".") == 1 and str(float(key)) in id_set:
                                    found = float(key)
                            except Exception:
                                pass
                    mapped.append(found)
                else:
                    mapped.append(item)
            coerced = [QualtricsImport.coerceNumeric(v) for v in mapped if v is not None]
            if not coerced:
                return None
            if qid in multi_select_qids:
                return [str(v) for v in coerced]
            return ",".join(str(v) for v in coerced)

        if isinstance(value, str):
            key = survey_parsing.stripTags(value).strip()
            mapped = raw_choices.get(key, norm_choices.get(QualtricsImport.normalizeText(key), None))
            if mapped is None:
                if key.isdigit() and key in id_set:
                    mapped = int(key)
                try:
                    if key.count(".") == 1 and str(float(key)) in id_set:
                        mapped = float(key)
                except Exception:
                    pass
                if mapped is None:
                    return None
            mapped = QualtricsImport.coerceNumeric(mapped)
            if qid in multi_select_qids:
                return [str(mapped)]
            return mapped

        return QualtricsImport.coerceNumeric(value)

    @staticmethod
    def buildQualtricsValues(simulated_response, choice_map, multi_select_qids, slider_qids, required_qids, default_choice_by_qid, supported_qids):
        values = {}
        responses = simulated_response.get("responses", {})
        if not isinstance(responses, dict):
            return values

        for qid, answer in responses.items():
            if supported_qids and qid not in supported_qids:
                continue
            mapped = QualtricsImport.mapResponseValue(qid, answer, choice_map, multi_select_qids, slider_qids)
            if mapped is None:
                if qid in required_qids:
                    default_choice = default_choice_by_qid.get(qid)
                    if default_choice is not None:
                        mapped = [default_choice] if qid in multi_select_qids else default_choice
                    else:
                        continue
                else:
                    continue
            values[qid] = mapped

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        values.setdefault("startDate", now)
        values.setdefault("endDate", now)
        values.setdefault("finished", 1)
        values.setdefault("progress", 100)
        values.setdefault("duration", 60)
        values.setdefault("status", 0)
        values.setdefault("distributionChannel", "api")

        return values

    @staticmethod
    def coerceNumeric(value):
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int) or isinstance(value, float):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                return int(stripped)
            try:
                if stripped.count(".") == 1:
                    return float(stripped)
            except Exception:
                pass
        return value

    @staticmethod
    def postResponsesToQualtrics(survey_id: str, simulated_responses):
        api_token = os.getenv("API_TOKEN")
        if not api_token:
            raise HTTPException(status_code=500, detail="Qualtrics API token not set")

        survey = survey_parsing.getSurvey(survey_id)
        questions = survey.get('result', {}).get('questions', {})
        choice_map, multi_select_qids, slider_qids, required_qids, default_choice_by_qid, supported_qids = QualtricsImport.buildChoiceMap(questions)

        url = f"{BASE_URL}/surveys/{survey_id}/responses"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-TOKEN": api_token
        }

        imported = 0
        response_ids = []
        errors = []

        for idx, simulated in enumerate(simulated_responses):
            values = QualtricsImport.buildQualtricsValues(
                simulated,
                choice_map,
                multi_select_qids,
                slider_qids,
                required_qids,
                default_choice_by_qid,
                supported_qids
            )
            if not values:
                errors.append({"index": idx, "error": "No mappable responses"})
                continue

            try:
                response = requests.post(url, headers=headers, json={"values": values})
                if not response.ok:
                    try:
                        body = response.json()
                    except Exception:
                        body = response.text
                    errors.append({
                        "index": idx,
                        "error": f"{response.status_code} {response.reason}",
                        "response_body": body
                    })
                    continue

                result = response.json()
                meta = result.get("meta", {})
                meta_error = meta.get("error")
                if meta_error:
                    errors.append({
                        "index": idx,
                        "error": meta_error.get("errorMessage", "Qualtrics API error"),
                        "request_id": meta.get("requestId"),
                        "response_body": result
                    })
                    continue
                response_id = result.get("result", {}).get("responseId")
                if not response_id:
                    errors.append({
                        "index": idx,
                        "error": "Missing responseId in Qualtrics response",
                        "raw_result": result
                    })
                    continue
                response_ids.append(response_id)
                imported += 1
            except requests.exceptions.RequestException as e:
                errors.append({"index": idx, "error": str(e)})

        return {
            "imported": imported,
            "response_ids": response_ids,
            "errors": errors
        }
