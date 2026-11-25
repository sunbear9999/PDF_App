"""A chronological rail with accessible dates and contextual event rows."""
from PySide6.QtCore import QSize,Qt,QPointF
from PySide6.QtGui import QPen
from PySide6.QtWidgets import QStyledItemDelegate,QStyleOptionViewItem

TIMELINE_ROW_HEIGHT=40   # two lines: date + precision, sentence + co-mentioned names


class TimelineDateDelegate(QStyledItemDelegate):
    def sizeHint(self,option,index):
        hint=super().sizeHint(option,index)
        return QSize(max(160,hint.width()+32) if index.column()==0 else hint.width(),TIMELINE_ROW_HEIGHT)

    def paint(self,painter,option,index):
        if index.column()!=0:
            super().paint(painter,option,index)
            return
        inset=QStyleOptionViewItem(option)
        inset.rect=option.rect.adjusted(28,0,0,0)
        super().paint(painter,inset,index)
        painter.save()
        x=option.rect.left()+14
        color=option.palette.highlight().color()
        painter.setPen(QPen(color,2))
        painter.drawLine(x,option.rect.top(),x,option.rect.bottom())
        painter.setBrush(color)
        painter.drawEllipse(QPointF(x,option.rect.center().y()),5,5)
        painter.restore()


def event_columns(row,source_title):
    state=[]
    if row.get('hidden'): state.append('Hidden in this view')
    if not row.get('event_candidate'): state.append('Date observation: '+str(row.get('exclusion_reason') or 'not an event'))
    if not row.get('association_complete'): state.append('Association incomplete')
    state.append('Date: '+(row.get('review_state') or 'unreviewed'))
    state.append('Event: '+(row.get('event_decision') or 'candidate'))
    if row.get('authored'): state.append('User-authored interpretation')
    if row.get('personally_included'): state.append('Personally included observation')
    names=', '.join(person['name'] for person in row.get('participants') or [])
    context=(row.get('text') or '')[:400]
    if names: context+='\nCo-mentioned: '+names
    if row.get('participants_truncated'): context+=f" · {row['participant_count']} total (preview)"
    date=row.get('date') or 'Date unresolved'
    precision=row.get('precision') or 'unrecorded precision'
    certainty=row.get('certainty') or 'unrecorded certainty'
    return (date+'\n'+precision+' · '+certainty,context,source_title(row.get('source_id')),' · '.join(state))
