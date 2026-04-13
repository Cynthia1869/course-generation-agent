from functools import lru_cache

from app.application.services import CourseAgentService
from app.audit.logger import AuditService, EventBroker
from app.config import Settings, get_settings
from app.documents.parser import DocumentParser
from app.graph.workflow import CourseGraph
from app.models.llm import ModelGateway
from app.persistence.store import ThreadStore


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
        model_gateway=ModelGateway(settings),
    )
    return CourseAgentService(
        settings=settings,
        store=store,
        broker=broker,
        audit=audit,
        parser=parser,
        graph=graph,
    )
