from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from tradingagents.agents.utils.agent_utils import Toolkit

def show(tag, msgs):
    before = [type(m).__name__ for m in msgs]
    res = Toolkit.budget_messages(
        msgs,
        'gpt-4o-mini',
        reply_tokens_budget=1024,
        safety_margin=0.9,
        max_messages=50,
    )
    after = [type(m).__name__ for m in res]
    print(f"{tag} {before} -> {after}")
    # 打印细节方便人工核查
    for m in res:
        if isinstance(m, AIMessage):
            print('AI tool_calls:', getattr(m, 'tool_calls', None))
        if isinstance(m, ToolMessage):
            print('Tool tool_call_id:', getattr(m, 'tool_call_id', None))

# case1: 孤立的 ToolMessage 应被移除
msgs1 = [HumanMessage(content='Hi'), ToolMessage(content='out', tool_call_id='x1')]
show('case1', msgs1)

# case2: 合法的 AI tool_calls + 匹配的 ToolMessage 保留
msgs2 = [
    HumanMessage(content='Hi'),
    AIMessage(content='call', tool_calls=[{'id': 'x1', 'type': 'tool_call', 'name': 'get_data', 'args': {}}]),
    ToolMessage(content='out', tool_call_id='x1'),
    HumanMessage(content='thanks')
]
show('case2', msgs2)

# case3: 尾部未执行的 AI tool_calls 应被移除（避免 400）
msgs3 = [
    HumanMessage(content='Hi'),
    AIMessage(content='call', tool_calls=[{'id': 'x2', 'type': 'tool_call', 'name': 'get_data', 'args': {}}])
]
show('case3', msgs3)

# case4: 多工具调用：保留有响应的 x1，移除无响应的 x2
msgs4 = [
    AIMessage(content='call', tool_calls=[
        {'id': 'x1', 'type': 'tool_call', 'name': 'a', 'args': {}},
        {'id': 'x2', 'type': 'tool_call', 'name': 'b', 'args': {}}
    ]),
    ToolMessage(content='A done', tool_call_id='x1'),
    HumanMessage(content='next')
]
show('case4', msgs4)

# case5: 多工具调用，两个都响应，均应保留
msgs5 = [
    AIMessage(content='call', tool_calls=[
        {'id': 'y1', 'type': 'tool_call', 'name': 'a', 'args': {}},
        {'id': 'y2', 'type': 'tool_call', 'name': 'b', 'args': {}}
    ]),
    ToolMessage(content='A done', tool_call_id='y1'),
    ToolMessage(content='B done', tool_call_id='y2'),
    HumanMessage(content='ok')
]
show('case5', msgs5)

# case6: 多工具调用，均无响应，应清空 AI 的 tool_calls，仅保留其 content
msgs6 = [
    HumanMessage(content='Hi'),
    AIMessage(content='call2', tool_calls=[
        {'id': 'z1', 'type': 'tool_call', 'name': 'a', 'args': {}},
        {'id': 'z2', 'type': 'tool_call', 'name': 'b', 'args': {}}
    ]),
    HumanMessage(content='next')
]
show('case6', msgs6)

# case7: AI 的 tool_calls，但 ToolMessage 出现在下一条 AI 之后（跨 Assistant），应视为未响应并被清空
msgs7 = [
    AIMessage(content='c1', tool_calls=[{'id': 'w1', 'type': 'tool_call', 'name': 't', 'args': {}}]),
    AIMessage(content='c2'),
    ToolMessage(content='late', tool_call_id='w1'),
]
show('case7', msgs7)

# case8: AI 的 tool_calls，ToolMessage 在中间，之后还有 Human，再出现更多 ToolMessage，只有窗口内的应计入响应
msgs8 = [
    AIMessage(content='c1', tool_calls=[{'id': 'q1', 'type': 'tool_call', 'name': 't', 'args': {}}, {'id': 'q2', 'type': 'tool_call', 'name': 't2', 'args': {}}]),
    ToolMessage(content='in-window', tool_call_id='q1'),
    HumanMessage(content='user says something'),
    ToolMessage(content='out-window', tool_call_id='q2'),
]
show('case8', msgs8)
