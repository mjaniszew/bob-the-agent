#!/usr/bin/env python3
"""
NATS Background Listener for Hermes Agent

Registers the agent with NATS server and listens for incoming task messages.
When a task arrives, it executes the task using Hermes one-shot mode and
sends the result back to the originating agent.

This script runs as a background process alongside the Hermes gateway
in each agent container.

Environment Variables:
  NATS_URL          - NATS server URL (default: nats://nats:4222)
  AGENT_NAME        - Agent identifier (default: main)
  NATS_TASK_TIMEOUT - Task execution timeout in seconds (default: 600)
  NATS_INCOMING_DIR - Directory for persisting incoming messages (default: /opt/data/nats-messages/incoming)

Usage:
  python3 register-nats.py [--help] [--url NATS_URL] [--agent AGENT_NAME]
"""

import argparse
import asyncio
import json
import os
import signal
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import nats
from nats.errors import ConnectionClosedError, TimeoutError, NoServersError

# Configuration from environment
NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")
AGENT_NAME = os.environ.get("AGENT_NAME", "main")
TASK_TIMEOUT = int(os.environ.get("NATS_TASK_TIMEOUT", "1800"))
INCOMING_DIR = os.environ.get("NATS_INCOMING_DIR", "/opt/data/nats-messages/incoming")

# Global NATS connection
nc = None
shutdown_event = asyncio.Event()


def generate_message_id() -> str:
    """Generate a unique message ID."""
    return str(uuid.uuid4())


def get_timestamp() -> str:
    """Get current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


async def execute_task(message_data: dict) -> dict:
    """
    Execute a task using Hermes one-shot mode.

    Constructs a prompt from the task message payload and runs
    `/app/scripts/hermes-cmd.sh -z "<prompt>"` as a subprocess.
    """
    payload = message_data.get("payload", {})
    goal = payload.get("goal", "")
    context = payload.get("context", "")
    save_to = payload.get("save_results_to", "")

    # Build the prompt for hermes one-shot mode
    prompt = goal
    if context:
        prompt = f"{context}\n\nTask: {prompt}"
    if save_to:
        prompt += f"\n\nSave your results to: {save_to}"

    start_time = datetime.now(timezone.utc)

    try:
        process = await asyncio.create_subprocess_exec(
            "/app/scripts/hermes-cmd.sh", "-z", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=TASK_TIMEOUT
        )
        result_text = stdout.decode().strip() if stdout else ""
        error_text = stderr.decode().strip() if stderr else ""
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()

        if process.returncode == 0:
            return {
                "status": "completed",
                "summary": result_text[:5000],  # Truncate for NATS message size
                "result_path": save_to,
                "duration_seconds": duration,
            }
        else:
            return {
                "status": "failed",
                "error": f"Task failed with code {process.returncode}: {error_text[:500]}",
                "duration_seconds": duration,
            }
    except asyncio.TimeoutError:
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        return {"status": "failed", "error": f"Task timed out after {TASK_TIMEOUT}s", "duration_seconds": duration}
    except Exception as e:
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        print(f"[NATS] Error occured executing task: {e}", file=sys.stderr)
        return {"status": "failed", "error": str(e), "duration_seconds": duration}


async def publish_result(nc, original_message: dict, result_data: dict) -> None:
    """Send a result message back to the originating agent via NATS."""
    result_message = {
        "message_id": generate_message_id(),
        "task_type": "task_result",
        "original_message_id": original_message.get("message_id", ""),
        "sender_agent_id": AGENT_NAME,
        "target_agent_id": original_message.get("sender_agent_id", ""),
        "timestamp": get_timestamp(),
        "status": result_data.get("status", "unknown"),
        "payload": result_data,
    }

    subject = f"agent.{original_message.get('sender_agent_id', 'main')}.results"
    try:
        await nc.publish(subject, json.dumps(result_message).encode())
        await nc.flush()
        print(f"[NATS] Published result to {subject} (status: {result_data.get('status')})")
    except Exception as e:
        print(f"[NATS] Error publishing result: {e}", file=sys.stderr)


async def handle_result(message_data: dict) -> None:
    """Persist an incoming result message to disk for the skill to pick up."""
    os.makedirs(INCOMING_DIR, exist_ok=True)
    message_id = message_data.get("message_id", generate_message_id())
    filepath = os.path.join(INCOMING_DIR, f"{message_id}.json")
    try:
        with open(filepath, "w") as f:
            json.dump(message_data, f, indent=2)
        print(f"[NATS] Persisted result message: {filepath}")
    except Exception as e:
        print(f"[NATS] Error persisting result message: {e}", file=sys.stderr)


async def task_handler(msg) -> None:
    """Handle incoming task messages from NATS."""
    try:
        data = json.loads(msg.data.decode())
        sender = data.get("sender_agent_id", "unknown")
        goal = data.get("payload", {}).get("goal", "")
        print(f"[NATS] Received task from {sender}: {goal[:100]}...")

        # Execute the task
        result = await execute_task(data)
        print(f"[NATS] Task completed: {result.get('status')}")

        # Send result back to the originating agent
        await publish_result(nc, data, result)
    except json.JSONDecodeError as e:
        print(f"[NATS] Error decoding task message: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[NATS] Error handling task: {e}", file=sys.stderr)


async def result_handler(msg) -> None:
    """Handle incoming result messages from NATS."""
    try:
        data = json.loads(msg.data.decode())
        sender = data.get("sender_agent_id", "unknown")
        print(f"[NATS] Received result from {sender}")

        # Persist to disk for the agent-to-agent skill to read
        await handle_result(data)
    except json.JSONDecodeError as e:
        print(f"[NATS] Error decoding result message: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[NATS] Error handling result: {e}", file=sys.stderr)


async def connect_with_retry(url: str, name: str, max_retries: int = 0) -> nats.NATS:
    """
    Connect to NATS server with retry logic.

    Args:
        url: NATS server URL
        name: Client name for identification
        max_retries: Maximum number of reconnection attempts (0 = infinite)
    """
    print(f"[NATS] Connecting to {url} as {name}...")

    async def error_cb(e):
        print(f"[NATS] Error: {e}", file=sys.stderr)

    async def disconnected_cb():
        print("[NATS] Disconnected from server")

    async def reconnected_cb():
        print("[NATS] Reconnected to server")

    nc = await nats.connect(
        url,
        name=name,
        reconnect_time_wait=2,
        max_reconnect_attempts=max_retries,  # 0 means infinite in nats-py
        error_cb=error_cb,
        disconnected_cb=disconnected_cb,
        reconnected_cb=reconnected_cb,
    )
    print(f"[NATS] Connected successfully")
    return nc


async def main() -> None:
    """Main entry point for the NATS background listener."""
    parser = argparse.ArgumentParser(description="NATS Background Listener for Hermes Agent")
    parser.add_argument("--url", default=NATS_URL, help=f"NATS server URL (default: {NATS_URL})")
    parser.add_argument("--agent", default=AGENT_NAME, help=f"Agent identifier (default: {AGENT_NAME})")
    args = parser.parse_args()

    agent_id = args.agent
    nats_url = args.url

    global nc

    print(f"[NATS] Starting listener for agent: {agent_id}")
    print(f"[NATS] NATS server: {nats_url}")
    print(f"[NATS] Task timeout: {TASK_TIMEOUT}s")
    print(f"[NATS] Incoming directory: {INCOMING_DIR}")

    # Ensure incoming directory exists
    os.makedirs(INCOMING_DIR, exist_ok=True)

    # Set up signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()

    def signal_handler():
        print("[NATS] Shutdown signal received, cleaning up...")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    # Connect to NATS with infinite reconnect
    try:
        nc = await connect_with_retry(nats_url, f"agent-{agent_id}")
    except Exception as e:
        print(f"[NATS] Failed to connect to NATS: {e}", file=sys.stderr)
        sys.exit(1)

    # Subscribe to task messages for this agent
    task_subject = f"agent.{agent_id}.tasks"
    await nc.subscribe(task_subject, queue=f"{agent_id}-workers", cb=task_handler)
    print(f"[NATS] Subscribed to {task_subject} (queue: {agent_id}-workers)")

    # Subscribe to result messages for this agent
    result_subject = f"agent.{agent_id}.results"
    await nc.subscribe(result_subject, queue=f"{agent_id}-results", cb=result_handler)
    print(f"[NATS] Subscribed to {result_subject} (queue: {agent_id}-results)")

    print(f"[NATS] Agent {agent_id} is ready to receive messages")

    # Wait for shutdown signal
    try:
        await shutdown_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        print("[NATS] Shutting down...")
        if nc and nc.is_connected:
            try:
                await nc.drain()
            except Exception:
                await nc.close()
        print("[NATS] Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[NATS] Interrupted, exiting...")