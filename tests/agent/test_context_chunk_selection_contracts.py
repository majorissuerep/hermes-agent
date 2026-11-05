"""Lossless planning, recency allocation and untrusted range-response contracts."""
import json

import pytest

from agent.context_chunking import ContextChunk, plan_context_chunks, select_verbatim, source_units
from agent.model_metadata import estimate_tokens_rough


def test_balanced_chunks_cover_every_sentence_and_share_one_window_relative_budget():
    source = "One complete sentence. Another intact sentence.\n" * 26000
    chunks = plan_context_chunks(source, 250000, 25000)
    assert len(chunks) == 2
    assert "".join(chunk.text for chunk in chunks) == source
    assert all(chunk.text.endswith("\n") for chunk in chunks)
    assert all(estimate_tokens_rough(chunk.numbered()) + 16384 + 512 < 250000 for chunk in chunks)
    assert abs(estimate_tokens_rough(chunks[0].text) - estimate_tokens_rough(chunks[1].text)) < 512
    assert [chunk.retained_tokens for chunk in chunks] == [8333, 16667]
    unicode_source = "Прочитай предложение. 保留整句话。\n" * 300 + "indivisible" * 4000
    units = source_units(unicode_source, 128)
    assert "".join(units) == unicode_source
    assert all(estimate_tokens_rough(unit) <= 128 for unit in units)
    cjk = "保留完整句子。" * 400 + "\n"
    sentences = source_units(cjk, 128)
    assert "".join(sentences) == cjk
    assert all(sentence.endswith(("。", "\n")) for sentence in sentences)


def test_selection_copies_only_original_units_in_chronological_order_within_budget():
    chunk = ContextChunk(("Old exact decision.\n", "Never invent a result.\n", "Latest exact fact.\n"), 64, 2, 2)
    selected = select_verbatim(chunk, '{"ranges":[[3,3],[1,1],[1,1]]}')
    assert selected.count("Old exact decision.") == 1
    assert selected.index("Old exact decision.") < selected.index("Latest exact fact.")
    assert "Never invent a result." not in selected
    assert estimate_tokens_rough(selected) <= 64
    for response in (
        {"ranges": [[0, 3]]}, {"ranges": [[1, 4]]}, {"ranges": [[True, 1]]},
        {"ranges": []}, {"ranges": [[1, 1]], "text": "invented answer"},
    ):
        with pytest.raises(ValueError):
            select_verbatim(chunk, json.dumps(response))
