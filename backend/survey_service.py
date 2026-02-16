import json, requests, re, os, csv, io, random
from fastapi import HTTPException
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
import fetch_survey as survey_parsing
from app import OPENROUTER_API_KEY, SCOPES
from qualtrics_import import QualtricsImport

class Survey:
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

    @staticmethod
    def normalizeText(value):
        return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', value.lower())).strip()

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
                text = survey_parsing.stripTags(choice.get('choiceText', '')).strip()
                if text:
                    mapping[str(choice_id)] = text
            for answer_id, answer in q_data.get('answers', {}).items():
                text = survey_parsing.stripTags(answer.get('answerText', '')).strip()
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
                logic = survey_parsing.findNestedValue(q_data, logic_keys)
            conditions = survey_parsing.parseBranchLogic(logic) if logic else None
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
            stripped = survey_parsing.stripTags(value).strip()
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
            found = survey_parsing.findNestedValue(
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
                    conditions = survey_parsing.combineConditions(
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

    # Send responses back to Qualtrics
    @staticmethod
    def postResponsesToQualtrics(survey_id: str, simulated_responses):
        return QualtricsImport.postResponsesToQualtrics(survey_id, simulated_responses)

    # Generates responses from the LLM
    @staticmethod
    def generateResponses(survey_id: str, instructions: str, n: int, model: str = "openai/gpt-4o"):
        # Fetch the cleaned data
        survey_data = survey_parsing.cleanSurvey(survey_id)
        raw_survey = survey_parsing.getSurvey(survey_id)
        raw_questions = raw_survey.get('result', {}).get('questions', {})
        choice_texts_by_qid = Survey.buildChoiceTextById(raw_questions)
        question_logic_by_qid = Survey.buildQuestionLogicMap(raw_questions)
        try:
            definition = survey_parsing.getSurveyDefinition(survey_id)
            def_questions = survey_parsing.extractDefinitionQuestions(definition)
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
                    qdef = survey_parsing.getSurveyQuestionDefinition(survey_id, qid)
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

        payload = Survey.build_llm_payload(
            survey_id=survey_id,
            instructions=instructions,
            n=n,
            model=model,
            survey_data=survey_data
        )

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

    @staticmethod
    def build_llm_payload(survey_id: str, instructions: str, n: int, model: str = "openai/gpt-4o", survey_data=None):
        if survey_data is None:
            survey_data = survey_parsing.cleanSurvey(survey_id)

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

        return {
            "model": model,
            "messages": [
                {"role": "system", "content": system_context},
                {"role": "user", "content": f"Survey Structure JSON:\n{json.dumps(survey_data)}"}
            ],
            "temperature": 0.7
        }

    # Converts JSON LLM response to exportable csv
    @staticmethod
    def jsonToCsv(survey_json):
        def generate():
            output = io.StringIO()
            writer = csv.writer(output)

            writer.writerow([
                "participant_id",
                "persona_notes",
                "question_id",
                "answer"
            ])
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

            for participant in survey_json.get("simulated_responses", []):
                participant_id = participant.get("participant_id")
                persona_notes = participant.get("persona_notes", "")
                responses = participant.get("responses", {})

                if isinstance(responses, dict):
                    for question_id, answer in responses.items():
                        writer.writerow([
                            participant_id,
                            persona_notes,
                            question_id,
                            answer
                        ])
                        yield output.getvalue()
                        output.seek(0)
                        output.truncate(0)

        return generate()
