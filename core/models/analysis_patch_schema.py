"""Machine-readable bounds shared by initial and repair analysis calls."""
MAX_TEXT_PATCHES = 3
MAX_RELATION_PROPOSALS = 12


def analysis_patch_schema():
    identity = {'type': 'string', 'minLength': 1, 'maxLength': 256}
    text = {'type': 'string', 'maxLength': 1500}
    update = {'type': 'object', 'additionalProperties': False, 'required': ['i'],
              'properties': {'i': identity, 'l': {'type': 'string', 'maxLength': 160}, 's': text}}
    relation = {'type': 'object', 'additionalProperties': False,
                'required': ['a', 'b', 'r', 'q', 'why'], 'properties': {
                    'a': identity, 'b': identity, 'r': {'type': 'string', 'maxLength': 80},
                    'q': {'type': 'array', 'minItems': 1, 'maxItems': 4,
                          'items': {'type': 'string', 'pattern': '^q[1-9][0-9]*$'}},
                    'why': {'type': 'string', 'minLength': 20, 'maxLength': 800}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['t', 'u', 'x'],
            'properties': {'t': {'type': 'string', 'maxLength': 160},
                           'u': {'type': 'array', 'maxItems': MAX_TEXT_PATCHES, 'items': update},
                           'x': {'type': 'array', 'maxItems': MAX_RELATION_PROPOSALS, 'items': relation}}}
