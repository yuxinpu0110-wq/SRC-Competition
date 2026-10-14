"""小型禁区可见图，复用实体膨胀的segment_legal；不缩禁区、不执行动作。"""
import heapq
from match_tactics import Point,distance


def route(tactics,start,target):
    if tactics.in_defense(start) or distance(target,tactics.legal(target))>1:return None
    if tactics.segment_legal(start,target):return [start,target]
    c=tactics.c
    edge=c['field_length']/2-c['defense_depth']-122
    half=c['defense_width']/2+122
    nodes=[start,target]+[Point(x,y) for x in (-edge,edge) for y in (-half,half)]
    queue=[(0,0)];cost={0:0};previous={}
    while queue:
        d,i=heapq.heappop(queue)
        if d!=cost[i]:continue
        if i==1:
            indices=[1]
            while indices[-1]!=0:indices.append(previous[indices[-1]])
            return [nodes[n] for n in reversed(indices)]
        for j in range(len(nodes)):
            if i==j or not tactics.segment_legal(nodes[i],nodes[j]):continue
            candidate=d+distance(nodes[i],nodes[j])
            if candidate<cost.get(j,float('inf')):
                cost[j]=candidate;previous[j]=i;heapq.heappush(queue,(candidate,j))
    return None


def length(path):
    return sum(distance(a,b) for a,b in zip(path,path[1:]))
