import json, requests, re, os, csv, io, random
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query
from dotenv import load_dotenv
from markdownify import markdownify as md
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from google.oauth2 import id_token
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials


app = FastAPI()
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=ENV_PATH, override=True)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Qualtrics/LLM Connection
datacenter = os.getenv("DATACENTER_ID")
BASE_URL = f"https://{datacenter}.qualtrics.com/API/v3"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY") or os.getenv("API_KEY")


# Sign in with Google
SCOPES = ['https://www.googleapis.com/auth/forms.body.readonly']
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
VALID_EMAILS = ["rvjain@wisc.edu", "cho275@wisc.edu", "ekim298@wisc.edu", "mli936@wisc.edu", "hliu787@wisc.edu",
                "rpshah3@wisc.edu", "ksong65@wisc.edu", "jtong9@wisc.edu", "mzeng27@wisc.edu"]


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
async def root(): return {"message": "Sample response generator"}

# Returns the cleaned Qualtrics Survey questions.
@app.get("/{survey_id}")
def fetch_survey(survey_id: str):
    return Survey.cleanSurvey(survey_id)

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
        if user_email not in VALID_EMAILS: raise HTTPException(status_code=403, detail="Invalid email")

        # Return user information
        return AuthResponse(
            success=True,
            message="Authentication successful",
            user={"id": user_id, "email": user_email, "name": user_name, "avatar": user_picture}
        )
    except Exception as e: raise HTTPException(status_code=500, detail=f"Authentication failed: {str(e)}")

# Returns LLM generated responses to surveys
@app.post("/api/responses")
async def returnResponses(request: SurveyRequest, format: str = Query("json", enum=["json", "csv"])):
    try:
        survey_id = Survey.getSurveyId(request.form_link)
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
        survey_id = Survey.getSurveyId(request.form_link)
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

# Service Methods
class Survey:
    @staticmethod
    def getSurveyId(form_link: str) -> str:
        match = re.search(r'(SV_[A-Za-z0-9]+)', form_link or '')
        if match:
            return match.group(1)
        raise ValueError("Invalid Qualtrics survey link or ID")

    # Get valid user credentials from environment variables or OAuth flow.
    @staticmethod
    def get_credentials():
        token_file = "token.json"

        if not os.path.exists(token_file):
            raise HTTPException(status_code=500, detail="Google OAuth token.json not found")

        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

        if creds.expired and creds.refresh_token:
            try:
                print("Expired:", creds.expired)
                print("Has refresh token:", bool(creds.refresh_token))
                print("Scopes:", creds.scopes)
                creds.refresh(Request())
                with open(token_file, "w") as token:
                    token.write(creds.to_json())
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to refresh Google credentials: {str(e)}")

        return creds

    # Fetch the Survey from Qualtrics
    @staticmethod
    def getSurvey(survey_id: str):
        api_token = os.getenv("API_TOKEN")
        request_url = f"{BASE_URL}/surveys/{survey_id}"
        headers = {"Content-Type": "application/json", "X-API-TOKEN": api_token}

        try:
            # Make the request
            response = requests.get(request_url, headers=headers)

            # Return the JSON payload
            return response.json()
        except Exception as e: raise HTTPException(status_code=500, detail=str(e))

    # Fetch the Survey Definition (includes display logic) from Qualtrics
    @staticmethod
    def getSurveyDefinition(survey_id: str):
        api_token = os.getenv("API_TOKEN")
        request_url = f"{BASE_URL}/survey-definitions/{survey_id}"
        headers = {"Content-Type": "application/json", "X-API-TOKEN": api_token}

        try:
            response = requests.get(request_url, headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # Fetch a single question definition (includes display logic) from Qualtrics
    @staticmethod
    def getSurveyQuestionDefinition(survey_id: str, question_id: str):
        api_token = os.getenv("API_TOKEN")
        request_url = f"{BASE_URL}/survey-definitions/{survey_id}/questions/{question_id}"
        headers = {"Content-Type": "application/json", "X-API-TOKEN": api_token}

        try:
            response = requests.get(request_url, headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @staticmethod
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
    @staticmethod
    def getSurveyFlow(survey_id: str):
        api_token = os.getenv("API_TOKEN")
        request_url = f"{BASE_URL}/survey-definitions/{survey_id}/flow"
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

    # Convert question type codes to readable strings
    @staticmethod
    def formatQuestionTypes(q_type):
        type_map = {
            'MC': {'SAVR': 'Single Select', 'MAVR': 'Multi Select', 'default': 'Multiple Choice'},
            'TE': {'SL': 'Text Input', 'ML': 'Text Area', 'FORM': 'Form', 'default': 'Text Entry'},
            'DB': 'Display Text',
            'Matrix': 'Matrix',
            'RO': 'Rank Order',
            'Slider': 'Slider',
            'CS': 'Constant Sum'
        }

        q_type_code = q_type.get('type', '')
        selector = q_type.get('selector', '')

        if q_type_code in type_map:
            type_def = type_map[q_type_code]
            if isinstance(type_def, dict):
                return type_def.get(selector, type_def['default'])
            return type_def

        return 'Unknown'

    # Strip HTML Tags
    @staticmethod
    def stripTags(html):
        if not html: return ''
        return re.sub(r'<[^>]*>', '', html).strip()

    # Handle the branching logic
    @staticmethod
    def parseBranchLogic(branch_logic):
        if not branch_logic or not isinstance(branch_logic, (dict, list)):
            return None

        def parse_if(expr):
            if not expr or not isinstance(expr, dict):
                return None

            question_id = (
                expr.get('QuestionID')
                or Survey.findNestedValue(expr, ['QuestionID', 'QuestionId', 'questionID', 'questionId'])
            )
            operator = expr.get('Operator') or Survey.findNestedValue(expr, ['Operator', 'operator'])
            choice_locator = (
                expr.get('ChoiceLocator')
                or Survey.findNestedValue(expr, ['ChoiceLocator', 'ChoiceLocatorId', 'choiceLocator', 'choiceLocatorId'])
            )
            if not choice_locator:
                choice_id = Survey.findNestedValue(
                    expr,
                    ['ChoiceID', 'ChoiceId', 'choiceID', 'choiceId', 'SelectedChoice', 'SelectedChoiceID', 'SelectableChoiceID']
                )
                if choice_id is not None:
                    choice_locator = str(choice_id)

            description = expr.get('Description') or Survey.findNestedValue(expr, ['Description', 'description']) or ''
            if description:
                description = Survey.stripTags(description)

            if operator is None and choice_locator is not None:
                operator = "Selected"

            if not question_id or not operator:
                return None

            if not isinstance(question_id, str):
                question_id = str(question_id)
            if not isinstance(operator, str):
                operator = str(operator)
            if choice_locator is not None and not isinstance(choice_locator, str):
                choice_locator = str(choice_locator)

            raw = dict(expr)
            if question_id and 'QuestionID' not in raw:
                raw['QuestionID'] = question_id
            if operator and 'Operator' not in raw:
                raw['Operator'] = operator
            if choice_locator and 'ChoiceLocator' not in raw:
                raw['ChoiceLocator'] = choice_locator
            if description and 'Description' not in raw:
                raw['Description'] = description

            return {
                'question_id': question_id,
                'choice_locator': choice_locator,
                'operator': operator,
                'description': description,
                'raw': raw
            }

        def parse_node(expr):
            if not expr:
                return None
            if isinstance(expr, list):
                items = [parse_node(item) for item in expr]
                items = [item for item in items if item]
                if not items:
                    return None
                if len(items) == 1:
                    return items[0]
                return {'op': 'and', 'conditions': items}
            if not isinstance(expr, dict):
                return None

            expr_type = expr.get('Type') or expr.get('LogicType')
            if expr_type == 'If' or Survey.findNestedValue(expr, ['Operator', 'operator']) is not None:
                inner = expr.get('0', expr)
                return parse_if(inner)

            if expr_type in {'BooleanExpression', 'And', 'Or'} or 'Conjunction' in expr:
                conj = (expr.get('Conjunction') or '').lower()
                op = 'or' if (expr_type or '').lower() == 'or' or conj == 'or' else 'and'
                items = []
                for key, val in expr.items():
                    if key in {'Type', 'LogicType', 'Conjunction'}:
                        continue
                    node = parse_node(val)
                    if node:
                        items.append(node)
                if not items:
                    return None
                if len(items) == 1:
                    return items[0]
                return {'op': op, 'conditions': items}

            items = []
            for val in expr.values():
                node = parse_node(val)
                if node:
                    items.append(node)
            if not items:
                return None
            if len(items) == 1:
                return items[0]
            return {'op': 'and', 'conditions': items}

        return parse_node(branch_logic)

    @staticmethod
    def combineConditions(parent_cond, child_cond):
        parent_cond = Survey.normalizeConditions(parent_cond)
        child_cond = Survey.normalizeConditions(child_cond)
        if not parent_cond and not child_cond:
            return None
        if not parent_cond:
            return child_cond
        if not child_cond:
            return parent_cond
        return {'op': 'and', 'conditions': [parent_cond, child_cond]}

    # Clean the survey flow
    @staticmethod
    def buildSurveyFlow(flow_data):
        flow_structure = []
        seq = 0

        root = flow_data.get('result', flow_data)

        def traverse(items, parent_cond=None, parent_randomizer=None):
            nonlocal seq
            if not isinstance(items, list): return

            for item in items:
                seq += 1
                t = item.get('Type')

                if t in ['Block', 'Standard']:
                    flow_structure.append({
                        'sequence': seq,
                        'type': 'block',
                        'block_id': item.get('ID'),
                        'flow_id': item.get('FlowID'),
                        'condition': parent_cond,
                        'randomizer_id': parent_randomizer.get('randomizer_id') if parent_randomizer else None,
                        'randomizer_subset_size': parent_randomizer.get('subset_size') if parent_randomizer else None,
                        'randomizer_even_presentation': parent_randomizer.get('even_presentation') if parent_randomizer else None
                    })

                elif t == 'Branch':
                    conditions = Survey.parseBranchLogic(item.get('BranchLogic', {}))
                    combined = Survey.combineConditions(parent_cond, conditions)
                    flow_structure.append({
                        'sequence': seq,
                        'type': 'branch',
                        'flow_id': item.get('FlowID'),
                        'description': item.get('Description', ''),
                        'conditions': conditions,
                        'parent_condition': parent_cond
                    })
                    if 'Flow' in item: traverse(item['Flow'], combined, parent_randomizer)

                elif t == 'BlockRandomizer':
                    rand_id = item.get('FlowID') or f"RAND_{seq}"
                    rand_info = {
                        'randomizer_id': rand_id,
                        'subset_size': item.get('SubSet'),
                        'even_presentation': item.get('EvenPresentation', False)
                    }
                    flow_structure.append({
                        'sequence': seq,
                        'type': 'randomizer',
                        'flow_id': rand_id,
                        'subset_size': rand_info['subset_size'],
                        'even_presentation': rand_info['even_presentation'],
                        'condition': parent_cond
                    })
                    if 'Flow' in item:
                        traverse(item['Flow'], parent_cond, rand_info)

                elif t == 'EndSurvey':
                    flow_structure.append({
                        'sequence': seq,
                        'type': 'end_survey',
                        'flow_id': item.get('FlowID'),
                        'ending_type': item.get('EndingType', ''),
                        'condition': parent_cond
                    })

        if 'Flow' in root: traverse(root['Flow'])
        return flow_structure

    @staticmethod
    def mapQuestions(blocks):
        mapping = {}
        for block_id, block_data in blocks.items():
            # API uses 'elements', but we check 'BlockElements' as a fallback
            elements = block_data.get("elements", block_data.get("BlockElements", []))

            for elem in elements:
                # API uses 'type' (camelCase), QSF uses 'Type'
                e_type = elem.get("type", elem.get("Type"))

                if e_type == "Question":
                    # API uses 'questionId', QSF uses 'QuestionID'
                    qid = elem.get("questionId", elem.get("QuestionID"))
                    if qid: mapping[qid] = block_id
        return mapping

    @staticmethod
    def mergeFlow(cleaned_questions, question_to_block, flow_structure):
        block_flow = {
            f['block_id']: {
                'sequence': f['sequence'],
                'condition': f['condition'],
                'randomizer_id': f.get('randomizer_id'),
                'randomizer_subset_size': f.get('randomizer_subset_size'),
                'randomizer_even_presentation': f.get('randomizer_even_presentation')
            }
            for f in flow_structure if f['type'] == 'block'
        }

        for qid, q in cleaned_questions.items():
            bid = question_to_block.get(qid)
            if bid and bid in block_flow:
                info = block_flow[bid]
                q['flow_info'] = {
                    'block_id': bid,
                    'sequence': info['sequence'],
                    'conditional': bool(info['condition']),
                    'conditions': info['condition'],
                    'randomizer_id': info.get('randomizer_id'),
                    'randomizer_subset_size': info.get('randomizer_subset_size'),
                    'randomizer_even_presentation': info.get('randomizer_even_presentation')
                }
            else:
                q['flow_info'] = None

        return cleaned_questions

    @staticmethod
    def numberValidation(validation):
        settings = validation.get('settings', {})
        rules = {}
        if 'minimum' in settings: rules['min'] = settings['minimum']
        if 'maximum' in settings: rules['max'] = settings['maximum']
        if 'maxDecimals' in settings: rules['decimals'] = settings['maxDecimals']
        return rules

    # Prepares the API to send to LLM
    @staticmethod
    def cleanSurvey(survey_id: str):
        survey = Survey.getSurvey(survey_id)
        flow = Survey.getSurveyFlow(survey_id)

        questions = survey['result']['questions']
        blocks = survey['result']['blocks']

        SKIP_QUESTION_TYPES = ['Timing']
        cleaned_questions = {}

        # Clean & Normalize Questions
        for qid, q_data in questions.items():
            q_type = q_data.get('questionType', {})
            type_str = q_type.get('type', '')

            if type_str in SKIP_QUESTION_TYPES: continue

            raw_text = q_data.get('questionText', '').strip()
            if not raw_text or raw_text == 'Timing': continue

            # Convert HTML to MD
            text_structure = md(raw_text, heading_style="ATX")

            # Normalize whitespace
            text_cleaned = re.sub(r'\n{3,}', '\n\n', text_structure).strip()

            cleaned_q = {
                'name': q_data.get('questionName'),
                'type': Survey.formatQuestionTypes(q_type),
                'text': text_cleaned
            }

            # Display-only questions
            if cleaned_q['type'] == 'Display Text': cleaned_q['display_only'] = True

            validation = q_data.get('validation', {})
            if validation.get('doesForceResponse', False): cleaned_q['required'] = True

            # Number validation
            if validation.get('type') == 'ValidNumber':
                cleaned_q['validation'] = Survey.numberValidation(validation)

            # Choices
            if 'choices' in q_data:
                cleaned_q['choices'] = [
                    choice.get('choiceText', choice.get('description', ''))
                    for choice in q_data['choices'].values()
                ]

            cleaned_questions[qid] = cleaned_q

        # Flow metadata
        flow_structure = Survey.buildSurveyFlow(flow)
        question_to_block = Survey.mapQuestions(blocks)
        enriched_questions = Survey.mergeFlow(cleaned_questions, question_to_block, flow_structure)

        return {
            'metadata': {
                'survey_id': survey_id,
                'total_questions': len(enriched_questions),
                'has_conditional_logic': any(f['type'] == 'branch' for f in flow_structure),
                'has_randomization': any(f['type'] == 'randomizer' for f in flow_structure)
            },
            'questions': enriched_questions,
            'flow_structure': flow_structure
        }

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
                text = Survey.stripTags(choice.get('choiceText', '')).strip()
                if text:
                    choices[text] = choice_id
                    normalized[Survey.normalizeText(text)] = choice_id
                    if default_choice is None:
                        default_choice = choice_id
                id_set.add(str(choice_id))

            for answer_id, answer in q_data.get('answers', {}).items():
                text = Survey.stripTags(answer.get('answerText', '')).strip()
                if text:
                    choices[text] = answer_id
                    normalized[Survey.normalizeText(text)] = answer_id
                    if default_choice is None:
                        default_choice = answer_id
                id_set.add(str(answer_id))

            if default_choice is not None:
                default_choice_by_qid[qid] = default_choice
            if choices:
                choice_map[qid] = {
                    "raw": choices,
                    "normalized": normalized
                    ,
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
            return Survey.coerceNumeric(value)

        choices = choice_map.get(qid, {})
        if not choices:
            return Survey.coerceNumeric(value)
        raw_choices = choices.get("raw", {})
        norm_choices = choices.get("normalized", {})
        id_set = choices.get("ids", set())

        if isinstance(value, list):
            mapped = []
            for item in value:
                if isinstance(item, str):
                    key = Survey.stripTags(item).strip()
                    found = raw_choices.get(key, norm_choices.get(Survey.normalizeText(key), None))
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
            coerced = [Survey.coerceNumeric(v) for v in mapped if v is not None]
            if not coerced:
                return None
            if qid in multi_select_qids:
                return [str(v) for v in coerced]
            return ",".join(str(v) for v in coerced)

        if isinstance(value, str):
            key = Survey.stripTags(value).strip()
            mapped = raw_choices.get(key, norm_choices.get(Survey.normalizeText(key), None))
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
            mapped = Survey.coerceNumeric(mapped)
            if qid in multi_select_qids:
                return [str(mapped)]
            return mapped

        return Survey.coerceNumeric(value)

    @staticmethod
    def buildQualtricsValues(
        simulated_response,
        choice_map,
        multi_select_qids,
        slider_qids,
        required_qids,
        default_choice_by_qid,
        supported_qids
    ):
        values = {}
        responses = simulated_response.get("responses", {})
        if not isinstance(responses, dict):
            return values

        for qid, answer in responses.items():
            if supported_qids and qid not in supported_qids:
                continue
            mapped = Survey.mapResponseValue(qid, answer, choice_map, multi_select_qids, slider_qids)
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
    def normalizeText(value):
        return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', value.lower())).strip()

    @staticmethod
    def findNestedValue(value, keys):
        if isinstance(value, dict):
            for key in keys:
                if key in value:
                    found = value.get(key)
                    if found is not None and found != {}:
                        return found
            for inner in value.values():
                found = Survey.findNestedValue(inner, keys)
                if found is not None:
                    return found
        elif isinstance(value, (list, tuple, set)):
            for inner in value:
                found = Survey.findNestedValue(inner, keys)
                if found is not None:
                    return found
        return None

    @staticmethod
    def normalizeOperator(value):
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        value = re.sub(r'[^a-z]+', '', value.lower())
        if value in {"equalto", "equals"}:
            return "equal"
        if value in {"notequalto", "notequals", "doesnotequal"}:
            return "notequal"
        if value in {"selectedchoice", "selectedchoices", "isselected", "isselected"}:
            return "selected"
        if value in {"notselectedchoice", "notselectedchoices", "isnotselected", "isnotselectedchoice"}:
            return "notselected"
        if value in {"isempty", "isblank"}:
            return "empty"
        if value in {"isnotempty", "isnotblank"}:
            return "notempty"
        if value in {"isanswered"}:
            return "answered"
        if value in {"isnotanswered"}:
            return "notanswered"
        if value in {"gt"}:
            return "greaterthan"
        if value in {"gte"}:
            return "greaterthanorequal"
        if value in {"lt"}:
            return "lessthan"
        if value in {"lte"}:
            return "lessthanorequal"
        return value

    @staticmethod
    def buildChoiceTextById(questions):
        choice_texts_by_qid = {}
        for qid, q_data in questions.items():
            mapping = {}
            for choice_id, choice in q_data.get('choices', {}).items():
                text = Survey.stripTags(choice.get('choiceText', '')).strip()
                if text:
                    mapping[str(choice_id)] = text
            for answer_id, answer in q_data.get('answers', {}).items():
                text = Survey.stripTags(answer.get('answerText', '')).strip()
                if text:
                    mapping[str(answer_id)] = text
            if mapping:
                choice_texts_by_qid[qid] = mapping
        return choice_texts_by_qid

    @staticmethod
    def buildQuestionLogicMap(questions):
        logic_map = {}
        if not isinstance(questions, dict):
            return logic_map
        logic_keys = [
            "DisplayLogic",
            "displayLogic",
            "DisplayLogicExpression",
            "displayLogicExpression",
            "DisplayLogicInput",
            "displayLogicInput"
        ]
        for qid, q_data in questions.items():
            logic = None
            for key in logic_keys:
                if key in q_data:
                    logic = q_data.get(key)
                    break
            if logic is None:
                logic = Survey.findNestedValue(q_data, logic_keys)
            conditions = Survey.parseBranchLogic(logic) if logic else None
            if conditions:
                logic_map[qid] = conditions
        return logic_map

    @staticmethod
    def extractChoiceId(choice_locator):
        if not choice_locator or not isinstance(choice_locator, str):
            return None
        match = re.search(r'/([^/]+)$', choice_locator)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def tokensFromValue(value):
        tokens = set()
        if value is None:
            return tokens
        if isinstance(value, dict):
            for key in ["Value", "value", "Text", "text", "Constant", "constant"]:
                if key in value:
                    tokens |= Survey.tokensFromValue(value.get(key))
            return tokens
        if isinstance(value, (list, tuple, set)):
            for item in value:
                tokens |= Survey.tokensFromValue(item)
            return tokens
        if isinstance(value, bool):
            tokens.add(str(int(value)))
            return tokens
        if isinstance(value, (int, float)):
            tokens.add(str(value))
            return tokens
        if isinstance(value, str):
            stripped = Survey.stripTags(value).strip()
            if stripped:
                tokens.add(stripped)
                tokens.add(Survey.normalizeText(stripped))
            return tokens
        tokens.add(str(value))
        return tokens

    @staticmethod
    def answerTokens(answer):
        tokens = set()
        if answer is None:
            return tokens
        if isinstance(answer, (list, tuple, set)):
            for item in answer:
                tokens |= Survey.tokensFromValue(item)
            return tokens
        return Survey.tokensFromValue(answer)

    @staticmethod
    def expectedTokens(cond, choice_texts_by_qid):
        tokens = set()
        qid = cond.get('question_id')
        choice_id = Survey.extractChoiceId(cond.get('choice_locator'))
        if qid and choice_id:
            tokens.add(str(choice_id))
            text = choice_texts_by_qid.get(qid, {}).get(str(choice_id))
            if text:
                tokens.add(text)
                tokens.add(Survey.normalizeText(text))

        raw = cond.get('raw', {})
        if isinstance(raw, dict):
            found = Survey.findNestedValue(
                raw,
                ["Constant", "Value", "Text", "SelectedChoice", "ChoiceID", "ChoiceLocator"]
            )
            if found not in (None, {}):
                tokens |= Survey.tokensFromValue(found)

        desc = cond.get('description', '')
        if isinstance(desc, str) and desc:
            for match in re.findall(r'"([^"]+)"', desc):
                tokens.add(match)
                tokens.add(Survey.normalizeText(match))

        return tokens

    @staticmethod
    def toNumber(value):
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            stripped = value.strip()
            try:
                return float(stripped)
            except Exception:
                return None
        return None

    @staticmethod
    def answerMatchesExpected(answer, expected_tokens):
        if not expected_tokens:
            return False
        answer_tokens = Survey.answerTokens(answer)
        if not answer_tokens:
            return False
        return bool(answer_tokens & expected_tokens)

    @staticmethod
    def answerContainsExpected(answer, expected_tokens):
        if not expected_tokens:
            return False
        if answer is None:
            return False
        if isinstance(answer, (list, tuple, set)):
            joined = " ".join(str(item) for item in answer)
        else:
            joined = str(answer)
        normalized = Survey.normalizeText(joined)
        for token in expected_tokens:
            if not token:
                continue
            if Survey.normalizeText(str(token)) in normalized:
                return True
        return False

    @staticmethod
    def isEmptyAnswer(answer):
        if answer is None:
            return True
        if isinstance(answer, (list, tuple, set)):
            return len(answer) == 0
        if isinstance(answer, str):
            return answer.strip() == ""
        return False

    @staticmethod
    def conditionSatisfied(cond, responses, choice_texts_by_qid):
        if not isinstance(cond, dict):
            return False
        qid = cond.get('question_id')
        if not qid:
            return False
        answer = responses.get(qid)
        operator = Survey.normalizeOperator(cond.get('operator'))
        expected = Survey.expectedTokens(cond, choice_texts_by_qid)

        if operator in {"selected", "equal", "is"}:
            return Survey.answerMatchesExpected(answer, expected)
        if operator in {"notselected", "notequal", "isnot"}:
            return not Survey.answerMatchesExpected(answer, expected)
        if operator in {"contains"}:
            return Survey.answerContainsExpected(answer, expected)
        if operator in {"notcontains"}:
            return not Survey.answerContainsExpected(answer, expected)
        if operator in {"empty", "isnotanswered", "notanswered"}:
            return Survey.isEmptyAnswer(answer)
        if operator in {"notempty", "isanswered", "answered"}:
            return not Survey.isEmptyAnswer(answer)
        if operator in {"greaterthan", "greaterthanorequal", "lessthan", "lessthanorequal"}:
            answer_num = Survey.toNumber(answer if not isinstance(answer, list) else (answer[0] if answer else None))
            expected_num = None
            if expected:
                for token in expected:
                    expected_num = Survey.toNumber(token)
                    if expected_num is not None:
                        break
            if answer_num is None or expected_num is None:
                return False
            if operator == "greaterthan":
                return answer_num > expected_num
            if operator == "greaterthanorequal":
                return answer_num >= expected_num
            if operator == "lessthan":
                return answer_num < expected_num
            if operator == "lessthanorequal":
                return answer_num <= expected_num

        return False

    @staticmethod
    def normalizeConditions(conditions):
        if not conditions:
            return None
        if isinstance(conditions, dict):
            return conditions
        if isinstance(conditions, list):
            items = [Survey.normalizeConditions(item) for item in conditions if item]
            items = [item for item in items if item]
            if not items:
                return None
            if len(items) == 1:
                return items[0]
            return {'op': 'and', 'conditions': items}
        return None

    @staticmethod
    def conditionsSatisfied(conditions, responses, choice_texts_by_qid):
        if not conditions:
            return True
        if isinstance(conditions, list):
            for cond in conditions:
                if not Survey.conditionsSatisfied(cond, responses, choice_texts_by_qid):
                    return False
            return True
        if isinstance(conditions, dict):
            if 'op' in conditions and 'conditions' in conditions:
                op = (conditions.get('op') or 'and').lower()
                items = conditions.get('conditions') or []
                if op == 'or':
                    return any(Survey.conditionsSatisfied(item, responses, choice_texts_by_qid) for item in items)
                return all(Survey.conditionsSatisfied(item, responses, choice_texts_by_qid) for item in items)
            return Survey.conditionSatisfied(conditions, responses, choice_texts_by_qid)
        return False

    @staticmethod
    def rebuildPathTaken(responses, questions, flow_structure):
        qids_by_block = {}
        for qid, q_data in questions.items():
            flow_info = q_data.get('flow_info') or {}
            block_id = flow_info.get('block_id')
            if block_id:
                qids_by_block.setdefault(block_id, []).append(qid)

        block_order = [f['block_id'] for f in flow_structure if f.get('type') == 'block']
        path = []
        for block_id in block_order:
            for qid in qids_by_block.get(block_id, []):
                if qid in responses:
                    path.append(block_id)
                    break
        return path

    @staticmethod
    def selectRandomizedBlocks(questions, participant_seed=None):
        randomizer_info = {}
        for q_data in questions.values():
            flow_info = q_data.get('flow_info') or {}
            rand_id = flow_info.get('randomizer_id')
            if not rand_id:
                continue
            block_id = flow_info.get('block_id')
            if not block_id:
                continue
            entry = randomizer_info.setdefault(
                rand_id,
                {
                    'subset_size': flow_info.get('randomizer_subset_size'),
                    'blocks': set()
                }
            )
            entry['blocks'].add(block_id)

        selected_by_randomizer = {}
        for rand_id, info in randomizer_info.items():
            blocks = list(info['blocks'])
            subset = info.get('subset_size')
            if not subset or subset >= len(blocks):
                selected = set(blocks)
            else:
                rng = random.Random(f"{participant_seed}:{rand_id}")
                selected = set(rng.sample(blocks, k=subset))
            selected_by_randomizer[rand_id] = selected

        return selected_by_randomizer

    @staticmethod
    def applyConditionalLogic(simulated_responses, survey_data, choice_texts_by_qid, question_logic_by_qid=None):
        questions = survey_data.get('questions', {})
        flow_structure = survey_data.get('flow_structure', [])
        if not isinstance(simulated_responses, list) or not questions:
            return simulated_responses
        question_logic_by_qid = question_logic_by_qid or {}

        for idx, participant in enumerate(simulated_responses):
            responses = participant.get('responses', {})
            if not isinstance(responses, dict):
                continue

            participant_seed = participant.get('participant_id', idx)
            selected_by_randomizer = Survey.selectRandomizedBlocks(questions, participant_seed)

            current = dict(responses)
            for _ in range(5):
                filtered = {}
                for qid, answer in current.items():
                    q_data = questions.get(qid)
                    if not q_data:
                        continue
                    flow_info = q_data.get('flow_info')
                    if flow_info:
                        rand_id = flow_info.get('randomizer_id')
                        block_id = flow_info.get('block_id')
                        if rand_id and block_id:
                            selected_blocks = selected_by_randomizer.get(rand_id)
                            if selected_blocks is not None and block_id not in selected_blocks:
                                continue
                    question_conditions = question_logic_by_qid.get(qid)
                    conditions = Survey.combineConditions(
                        flow_info.get('conditions') if flow_info else None,
                        question_conditions
                    )
                    is_conditional = bool(flow_info and flow_info.get('conditional')) or bool(question_conditions)
                    if not is_conditional:
                        filtered[qid] = answer
                        continue
                    if Survey.conditionsSatisfied(conditions, current, choice_texts_by_qid):
                        filtered[qid] = answer
                if len(filtered) == len(current):
                    current = filtered
                    break
                current = filtered

            participant['responses'] = current
            participant['path_taken'] = Survey.rebuildPathTaken(current, questions, flow_structure)

        return simulated_responses

    @staticmethod
    def postResponsesToQualtrics(survey_id: str, simulated_responses):
        api_token = os.getenv("API_TOKEN")
        if not api_token:
            raise HTTPException(status_code=500, detail="Qualtrics API token not set")

        survey = Survey.getSurvey(survey_id)
        questions = survey.get('result', {}).get('questions', {})
        choice_map, multi_select_qids, slider_qids, required_qids, default_choice_by_qid, supported_qids = Survey.buildChoiceMap(questions)

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
            values = Survey.buildQualtricsValues(
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

    # Generates responses from the LLM
    @staticmethod
    def generateResponses(survey_id: str, instructions: str, n: int, model: str = "openai/gpt-4o"):
        # Fetch the cleaned data
        survey_data = Survey.cleanSurvey(survey_id)
        raw_survey = Survey.getSurvey(survey_id)
        raw_questions = raw_survey.get('result', {}).get('questions', {})
        choice_texts_by_qid = Survey.buildChoiceTextById(raw_questions)
        question_logic_by_qid = Survey.buildQuestionLogicMap(raw_questions)
        try:
            definition = Survey.getSurveyDefinition(survey_id)
            def_questions = Survey.extractDefinitionQuestions(definition)
            def_logic = Survey.buildQuestionLogicMap(def_questions)
            if def_logic:
                merged_logic = dict(def_logic)
                merged_logic.update(question_logic_by_qid)
                question_logic_by_qid = merged_logic
        except Exception:
            pass

        if raw_questions:
            missing_qids = [qid for qid in raw_questions.keys() if qid not in question_logic_by_qid]
            for qid in missing_qids:
                try:
                    qdef = Survey.getSurveyQuestionDefinition(survey_id, qid)
                    q_payload = qdef.get('result', qdef)
                    q_logic = Survey.buildQuestionLogicMap({qid: q_payload})
                    if q_logic:
                        question_logic_by_qid.update(q_logic)
                except Exception:
                    continue

        # Prepare OpenRouter configuration
        api_key = OPENROUTER_API_KEY
        if not api_key:
            raise HTTPException(status_code=500, detail="OpenRouter API key not set")

        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        # System Prompt
        system_context = f"""
        You are an expert Survey Simulation Engine. You have been provided with the structure and logic of a Qualtrics survey in JSON format.

        ### INSTRUCTIONS:
        1. **Simulate {n} distinct participants** taking this survey.
        2. **Persona**: All participants must fit this description: "{instructions}".
        3. **Behavior**:
           - Respect all branching logic (e.g., if a user selects "No" and the logic skips a block, do not answer questions in that block).
           - Vary the answers realistically within the bounds of the persona.
        ### OUTPUT FORMAT:
        Return ONLY a valid JSON array of objects. Do not include markdown formatting (like ```json).
        Each object in the array represents one participant and must have this structure:
        [
          {{
            "participant_id": 1,
            "persona_notes": "Brief specific details about this simulated user",
            "path_taken": ["BlockID_1", "BlockID_2"],
            "responses": {{
                "QID1": "Selected Choice Text",
                "QID2": "Open ended text response..."
            }}
          }},
          ...
        ]
        """

        # Construct the Payload
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_context},
                {"role": "user", "content": f"Survey Structure JSON:\n{json.dumps(survey_data)}"}
            ],
            "temperature": 0.7
        }

        # Send Request
        try:
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()

            result = response.json()
            content = result['choices'][0]['message']['content']

            # Cleanup in case the model adds markdown code blocks despite instructions
            if content.startswith("```"): content = content.replace("```json", "").replace("```", "").strip()

            simulated_responses = json.loads(content)
            simulated_responses = Survey.applyConditionalLogic(
                simulated_responses,
                survey_data,
                choice_texts_by_qid,
                question_logic_by_qid
            )

            return {
                "metadata": {
                    "survey_id": survey_id,
                    "simulations_requested": n,
                    "persona_prompt": instructions
                },
                "simulated_responses": simulated_responses,
                "usage": result.get('usage')
            }

        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=502, detail=f"OpenRouter API Error: {str(e)}")
        except json.JSONDecodeError: return {"error": "Model did not return valid JSON", "raw_output": content}

    # Converts JSON LLM response to exportable csv
    @staticmethod
    def jsonToCsv(survey_json):
        def generate():
            output = io.StringIO()
            writer = csv.writer(output)

            writer.writerow([
                "participant_id",
                "persona_notes",
                "path_taken",
                "question_id",
                "answer"
            ])
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

            for participant in survey_json.get("simulated_responses", []):
                participant_id = participant.get("participant_id")
                persona_notes = participant.get("persona_notes", "")
                path = participant.get("path_taken", [])
                path_taken = " > ".join(path) if isinstance(path, list) else (path or "")
                responses = participant.get("responses", {})

                if isinstance(responses, dict):
                    for question_id, answer in responses.items():
                        writer.writerow([
                            participant_id,
                            persona_notes,
                            path_taken,
                            question_id,
                            answer
                        ])
                        yield output.getvalue()
                        output.seek(0)
                        output.truncate(0)

        return generate()
