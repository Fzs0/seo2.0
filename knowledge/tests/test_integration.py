from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge.backend.app.database import get_engine
from knowledge.backend.app.knowledge_service import KnowledgeService
from knowledge.backend.app.schemas import (
    ImportDocumentRequest,
    RetrieveRequest,
    ReviewClaimRequest,
)


pytestmark = pytest.mark.skipif(
    not os.getenv("KNOWLEDGE_DATABASE_URL"),
    reason="KNOWLEDGE_DATABASE_URL is not configured for integration tests",
)


@pytest.mark.asyncio
async def test_import_review_retrieve_and_idempotence_roll_back() -> None:
    unique = uuid4().hex
    query_token = f"intent{unique}"
    engine = get_engine()
    async with engine.connect() as connection:
        outer_transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            service = KnowledgeService(session)
            request = ImportDocumentRequest(
                source_name=f"integration-{unique}",
                canonical_url=f"https://example.test/{unique}",
                title=f"Intent {unique}",
                channel="seo",
                language_code="en",
                market="US",
                raw_content=(
                    f"Use {query_token} to align the page with search intent.\n\n"
                    "Validate the recommendation against real query results."
                ),
                rights_confirmed=True,
            )
            imported = await service.import_document(request)
            duplicate = await service.import_document(request)
            pending = await service.retrieve(
                RetrieveRequest(
                    query=query_token,
                    channel="seo",
                    market="US",
                    language_code="en",
                )
            )

            claims = await service.list_claims(
                review_status="pending", limit=20, offset=0
            )
            # End the read-only implicit transaction before exercising the
            # service's next explicit write transaction on the same test session.
            await session.rollback()
            imported_claim = next(
                item
                for item in claims["items"]
                if item["document_id"] == imported["document"]["id"]
                and query_token in item["statement"]
            )
            reviewed = await service.review_claim(
                imported_claim["id"],
                ReviewClaimRequest(decision="approved", reviewer="integration-test"),
            )
            approved = await service.retrieve(
                RetrieveRequest(
                    query=query_token,
                    channel="seo",
                    market="US",
                    language_code="en",
                )
            )
            cross_market = await service.retrieve(
                RetrieveRequest(
                    query=query_token,
                    channel="seo",
                    market="global",
                    language_code="en",
                )
            )

            assert imported["created"] is True
            assert imported["claims_created"] == 2
            assert duplicate["duplicate"] is True
            assert duplicate["claims_created"] == 0
            assert pending["items"] == []
            assert reviewed["review_status"] == "approved"
            assert len(approved["items"]) == 1
            assert len(cross_market["items"]) == 1
            assert approved["items"][0]["source"]["name"] == request.source_name
            assert approved["items"][0]["evidence"]

            await session.rollback()
            revoked = await service.review_claim(
                imported_claim["id"],
                ReviewClaimRequest(
                    decision="rejected",
                    reviewer="integration-test",
                    note="manual revoke",
                ),
            )
            rejected = await service.retrieve(
                RetrieveRequest(
                    query=query_token,
                    channel="seo",
                    market="US",
                    language_code="en",
                )
            )

            assert revoked["review_status"] == "rejected"
            assert rejected["items"] == []
        finally:
            await session.close()
            await outer_transaction.rollback()
