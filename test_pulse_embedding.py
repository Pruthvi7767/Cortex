import pytest
import math
from unittest.mock import AsyncMock, patch

from pulse_embedding import (
    cosine_similarity,
    compute_embedding_score,
    _get_anchor_embeddings
)

def test_cosine_similarity():
    vec1 = [1.0, 0.0, 0.0]
    vec2 = [1.0, 0.0, 0.0]
    assert cosine_similarity(vec1, vec2) == 1.0

    vec3 = [0.0, 1.0, 0.0]
    assert cosine_similarity(vec1, vec3) == 0.0

    # 45 degree angle
    vec4 = [1.0, 1.0, 0.0]
    sim = cosine_similarity(vec1, vec4)
    assert math.isclose(sim, math.cos(math.pi/4), rel_tol=1e-5)

@pytest.mark.asyncio
async def test_compute_embedding_score():
    with patch("pulse_embedding.get_embedding", new_callable=AsyncMock) as mock_get_embedding, \
         patch("pulse_embedding._get_anchor_embeddings", new_callable=AsyncMock) as mock_get_anchors:
        
        # Mock the user prompt embedding
        mock_get_embedding.return_value = [1.0, 0.0, 0.0]

        # Mock the anchor embeddings
        # Strong is identical to prompt
        mock_get_anchors.side_effect = [
            [[1.0, 0.0, 0.0]],  # strong anchors
            [[0.0, 1.0, 0.0]]   # fast anchors
        ]

        score = await compute_embedding_score("test prompt")
        # max_strong = 1.0, max_fast = 0.0 => score = 1.0 / (1.0 + 0.0) = 1.0
        assert score == 1.0

@pytest.mark.asyncio
async def test_compute_embedding_score_half():
    with patch("pulse_embedding.get_embedding", new_callable=AsyncMock) as mock_get_embedding, \
         patch("pulse_embedding._get_anchor_embeddings", new_callable=AsyncMock) as mock_get_anchors:
        
        mock_get_embedding.return_value = [1.0, 1.0, 0.0]

        mock_get_anchors.side_effect = [
            [[1.0, 0.0, 0.0]],  # strong anchors
            [[0.0, 1.0, 0.0]]   # fast anchors
        ]

        score = await compute_embedding_score("test prompt")
        # max_strong = 0.707, max_fast = 0.707 => score = 0.5
        assert math.isclose(score, 0.5, rel_tol=1e-5)
