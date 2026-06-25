import pytest

from securgraph.adapters.llm import get_llm_adapter, reset_llm_adapter_cache

pytestmark = [pytest.mark.integration, pytest.mark.live]


@pytest.fixture(autouse=True)
def clear_adapter_cache():
    reset_llm_adapter_cache()
    yield
    reset_llm_adapter_cache()


@pytest.mark.asyncio
async def test_extract_claims_live() -> None:
    adapter = get_llm_adapter()
    text = "Marco works as a medical doctor in Milan. He has a degree in medicine."
    res = await adapter.extract_claims(text)
    assert len(res.claims) >= 1
    # Check that claims decompose facts cleanly
    assert any("doctor" in c.text.lower() or "medicine" in c.text.lower() for c in res.claims)
    assert any("marco" in c.text.lower() or "doctor" in c.text.lower() for c in res.claims)


@pytest.mark.asyncio
async def test_judge_nli_live() -> None:
    adapter = get_llm_adapter()
    claim = "Marco is a doctor."
    reference = "Marco works as a medical doctor in Milan."
    res = await adapter.judge_nli(claim, reference)
    assert res.label == "entailment"
    assert res.confidence >= 0.5

    claim_false = "Marco is a lawyer."
    res_false = await adapter.judge_nli(claim_false, reference)
    assert res_false.label in ("contradiction", "neutral")
