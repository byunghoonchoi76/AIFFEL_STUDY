"""
LangGraph 기반 ReAct 에이전트
Day 4 학습 내용 기반 구현
"""
from typing import TypedDict, Annotated, List
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolExecutor, ToolInvocation
import operator
import json


# ============================================================
# 도구 정의
# ============================================================

@tool
def search_web(query: str) -> str:
    """웹에서 정보를 검색합니다."""
    # 실제 구현 시 Tavily, SerpAPI 등 연결
    return f"[검색 결과] '{query}'에 대한 가상 검색 결과입니다."


@tool
def calculator(expression: str) -> str:
    """수학 계산을 수행합니다. 예: '2 + 3 * 4'"""
    try:
        result = eval(expression)  # 실제 배포 시 더 안전한 방법 사용
        return f"계산 결과: {result}"
    except Exception as e:
        return f"계산 오류: {e}"


TOOLS = [search_web, calculator]


# ============================================================
# 에이전트 상태
# ============================================================

class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]


# ============================================================
# 에이전트 노드
# ============================================================

def create_agent_node(llm_with_tools):
    def agent_node(state: AgentState) -> dict:
        """LLM이 다음 행동을 결정"""
        response = llm_with_tools.invoke(state["messages"])
        return {"messages": [response]}
    return agent_node


def create_tool_node(tools):
    tool_executor = ToolExecutor(tools)

    def tool_node(state: AgentState) -> dict:
        """도구를 실행하고 결과를 반환"""
        last_msg = state["messages"][-1]
        tool_calls = last_msg.tool_calls

        results = []
        for call in tool_calls:
            invocation = ToolInvocation(tool=call["name"], tool_input=call["args"])
            result = tool_executor.invoke(invocation)
            results.append(
                {"role": "tool", "content": str(result), "tool_call_id": call["id"]}
            )

        from langchain_core.messages import ToolMessage
        return {"messages": [ToolMessage(**r) for r in results]}

    return tool_node


def should_continue(state: AgentState) -> str:
    """도구 호출이 있으면 계속, 없으면 종료"""
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "tools"
    return "end"


# ============================================================
# 그래프 빌드
# ============================================================

def build_react_agent(model_name: str = "gpt-4o-mini") -> object:
    """
    ReAct 패턴의 LangGraph 에이전트 생성

    Returns:
        컴파일된 LangGraph 앱

    Example:
        >>> app = build_react_agent()
        >>> result = app.invoke({"messages": [HumanMessage("도쿄 인구는?")]})
        >>> print(result["messages"][-1].content)
    """
    llm = ChatOpenAI(model=model_name, temperature=0)
    llm_with_tools = llm.bind_tools(TOOLS)

    # 노드 생성
    agent_node = create_agent_node(llm_with_tools)
    tool_node = create_tool_node(TOOLS)

    # 그래프 구성
    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)

    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {
        "tools": "tools",
        "end": END,
    })
    graph.add_edge("tools", "agent")

    return graph.compile()


# ============================================================
# 사용 예시
# ============================================================

if __name__ == "__main__":
    app = build_react_agent()

    questions = [
        "2024년 서울 인구를 검색하고, 그 수의 제곱근을 계산해줘.",
        "파이썬에서 리스트 컴프리헨션이란?",
    ]

    for q in questions:
        print(f"\n🔵 질문: {q}")
        result = app.invoke({"messages": [HumanMessage(q)]})
        print(f"✅ 답변: {result['messages'][-1].content}")
