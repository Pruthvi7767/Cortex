import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import pulse_learner

@pytest.mark.asyncio
async def test_update_thresholds_high_trust():
    # Mock records returned by DB
    mock_records = [
        {
            "caller_id": "client_trusted",
            "total_reqs": 150,
            "fast_mid_reqs": 120,
            "fast_mid_successes": 120  # 100% success rate -> High trust
        }
    ]

    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = mock_records
    # acquire() is an async context manager
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

    with patch("pulse_learner.init_db", new_callable=AsyncMock), \
         patch("pulse_learner.close_db", new_callable=AsyncMock), \
         patch("pulse_learner.get_pool", return_value=mock_pool), \
         patch("pulse_learner.set_caller_thresholds", new_callable=AsyncMock) as mock_set_redis:

        await pulse_learner.update_thresholds()

        # Check DB was updated with lower thresholds
        mock_conn.execute.assert_called_once()
        args = mock_conn.execute.call_args[0]
        assert args[1] == "client_trusted"
        assert args[2] == 1.5  # fast_threshold lowered
        assert args[3] == 4.0  # strong_threshold lowered

        # Check Redis was updated
        mock_set_redis.assert_called_once_with("client_trusted", 1.5, 4.0)

@pytest.mark.asyncio
async def test_update_thresholds_high_error():
    mock_records = [
        {
            "caller_id": "client_buggy",
            "total_reqs": 150,
            "fast_mid_reqs": 100,
            "fast_mid_successes": 90  # 90% success rate -> High error
        }
    ]

    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = mock_records
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

    with patch("pulse_learner.init_db", new_callable=AsyncMock), \
         patch("pulse_learner.close_db", new_callable=AsyncMock), \
         patch("pulse_learner.get_pool", return_value=mock_pool), \
         patch("pulse_learner.set_caller_thresholds", new_callable=AsyncMock) as mock_set_redis:

        await pulse_learner.update_thresholds()

        mock_conn.execute.assert_called_once()
        args = mock_conn.execute.call_args[0]
        assert args[1] == "client_buggy"
        assert args[2] == 3.0  # fast_threshold raised
        assert args[3] == 6.0  # strong_threshold raised

        mock_set_redis.assert_called_once_with("client_buggy", 3.0, 6.0)

@pytest.mark.asyncio
async def test_update_thresholds_normal():
    mock_records = [
        {
            "caller_id": "client_normal",
            "total_reqs": 150,
            "fast_mid_reqs": 100,
            "fast_mid_successes": 97  # 97% success rate -> Default
        }
    ]

    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = mock_records
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

    with patch("pulse_learner.init_db", new_callable=AsyncMock), \
         patch("pulse_learner.close_db", new_callable=AsyncMock), \
         patch("pulse_learner.get_pool", return_value=mock_pool), \
         patch("pulse_learner.set_caller_thresholds", new_callable=AsyncMock) as mock_set_redis:

        await pulse_learner.update_thresholds()

        mock_conn.execute.assert_called_once()
        args = mock_conn.execute.call_args[0]
        assert args[1] == "client_normal"
        assert args[2] == 2.0  # fast_threshold default
        assert args[3] == 5.0  # strong_threshold default

        mock_set_redis.assert_called_once_with("client_normal", 2.0, 5.0)

