import re


# Strip HTML Tags
def stripTags(html):
    if not html:
        return ''
    return re.sub(r'<[^>]*>', '', html).strip()


def findNestedValue(value, keys):
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                found = value.get(key)
                if found is not None and found != {}:
                    return found
        for inner in value.values():
            found = findNestedValue(inner, keys)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple, set)):
        for inner in value:
            found = findNestedValue(inner, keys)
            if found is not None:
                return found
    return None


# Handle the branching logic
def parseBranchLogic(branch_logic):
    if not branch_logic or not isinstance(branch_logic, (dict, list)):
        return None

    def extractEmbeddedField(expr):
        if not isinstance(expr, dict):
            return None

        for key in [
            'EmbeddedData',
            'EmbeddedDataName',
            'EmbeddedDataField',
            'EmbeddedDataId',
            'EmbeddedDataID',
            'Field',
            'field'
        ]:
            if key in expr and expr.get(key):
                return expr.get(key)

        for key in ['LeftOperand', 'Left', 'Operand1', 'LeftExpression']:
            left = expr.get(key)
            if isinstance(left, dict):
                if left.get('Type') == 'EmbeddedData' or left.get('LogicType') == 'EmbeddedData':
                    return left.get('EmbeddedData') or left.get('Field') or left.get('Name')
                for inner_key in ['EmbeddedData', 'Field', 'Name']:
                    if inner_key in left and left.get(inner_key):
                        return left.get(inner_key)
            if isinstance(left, str):
                logic_type = expr.get('LogicType') or expr.get('Type')
                if logic_type in {'EmbeddedField', 'EmbeddedData'}:
                    return left

        description = expr.get('Description') or findNestedValue(expr, ['Description', 'description']) or ''
        if description:
            text = stripTags(description)
            match = re.search(r'Embedded\s*Data[:\s]+([A-Za-z0-9_\-]+)', text, flags=re.IGNORECASE)
            if match:
                return match.group(1)

        return None

    def parse_if(expr):
        if not expr or not isinstance(expr, dict):
            return None

        question_id = (
            expr.get('QuestionID')
            or findNestedValue(expr, ['QuestionID', 'QuestionId', 'questionID', 'questionId'])
        )
        embedded_field = extractEmbeddedField(expr)
        operator = expr.get('Operator') or findNestedValue(expr, ['Operator', 'operator'])
        right_operand = expr.get('RightOperand') or findNestedValue(expr, ['RightOperand', 'Right', 'Operand2', 'RightExpression'])
        choice_locator = (
            expr.get('ChoiceLocator')
            or findNestedValue(expr, ['ChoiceLocator', 'ChoiceLocatorId', 'choiceLocator', 'choiceLocatorId'])
        )
        if not choice_locator:
            choice_id = findNestedValue(
                expr,
                ['ChoiceID', 'ChoiceId', 'choiceID', 'choiceId', 'SelectedChoice', 'SelectedChoiceID', 'SelectableChoiceID']
            )
            if choice_id is not None:
                choice_locator = str(choice_id)

        description = expr.get('Description') or findNestedValue(expr, ['Description', 'description']) or ''
        if description:
            description = stripTags(description)

        if operator is None and choice_locator is not None:
            operator = "Selected"

        if (not question_id and not embedded_field) or not operator:
            return None

        if not isinstance(question_id, str):
            question_id = str(question_id)
        if not isinstance(operator, str):
            operator = str(operator)
        if choice_locator is not None and not isinstance(choice_locator, str):
            choice_locator = str(choice_locator)
        if embedded_field is not None and not isinstance(embedded_field, str):
            embedded_field = str(embedded_field)
        if right_operand is not None and not isinstance(right_operand, str):
            right_operand = str(right_operand)

        raw = dict(expr)
        if question_id and 'QuestionID' not in raw:
            raw['QuestionID'] = question_id
        if operator and 'Operator' not in raw:
            raw['Operator'] = operator
        if choice_locator and 'ChoiceLocator' not in raw:
            raw['ChoiceLocator'] = choice_locator
        if embedded_field and 'EmbeddedData' not in raw:
            raw['EmbeddedData'] = embedded_field
        if right_operand is not None and 'RightOperand' not in raw:
            raw['RightOperand'] = right_operand
        if description and 'Description' not in raw:
            raw['Description'] = description

        return {
            'question_id': question_id,
            'embedded_field': embedded_field,
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

        for key in ['Expression', 'Expressions', 'Logic', 'LogicTree', 'Condition', 'Conditions']:
            if key in expr:
                inner = expr.get(key)
                node = parse_node(inner)
                if node:
                    return node

        expr_type = expr.get('Type') or expr.get('LogicType')
        if expr_type == 'If' or findNestedValue(expr, ['Operator', 'operator']) is not None:
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

    def collect_if_nodes(expr, collected):
        if isinstance(expr, dict):
            node = None
            if findNestedValue(expr, ['Operator', 'operator']) is not None:
                node = parse_if(expr)
            elif any(k in expr for k in ['LeftOperand', 'Left', 'Operand1', 'LeftExpression']):
                node = parse_if(expr)
            if node:
                collected.append(node)
            for val in expr.values():
                collect_if_nodes(val, collected)
        elif isinstance(expr, (list, tuple, set)):
            for val in expr:
                collect_if_nodes(val, collected)

    parsed = parse_node(branch_logic)
    if parsed:
        return parsed

    fallback_nodes = []
    collect_if_nodes(branch_logic, fallback_nodes)
    if not fallback_nodes:
        return None
    if len(fallback_nodes) == 1:
        return fallback_nodes[0]
    return {'op': 'and', 'conditions': fallback_nodes}


def normalizeConditions(conditions):
    if not conditions:
        return None
    if isinstance(conditions, dict):
        return conditions
    if isinstance(conditions, list):
        items = [normalizeConditions(item) for item in conditions if item]
        items = [item for item in items if item]
        if not items:
            return None
        if len(items) == 1:
            return items[0]
        return {'op': 'and', 'conditions': items}
    return None


# Combine conditional logic
def combineConditions(parent_cond, child_cond):
    parent_cond = normalizeConditions(parent_cond)
    child_cond = normalizeConditions(child_cond)
    if not parent_cond and not child_cond:
        return None
    if not parent_cond:
        return child_cond
    if not child_cond:
        return parent_cond
    return {'op': 'and', 'conditions': [parent_cond, child_cond]}


# Clean the survey flow
def buildSurveyFlow(flow_data):
    flow_structure = []
    seq = 0

    root = flow_data.get('result', flow_data)

    def traverse(items, parent_cond=None, parent_randomizer=None, parent_branch_id=None):
        nonlocal seq
        if not isinstance(items, list):
            return

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
                conditions = parseBranchLogic(item.get('BranchLogic', {}))
                combined = combineConditions(parent_cond, conditions)
                branch_id = item.get('FlowID')
                flow_structure.append({
                    'sequence': seq,
                    'type': 'branch',
                    'flow_id': item.get('FlowID'),
                    'description': item.get('Description', ''),
                    'conditions': conditions,
                    'parent_condition': parent_cond
                })
                if 'Flow' in item:
                    traverse(item['Flow'], combined, parent_randomizer, branch_id)

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
                    traverse(item['Flow'], parent_cond, rand_info, parent_branch_id)

            elif t == 'EmbeddedData':
                embedded = []
                for entry in item.get('EmbeddedData', []) or []:
                    embedded.append({
                        'field': entry.get('Field') or entry.get('field') or entry.get('Description'),
                        'value': entry.get('Value'),
                        'variable_type': entry.get('VariableType'),
                        'description': entry.get('Description')
                    })
                flow_structure.append({
                    'sequence': seq,
                    'type': 'embedded_data',
                    'flow_id': item.get('FlowID'),
                    'embedded_data': embedded,
                    'condition': parent_cond,
                    'randomizer_id': parent_randomizer.get('randomizer_id') if parent_randomizer else None,
                    'randomizer_subset_size': parent_randomizer.get('subset_size') if parent_randomizer else None,
                    'randomizer_even_presentation': parent_randomizer.get('even_presentation') if parent_randomizer else None,
                    'branch_id': parent_branch_id
                })

            elif t == 'EndSurvey':
                flow_structure.append({
                    'sequence': seq,
                    'type': 'end_survey',
                    'flow_id': item.get('FlowID'),
                    'ending_type': item.get('EndingType', ''),
                    'condition': parent_cond
                })

    if 'Flow' in root:
        traverse(root['Flow'])
    return flow_structure


#
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
                if qid:
                    mapping[qid] = block_id
    return mapping


#
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
