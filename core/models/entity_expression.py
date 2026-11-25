"""Typed Boolean entity expressions; no text is evaluated as Python or SQL."""
from dataclasses import dataclass
import json
import re

MAX_EXPRESSION_CHARS = 4096
MAX_EXPRESSION_NODES = 64
MAX_EXPRESSION_DEPTH = 12
TOKEN = re.compile(r'\s*("(?:[^"\\]|\\.)*"|\(|\)|[^\s()]+)')


@dataclass(frozen=True)
class EntityExpression:
    op: str
    entity_id: str = ''
    children: tuple = ()


def coerce_expression(value, depth=0, budget=None):
    if value is None:
        return None
    budget = [MAX_EXPRESSION_NODES] if budget is None else budget
    budget[0] -= 1
    if depth > MAX_EXPRESSION_DEPTH or budget[0] < 0:
        raise ValueError('Entity expression is too complex; use fewer terms or groups.')
    if isinstance(value, EntityExpression):
        value = {'op':value.op, 'entity_id':value.entity_id, 'children':value.children}
    if not isinstance(value, dict) or set(value)-{'op','entity_id','children'}:
        raise ValueError('Use typed entity, and, or, and not expressions.')
    op, identity, children = value.get('op'), value.get('entity_id',''), value.get('children',())
    if not isinstance(children, (list,tuple)):
        raise ValueError('Expression children must be a list.')
    if op == 'entity' and isinstance(identity,str) and 0 < len(identity) <= 512 and not children:
        return EntityExpression(op,identity)
    if identity or op not in {'and','or','not'} or len(children) != (1 if op == 'not' else 2) or any(child is None for child in children):
        raise ValueError('AND/OR need two terms; NOT needs one term.')
    return EntityExpression(op,children=tuple(coerce_expression(child,depth+1,budget) for child in children))


def parse_expression(text, resolve):
    if not isinstance(text,str) or len(text)>MAX_EXPRESSION_CHARS:
        raise ValueError('Entity filter text is too long.')
    if not text.strip():
        return None
    tokens = TOKEN.findall(text)
    if len(tokens) > MAX_EXPRESSION_NODES*2:
        raise ValueError('Use fewer entity terms.')
    parser = _Parser(tokens,resolve)
    result = parser.expression()
    if parser.index != len(tokens):
        raise ValueError('Use AND/OR between names; put multiword names in double quotes.')
    return coerce_expression(result)


class _Parser:
    def __init__(self,tokens,resolve):
        self.tokens,self.resolve,self.index = tokens,resolve,0

    def consume(self,value):
        if self.index < len(self.tokens) and self.tokens[self.index].upper()==value:
            self.index += 1
            return True
        return False

    def expression(self,depth=0):
        left = self.conjunction(depth)
        while self.consume('OR'):
            left = EntityExpression('or',children=(left,self.conjunction(depth)))
        return left

    def conjunction(self,depth):
        left = self.term(depth)
        while self.consume('AND'):
            left = EntityExpression('and',children=(left,self.term(depth)))
        return left

    def term(self,depth):
        if depth>MAX_EXPRESSION_DEPTH:
            raise ValueError('Entity filter has too many nested groups.')
        if self.consume('NOT'):
            return EntityExpression('not',children=(self.term(depth+1),))
        if self.consume('('):
            value = self.expression(depth+1)
            if not self.consume(')'):
                raise ValueError('Close every parenthesized entity group.')
            return value
        if self.index >= len(self.tokens) or self.tokens[self.index].upper() in {'AND','OR',')'}:
            raise ValueError('Choose an entity after each operator.')
        token = self.tokens[self.index]
        self.index += 1
        try:
            name = json.loads(token) if token.startswith('"') else token
        except ValueError:
            raise ValueError('Close quoted entity names; use valid quoted text.') from None
        return EntityExpression('entity',self.resolve(name))


def expression_ids(expression):
    if expression is None:
        return set()
    if expression.op=='entity':
        return {expression.entity_id}
    return set().union(*(expression_ids(child) for child in expression.children))


def required_any(expression):
    """Entities of which every match names at least one, or None when a match may name none
    (NOT, or OR with such a branch). Lets readers skip passages naming none of them."""
    if expression is None:
        return None
    if expression.op == 'entity':
        return {expression.entity_id}
    if expression.op == 'not':
        return None
    sets = [required_any(child) for child in expression.children]
    if expression.op == 'and':
        known = [found for found in sets if found is not None]
        return min(known, key=len) if known else None
    return None if None in sets else set().union(*sets)


def replace_entity_ids(expression,mapping):
    if expression is None:
        return None
    if expression.op=='entity':
        return EntityExpression('entity',mapping.get(expression.entity_id,expression.entity_id))
    return EntityExpression(expression.op,children=tuple(replace_entity_ids(child,mapping) for child in expression.children))


def evaluate_expression(expression, match):
    """Three-valued logic: missing coordinates must never prove NOT X."""
    if expression is None:
        return True
    if expression.op=='entity':
        return match(expression.entity_id)
    values = [evaluate_expression(child,match) for child in expression.children]
    if expression.op=='not':
        return None if values[0] is None else not values[0]
    if expression.op=='and':
        return False if False in values else (None if None in values else True)
    return True if True in values else (None if None in values else False)
