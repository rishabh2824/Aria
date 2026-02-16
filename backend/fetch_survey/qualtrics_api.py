import os
import re
import requests
from fastapi import HTTPException


def _get_base_url():
    datacenter = os.getenv("DATACENTER_ID")
    return f"https://{datacenter}.qualtrics.com/API/v3"


# Extract the survey id from the link
def getSurveyId(form_link: str) -> str:
    match = re.search(r'(SV_[A-Za-z0-9]+)', form_link or '')
    if match:
        return match.group(1)
    raise ValueError("Invalid Qualtrics survey link or ID")


# Fetch the Survey from Qualtrics
def getSurvey(survey_id: str):
    api_token = os.getenv("API_TOKEN")
    request_url = f"{_get_base_url()}/surveys/{survey_id}"
    headers = {"Content-Type": "application/json", "X-API-TOKEN": api_token}

    try:
        # Make the request
        response = requests.get(request_url, headers=headers)

        # Return the JSON payload
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Fetch the Survey Definition (includes display logic) from Qualtrics
def getSurveyDefinition(survey_id: str):
    api_token = os.getenv("API_TOKEN")
    request_url = f"{_get_base_url()}/survey-definitions/{survey_id}"
    headers = {"Content-Type": "application/json", "X-API-TOKEN": api_token}

    try:
        response = requests.get(request_url, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Fetch a single question definition (includes display logic) from Qualtrics
def getSurveyQuestionDefinition(survey_id: str, question_id: str):
    api_token = os.getenv("API_TOKEN")
    request_url = f"{_get_base_url()}/survey-definitions/{survey_id}/questions/{question_id}"
    headers = {"Content-Type": "application/json", "X-API-TOKEN": api_token}

    try:
        response = requests.get(request_url, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Extract the survey definition questions
def extractDefinitionQuestions(definition):
    root = definition.get('result', definition)
    elements = root.get('SurveyElements', [])
    question_map = {}
    if not isinstance(elements, list):
        return question_map
    for element in elements:
        if element.get('Element') != 'SQ':
            continue
        payload = element.get('Payload', {}) or {}
        qid = payload.get('QuestionID') or element.get('PrimaryAttribute')
        if qid:
            question_map[qid] = payload
    return question_map


# Fetch the survey flow from Qualtrics
def getSurveyFlow(survey_id: str):
    api_token = os.getenv("API_TOKEN")
    request_url = f"{_get_base_url()}/survey-definitions/{survey_id}/flow"
    headers = {"Content-Type": "application/json", "X-API-TOKEN": api_token}

    try:
        # Make the request
        response = requests.get(request_url, headers=headers)

        # Raise an error if the status code is 4xx or 5xx
        response.raise_for_status()

        # Return the JSON payload
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
