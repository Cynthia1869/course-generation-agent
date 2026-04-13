from functools import lru_cache

from app.audit.logger import AuditService, EventBroker
from app.core.settings import Settings, get_settings
from app.files.parser import DocumentParser
from app.llm.deepseek_client import DeepSeekClient
from app.services.course_agent import CourseAgentService
from app.storage.thread_store import ThreadStore
from app.workflows.course_graph import CourseGraph


@lru_cache
def get_service() -> CourseAgentService:
    settings: Settings = get_settings()
    broker = EventBroker()
    store = ThreadStore()
    audit = AuditService(broker)
    parser = DocumentParser()
    graph = CourseGraph(
        settings=settings,
        store=store,
        broker=broker,
        audit=audit,
        deepseek=DeepSeekClient(settings),
    )
    return CourseAgentService(
        settings=settings,
        store=store,
        broker=broker,
        audit=audit,
        parser=parser,
        graph=graph,
    )
