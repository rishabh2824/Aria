from fastapi import HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from google.oauth2 import id_token
from google.auth.transport.requests import Request

import fetch_survey as survey_parsing
from app import app, GOOGLE_CLIENT_ID, VALID_EMAILS
from survey_service import Survey


# Base models
class TokenRequest(BaseModel):
    token: str


class AuthResponse(BaseModel):
    success: bool
    message: str
    user: dict = None


class SurveyRequest(BaseModel):
    form_link: str = Field(...)
    num_responses: int = Field(..., ge=1, le=20)
    target_audience: str = Field(...)

class LlmPayloadRequest(BaseModel):
    form_link: str = Field(...)
    num_responses: int = Field(..., ge=1, le=20)
    target_audience: str = Field(...)
    model: str = Field("openai/gpt-4o")

class LogicDebugRequest(BaseModel):
    form_link: str = Field(...)
    qids: list[str] | None = None

class FlowDebugRequest(BaseModel):
    form_link: str = Field(...)

class QuestionDebugRequest(BaseModel):
    form_link: str = Field(...)
    qids: list[str]

class SurveyQuestionDebugRequest(BaseModel):
    form_link: str = Field(...)
    qids: list[str]

class DefinitionQuestionDebugRequest(BaseModel):
    form_link: str = Field(...)
    qids: list[str]

class DefinitionSummaryRequest(BaseModel):
    form_link: str = Field(...)


# API Endpoints
@app.get("/")
async def root():
    return {"message": "Sample response generator"}


# Returns the exact LLM payload JSON for a survey.
@app.post("/api/llm-payload")
async def fetch_llm_payload(request: LlmPayloadRequest):
    survey_id = survey_parsing.getSurveyId(request.form_link)
    return Survey.build_llm_payload(
        survey_id=survey_id,
        instructions=request.target_audience,
        n=request.num_responses,
        model=request.model
    )


@app.post("/api/debug/logic")
async def debug_logic(request: LogicDebugRequest):
    survey_id = survey_parsing.getSurveyId(request.form_link)
    definition = survey_parsing.getSurveyDefinition(survey_id)
    def_questions = survey_parsing.extractDefinitionQuestions(definition)

    logic_keys = [
        "DisplayLogic",
        "displayLogic",
        "DisplayLogicExpression",
        "displayLogicExpression",
        "DisplayLogicInput",
        "displayLogicInput",
    ]

    if request.qids:
        qids = request.qids
    else:
        qids = [
            qid for qid, payload in def_questions.items()
            if survey_parsing.findNestedValue(payload, logic_keys) is not None
        ]

    debug = {}
    for qid in qids:
        payload = def_questions.get(qid) or {}
        raw_logic = survey_parsing.findNestedValue(payload, logic_keys)
        debug[qid] = {
            "has_logic": raw_logic is not None,
            "raw_logic": raw_logic,
            "parsed_conditions": survey_parsing.parseBranchLogic(raw_logic) if raw_logic else None,
        }

    return {
        "survey_id": survey_id,
        "qids": qids,
        "logic": debug
    }


@app.post("/api/debug/flow")
async def debug_flow(request: FlowDebugRequest):
    survey_id = survey_parsing.getSurveyId(request.form_link)
    flow = survey_parsing.getSurveyFlow(survey_id)
    root = flow.get('result', flow)
    items = root.get('Flow', [])

    def flatten(items, out):
        if not isinstance(items, list):
            return
        for item in items:
            out.append(item)
            if 'Flow' in item:
                flatten(item.get('Flow'), out)

    all_items = []
    flatten(items, all_items)

    branches = []
    for item in all_items:
        if item.get('Type') != 'Branch':
            continue
        raw_logic = item.get('BranchLogic')
        branches.append({
            "flow_id": item.get('FlowID'),
            "description": item.get('Description'),
            "raw_logic": raw_logic,
            "parsed_conditions": survey_parsing.parseBranchLogic(raw_logic) if raw_logic else None
        })

    return {
        "survey_id": survey_id,
        "branches": branches
    }


@app.post("/api/debug/question-definition")
async def debug_question_definition(request: QuestionDebugRequest):
    survey_id = survey_parsing.getSurveyId(request.form_link)
    definition = survey_parsing.getSurveyDefinition(survey_id)
    def_questions = survey_parsing.extractDefinitionQuestions(definition)

    out = {}
    for qid in request.qids:
        out[qid] = def_questions.get(qid)

    return {
        "survey_id": survey_id,
        "questions": out
    }


@app.post("/api/debug/survey-questions")
async def debug_survey_questions(request: SurveyQuestionDebugRequest):
    survey_id = survey_parsing.getSurveyId(request.form_link)
    survey = survey_parsing.getSurvey(survey_id)
    questions = survey.get('result', {}).get('questions', {})

    out = {}
    for qid in request.qids:
        out[qid] = questions.get(qid)

    return {
        "survey_id": survey_id,
        "questions": out
    }


@app.post("/api/debug/definition-questions")
async def debug_definition_questions(request: DefinitionQuestionDebugRequest):
    survey_id = survey_parsing.getSurveyId(request.form_link)
    out = {}
    for qid in request.qids:
        out[qid] = survey_parsing.getSurveyQuestionDefinition(survey_id, qid)
    return {
        "survey_id": survey_id,
        "questions": out
    }


@app.post("/api/debug/definition-summary")
async def debug_definition_summary(request: DefinitionSummaryRequest):
    survey_id = survey_parsing.getSurveyId(request.form_link)
    definition = survey_parsing.getSurveyDefinition(survey_id)
    root = definition.get('result', definition)
    elements = root.get('SurveyElements', [])
    sq_ids = []
    sq_labels = {}
    for element in elements:
        if element.get('Element') != 'SQ':
            continue
        qid = element.get('PrimaryAttribute')
        if qid:
            sq_ids.append(qid)
            payload = element.get('Payload', {}) or {}
            label = payload.get('QuestionText') or payload.get('QuestionText', '')
            sq_labels[qid] = label
    return {
        "survey_id": survey_id,
        "sq_count": len(sq_ids),
        "sq_ids": sq_ids,
        "sq_labels": sq_labels
    }


# Auth
@app.post("/api/auth/google", response_model=AuthResponse)
async def google_auth(token_request: TokenRequest):
    try:
        # Verify the token
        idinfo = id_token.verify_oauth2_token(
            token_request.token,
            Request(),
            GOOGLE_CLIENT_ID
        )

        # Get user information from the token
        user_email = idinfo.get('email')
        user_id = idinfo.get('sub')
        user_name = idinfo.get('name')
        user_picture = idinfo.get('picture')

        # Check if email is in the whitelist
        if user_email not in VALID_EMAILS:
            raise HTTPException(status_code=403, detail="Invalid email")

        # Return user information
        return AuthResponse(
            success=True,
            message="Authentication successful",
            user={"id": user_id, "email": user_email, "name": user_name, "avatar": user_picture}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Authentication failed: {str(e)}")


# Returns LLM generated responses to surveys
@app.post("/api/responses")
async def returnResponses(request: SurveyRequest, format: str = Query("json", enum=["json", "csv"])):
    try:
        survey_id = survey_parsing.getSurveyId(request.form_link)
        result = Survey.generateResponses(
            survey_id=survey_id,
            instructions=request.target_audience,
            n=request.num_responses
        )

        if "error" in result:
            raise HTTPException(status_code=502, detail=result["error"])

        import_result = Survey.postResponsesToQualtrics(
            survey_id=survey_id,
            simulated_responses=result.get("simulated_responses", [])
        )

        if format == "json":
            return {
                **result,
                "qualtrics_import": import_result
            }

        csv_stream = Survey.jsonToCsv(result)
        return StreamingResponse(
            csv_stream,
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=ai_survey_responses.csv",
                "X-Qualtrics-Imported": str(import_result.get("imported", 0)),
                "X-Qualtrics-Errors": str(len(import_result.get("errors", []))),
            }
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error processing request: {type(e).__name__}: {str(e)}"
        )
