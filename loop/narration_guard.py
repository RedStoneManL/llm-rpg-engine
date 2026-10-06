"""Reject obvious repetition of previously published story, before display."""
import re
from kernel.contextsystem import ValidationError


def validate_narration(commit, world):
    if not isinstance(commit.narration,str): return []
    text=re.sub(r'\s+','',commit.narration)
    if len(text)<120: return []
    scenes=world.get('systems',{}).get('narrative',{}).get('scenes',[])
    previous=[raw for bucket in scenes[-2:] for raw in bucket.get('raw',[])][-4:]
    for raw in previous:
        prior=re.sub(r'\s+','',raw)
        if len(prior)>=120 and (text==prior or text[:180]==prior[:180]):
            return [ValidationError('narration','','repeated_story',
                '正文复述了已发布的旧回合。只回应最后一条玩家行动，从当前结果继续写新内容；不得复制最近剧情原文。')]
    return []
