"""
Conversational retrieval logic: takes a user question + chat history,
retrieves relevant chunks from ChromaDB, and asks Gemini (via Google AI
Studio) to answer in plain, friendly language, citing sources.

Written as an explicit step-by-step pipeline (rather than a black-box
LangChain chain) so it's easy to see exactly what's happening at each step.
"""
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.schema import HumanMessage, SystemMessage

from app.config import settings
from app.vectorstore import get_retriever
from app.prompts import SYSTEM_PROMPT, CONDENSE_QUESTION_PROMPT

_llm = None


def get_llm() -> ChatGoogleGenerativeAI:
    global _llm
    if _llm is None:
        _llm = ChatGoogleGenerativeAI(
            model=settings.GEMINI_MODEL,
            google_api_key=settings.GOOGLE_API_KEY,
            temperature=0.2,
        )
    return _llm


def _format_history(history: list[dict]) -> str:
    lines = []
    for turn in history:
        lines.append(f"User: {turn['question']}")
        lines.append(f"Assistant: {turn['answer']}")
    return "\n".join(lines)


def _condense_question(question: str, history: list[dict]) -> str:
    """Rewrite a follow-up question into a standalone one using history."""
    if not history:
        return question
    prompt = CONDENSE_QUESTION_PROMPT.format(
        chat_history=_format_history(history[-5:]),  # last 5 turns is plenty
        question=question,
    )
    response = get_llm().invoke([HumanMessage(content=prompt)])
    return response.content.strip() or question


def _build_context(chunks) -> str:
    parts = []
    for c in chunks:
        source = c.metadata.get("source", "unknown document")
        parts.append(f"[From: {source}]\n{c.page_content}")
    return "\n\n---\n\n".join(parts)


def answer_question(
    question: str,
    history: list[dict] | None = None,
    owner_id: str | None = None,
) -> dict:
    """
    Returns:
      {
        "answer": str,
        "sources": [{"filename": str, "snippet": str}, ...],
        "standalone_question": str,
      }
    """
    history = history or []
    standalone_question = _condense_question(question, history)

    retriever = get_retriever(owner_id=owner_id)
    chunks = retriever.invoke(standalone_question)

    if not chunks:
        return {
            "answer": (
                "I couldn't find anything about that in your uploaded "
                "documents yet. Please upload the relevant prescription or "
                "report first, then ask again."
            ),
            "sources": [],
            "standalone_question": standalone_question,
        }

    context = _build_context(chunks)
    system_message = SystemMessage(content=SYSTEM_PROMPT.format(context=context))
    human_message = HumanMessage(content=question)

    response = get_llm().invoke([system_message, human_message])

    sources = [
        {
            "filename": c.metadata.get("source", "unknown"),
            "snippet": c.page_content[:220].strip() + (
                "..." if len(c.page_content) > 220 else ""
            ),
        }
        for c in chunks
    ]

    return {
        "answer": response.content,
        "sources": sources,
        "standalone_question": standalone_question,
    }
