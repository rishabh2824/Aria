import json
from fastapi import FastAPI, HTTPException
import requests
import os
from dotenv import load_dotenv
from markdownify import markdownify as md
import re

load_dotenv()

app = FastAPI()


datacenter = os.getenv("DATACENTER_ID")
BASE_URL = f"https://{datacenter}.qualtrics.com/API/v3"


# Returns the cleaned Qualtrics Survey questions.
@app.get("/{survey_id}")
def fetch_survey(survey_id: str):
    return Survey.cleanSurvey(survey_id)
    # return Survey.getSurvey(survey_id)
    # return Survey.getSurveyFlow(survey_id)


class Survey:
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

    # Extract Embedded data
    @staticmethod
    def extractEmbeddedData(flow_data):
        embedded = {}

        def traverse(items):
            if not isinstance(items, list): return

            for item in items:
                if item.get('Type') == 'EmbeddedData':
                    for data in item.get('EmbeddedData', []):
                        embedded[data['Field']] = {
                            'description': data.get('Description', ''),
                            'value': data.get('Value', ''),
                            'type': data.get('VariableType', 'String')
                        }

                if 'Flow' in item: traverse(item['Flow'])
        if 'Flow' in flow_data: traverse(flow_data['Flow'])
        return embedded

    # Replace dynamic placeholders with generic markers
    @staticmethod
    def handleEmbeddedVariables(text):
        token_pattern = re.compile(r'\$\{.*?\}|\$e\{.*?\}')
        text = token_pattern.sub('[DYNAMIC_VALUE]', text)
        return text

    @staticmethod
    def stripTags(html):
        if not html: return ''
        return re.sub(r'<[^>]*>', '', html).strip()

    @staticmethod
    def parseBranchLogic(branch_logic):
        if not branch_logic or branch_logic.get('Type') != 'BooleanExpression': return None

        conditions = []
        for key, val in branch_logic.items():
            if key == 'Type': continue

            if isinstance(val, dict) and val.get('Type') == 'If':
                expr = val.get('0', {})
                if expr:
                    conditions.append({
                        'question_id': expr.get('QuestionID'),
                        'choice_locator': expr.get('ChoiceLocator'),
                        'operator': expr.get('Operator'),
                        'description': Survey.stripTags(expr.get('Description', ''))
                    })
        return conditions or None

    # Clean the survey flow
    @staticmethod
    def buildSurveyFlow(flow_data):
        flow_structure = []
        seq = 0

        root = flow_data.get('result', flow_data)

        def traverse(items, parent_cond=None):
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
                        'condition': parent_cond
                    })

                elif t == 'Branch':
                    conditions = Survey.parseBranchLogic(item.get('BranchLogic', {}))
                    flow_structure.append({
                        'sequence': seq,
                        'type': 'branch',
                        'flow_id': item.get('FlowID'),
                        'description': item.get('Description', ''),
                        'conditions': conditions,
                        'parent_condition': parent_cond
                    })
                    if 'Flow' in item: traverse(item['Flow'], conditions)

                elif t == 'BlockRandomizer':
                    flow_structure.append({
                        'sequence': seq,
                        'type': 'randomizer',
                        'flow_id': item.get('FlowID'),
                        'subset_size': item.get('SubSet'),
                        'even_presentation': item.get('EvenPresentation', False),
                        'condition': parent_cond
                    })
                    if 'Flow' in item:
                        traverse(item['Flow'], parent_cond)

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
            f['block_id']: {'sequence': f['sequence'], 'condition': f['condition']}
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
                    'conditions': info['condition']
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
        qualtrics_token_pattern = re.compile(r'\$\{.*?\}|\$e\{.*?\}')

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

            # Placeholder normalization
            text_cleaned = Survey.handleEmbeddedVariables(text_structure)

            # Normalize whitespace
            text_cleaned = re.sub(r'\n{3,}', '\n\n', text_cleaned).strip()

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
        embedded_data = Survey.extractEmbeddedData(flow)
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
            'embedded_data': embedded_data,
            'questions': enriched_questions,
            'flow_structure': flow_structure
        }

    # Generates responses from the LLM
    @staticmethod
    def generateResponses(survey_id: str, instructions: str, n: int, model: str = "openai/gpt-4o"):
        # Fetch the cleaned data
        survey_data = Survey.cleanSurvey(survey_id)

        # Prepare OpenRouter configuration
        api_key = os.getenv("API_KEY")

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
           - Invent plausible values for any text marked [DYNAMIC_VALUE].

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

            return {
                "metadata": {
                    "survey_id": survey_id,
                    "simulations_requested": n,
                    "persona_prompt": instructions
                },
                "simulated_responses": json.loads(content),
                "usage": result.get('usage')
            }

        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=502, detail=f"OpenRouter API Error: {str(e)}")
        except json.JSONDecodeError: return {"error": "Model did not return valid JSON", "raw_output": content}