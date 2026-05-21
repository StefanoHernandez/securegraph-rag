import pytest
from securgraph.adapters.ollama import extract_claims, judge_nli, get_ollama_client

pytestmark = pytest.mark.integration

@pytest.fixture(autouse=True)
def clear_ollama_client_cache():
    get_ollama_client.cache_clear()
    yield
    get_ollama_client.cache_clear()

@pytest.mark.asyncio
async def test_extract_claims_live() -> None:
    text = "Marco works as a medical doctor in Milan. He has a degree in medicine."
    res = await extract_claims(text)
    assert len(res.claims) >= 1
    # Check that claims decompose facts cleanly
    assert any("doctor" in c.text.lower() or "medicine" in c.text.lower() for c in res.claims)
    assert any("marco" in c.text.lower() or "doctor" in c.text.lower() for c in res.claims)

@pytest.mark.asyncio
async def test_judge_nli_live() -> None:
    claim = "Marco is a doctor."
    reference = "Marco works as a medical doctor in Milan."
    res = await judge_nli(claim, reference)
    assert res.label == "entailment"
    assert res.confidence >= 0.5
    
    claim_false = "Marco is a lawyer."
    res_false = await judge_nli(claim_false, reference)
    assert res_false.label in ("contradiction", "neutral")
