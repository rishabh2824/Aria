import re

from markdownify import markdownify as md

from .flow_parser import buildSurveyFlow, mapQuestions, mergeFlow
from .qualtrics_api import getSurvey, getSurveyFlow


# Convert question type codes to readable strings
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


#
def numberValidation(validation):
    settings = validation.get('settings', {})
    rules = {}
    if 'minimum' in settings:
        rules['min'] = settings['minimum']
    if 'maximum' in settings:
        rules['max'] = settings['maximum']
    if 'maxDecimals' in settings:
        rules['decimals'] = settings['maxDecimals']
    return rules


# Prepares the API to send to LLM
def cleanSurvey(survey_id: str):
    survey = getSurvey(survey_id)
    flow = getSurveyFlow(survey_id)

    questions = survey['result']['questions']
    blocks = survey['result']['blocks']

    SKIP_QUESTION_TYPES = ['Timing']
    cleaned_questions = {}

    # Clean & Normalize Questions
    for qid, q_data in questions.items():
        q_type = q_data.get('questionType', {})
        type_str = q_type.get('type', '')

        if type_str in SKIP_QUESTION_TYPES:
            continue

        raw_text = q_data.get('questionText', '').strip()
        if not raw_text or raw_text == 'Timing':
            continue

        # Convert HTML to MD
        text_structure = md(raw_text, heading_style="ATX")

        # Normalize whitespace
        text_cleaned = re.sub(r'\n{3,}', '\n\n', text_structure).strip()

        cleaned_q = {
            'name': q_data.get('questionName'),
            'type': formatQuestionTypes(q_type),
            'text': text_cleaned
        }

        # Display-only questions
        if cleaned_q['type'] == 'Display Text':
            cleaned_q['display_only'] = True

        validation = q_data.get('validation', {})
        if validation.get('doesForceResponse', False):
            cleaned_q['required'] = True

        # Number validation
        if validation.get('type') == 'ValidNumber':
            cleaned_q['validation'] = numberValidation(validation)

        # Choices
        if 'choices' in q_data:
            cleaned_q['choices'] = [
                choice.get('choiceText', choice.get('description', ''))
                for choice in q_data['choices'].values()
            ]

        cleaned_questions[qid] = cleaned_q

    # Flow metadata
    flow_structure = buildSurveyFlow(flow)
    question_to_block = mapQuestions(blocks)
    enriched_questions = mergeFlow(cleaned_questions, question_to_block, flow_structure)

    embedded_fields = {}
    for item in flow_structure:
        if item.get('type') != 'embedded_data':
            continue
        for entry in item.get('embedded_data', []) or []:
            field = entry.get('field')
            if not field:
                continue
            info = embedded_fields.setdefault(
                field,
                {'variable_type': entry.get('variable_type'), 'values': set()}
            )
            if not info.get('variable_type') and entry.get('variable_type'):
                info['variable_type'] = entry.get('variable_type')
            if entry.get('value') is not None:
                info['values'].add(entry.get('value'))

    embedded_fields_out = {}
    for field, info in embedded_fields.items():
        embedded_fields_out[field] = {
            'variable_type': info.get('variable_type'),
            'values': sorted(info.get('values', set()))
        }

    return {
        'metadata': {
            'survey_id': survey_id,
            'total_questions': len(enriched_questions),
            'has_conditional_logic': any(f['type'] == 'branch' for f in flow_structure),
            'has_randomization': any(f['type'] == 'randomizer' for f in flow_structure),
            'embedded_data_fields': embedded_fields_out
        },
        'questions': enriched_questions,
        'flow_structure': flow_structure
    }
