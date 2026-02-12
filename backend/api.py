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


# API Endpoints
@app.get("/")
async def root():
    return {"message": "Sample response generator"}


# Returns the cleaned Qualtrics Survey questions.
@app.get("/{survey_id}")
def fetch_survey(survey_id: str):
    return survey_parsing.cleanSurvey(survey_id)


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


@app.post("/api/responses/qualtrics")
async def returnResponsesToQualtrics(request: SurveyRequest):
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

        return {
            "generated": result,
            "qualtrics_import": import_result
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error processing request: {type(e).__name__}: {str(e)}"
        )
