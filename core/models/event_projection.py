"""Read-query settings and calendar intervals for temporal research views."""
from dataclasses import dataclass, field, asdict
from datetime import date
import calendar
from core.models.entity_expression import coerce_expression

MAX_EVENT_PAGE = 200
MAX_EVENT_EXCLUSIONS = 5000
MAX_WORD_WINDOW=1000


@dataclass(frozen=True)
class EventFilter:
    expression: object = None
    expression_text: str = ''
    entity_bindings: dict = field(default_factory=dict)
    association: str = 'sentence'
    window_words: int = 30
    date_from: str = ''
    date_to: str = ''
    include_unresolved: bool = False
    include_excluded: bool = False
    excluded_artifact_ids: tuple = ()
    show_hidden: bool = False
    included_artifact_ids: tuple = ()
    zoom: int = 1


def coerce_filter(value=None):
    value = asdict(value) if isinstance(value,EventFilter) else dict(value or {})
    if set(value)-set(EventFilter.__dataclass_fields__):
        raise ValueError('Unknown event filter setting.')
    result = EventFilter(**value)
    if type(result.zoom) is not int or not 1 <= result.zoom <= 16:
        raise ValueError('Timeline zoom must be between 1 and 16.')
    if result.association not in {'sentence','word_window','event'}:
        raise ValueError('Choose same sentence or a word window within the event sentence.')
    if type(result.window_words) is not int or not 0 <= result.window_words <= MAX_WORD_WINDOW:
        raise ValueError('Word window must be between 0 and 1000.')
    for key in ('include_unresolved','include_excluded','show_hidden'):
        if type(getattr(result,key)) is not bool:
            raise ValueError('Event visibility options must be true or false.')
    if not isinstance(result.expression_text,str) or len(result.expression_text)>4096:
        raise ValueError('Entity filter text is too long.')
    if not isinstance(result.entity_bindings,dict) or len(result.entity_bindings)>64 or not all(
            isinstance(key,str) and 0<len(key)<=512 and isinstance(identity,str) and 0<len(identity)<=512
            for key,identity in result.entity_bindings.items()):
        raise ValueError('Choose valid entity bindings.')
    if not isinstance(result.excluded_artifact_ids,(list,tuple)) or len(result.excluded_artifact_ids)>MAX_EVENT_EXCLUSIONS or not all(
            isinstance(identity,str) and 0<len(identity)<=512 for identity in result.excluded_artifact_ids):
        raise ValueError('Use a bounded list of excluded event identities.')
    start,end = calendar_interval(result.date_from),calendar_interval(result.date_to)
    if not isinstance(result.included_artifact_ids, (list, tuple)) or len(result.included_artifact_ids) > MAX_EVENT_EXCLUSIONS or any(
            not isinstance(identity, str) or not 0 < len(identity) <= 512 for identity in result.included_artifact_ids):
        raise ValueError('Use a bounded list of personally included date identities.')
    if start and end and start[0]>end[1]:
        raise ValueError('The end date must be on or after the start date.')
    return EventFilter(**{**asdict(result),'expression':coerce_expression(result.expression),
                          'excluded_artifact_ids':tuple(sorted(set(result.excluded_artifact_ids))),
                          'included_artifact_ids':tuple(sorted(set(result.included_artifact_ids)))})


def calendar_interval(value):
    """A year/month represents its whole interval, never an invented exact day."""
    if not isinstance(value,str):
        raise ValueError('Dates must use YYYY, YYYY-MM or YYYY-MM-DD.')
    if not value:
        return None
    parts=value.split('-')
    if len(parts) not in {1,2,3} or len(parts[0])!=4 or not all(part.isascii() and part.isdigit() for part in parts):
        raise ValueError('Dates must use YYYY, YYYY-MM or YYYY-MM-DD.')
    if any(len(part)!=2 for part in parts[1:]):
        raise ValueError('Use two-digit months and days.')
    try:
        year=int(parts[0]); month=int(parts[1]) if len(parts)>1 else 1
        day=int(parts[2]) if len(parts)>2 else 1
        start=date(year,month,day)
        end=start if len(parts)==3 else date(year,month,calendar.monthrange(year,month)[1]) if len(parts)==2 else date(year,12,31)
    except (ValueError,OverflowError):
        raise ValueError('Choose a valid calendar date.') from None
    return start.isoformat(),end.isoformat()


def interval_matches(interval,filters):
    if interval is None:
        return filters.include_unresolved and not (filters.date_from or filters.date_to)
    start,end=calendar_interval(filters.date_from),calendar_interval(filters.date_to)
    return (not start or interval[1]>=start[0]) and (not end or interval[0]<=end[1])
