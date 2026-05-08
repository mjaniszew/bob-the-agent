#!/usr/bin/env python3
"""
NATS Helper for agent-to-agent skill.

CLI interface for sending/receiving inter-agent messages via NATS.
Uses the nats-py asyncio client for all NATS operations.

Usage:
  python3 nats-helper.py --action send_task --target <agent_id> --goal <text> [options]
  python3 nats-helper.py --action check_messages [--timeout <seconds>]
  python3 nats-helper.py --action send_result --target <agent_id> --original-msg-id <id> --status <completed|failed> [options]
"""

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone

import nats
from nats.errors import ConnectionClosedError, TimeoutError, NoServersError

# Configuration from environment
NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")
AGENT_NAME = os.environ.get("AGENT_NAME", "main")
INCOMING_DIR = os.environ.get("NATS_INCOMING_DIR", "/opt/data/nats-messages/incoming")

# Message types
TASK_TYPE_DELEGATE = "delegate_task"
TASK_TYPE_RESULT = "task_result"


def generate_message_id() -> str:
    """Generate a unique message ID."""
    return str(uuid.uuid4())


def get_timestamp() -> str:
    """Get current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


async def send_task(args: argparse.Namespace) -> None:
    """Send a task message to a target agent via NATS."""
    message = {
        "message_id": generate_message_id(),
        "task_type": TASK_TYPE_DELEGATE,
        "sender_agent_id": AGENT_NAME,
        "target_agent_id": args.target,
        "timestamp": get_timestamp(),
        "payload": {
            "goal": args.goal,
            "context": args.context or "",
            "toolsets": args.toolsets.split(",") if args.toolsets else [],
            "save_results_to": args.save_results_to or "",
        },
    }

    nc = None
    try:
        nc = await nats.connect(NATS_URL, name=f"agent-{AGENT_NAME}-sender")
        subject = f"agent.{args.target}.tasks"
        await nc.publish(subject, json.dumps(message).encode())
        await nc.flush()
        result = {
            "success": True,
            "message_id": message["message_id"],
            "subject": subject,
            "sender_agent_id": AGENT_NAME,
            "target_agent_id": args.target,
        }
        print(json.dumps(result, indent=2))
    except (ConnectionClosedError, TimeoutError, NoServersError) as e:
        result = {"success": False, "error": f"NATS connection error: {str(e)}"}
        print(json.dumps(result, indent=2))
        sys.exit(1)
    except Exception as e:
        result = {"success": False, "error": str(e)}
        print(json.dumps(result, indent=2))
        sys.exit(1)
    finally:
        if nc and nc.is_connected:
            await nc.drain()


async def check_messages(args: argparse.Namespace) -> None:
    """Check for pending incoming messages in the local message store."""
    if not os.path.exists(INCOMING_DIR):
        result = {"success": True, "messages": [], "agent_id": AGENT_NAME}
        print(json.dumps(result, indent=2))
        return

    messages = []
    # Sort filenames for chronological order
    for filename in sorted(os.listdir(INCOMING_DIR)):
        if not filename.endswith(".json"):
            continue
        filepath = os.path.join(INCOMING_DIR, filename)
        try:
            with open(filepath, "r") as f:
                msg_data = json.load(f)
                messages.append(msg_data)
            # Remove consumed message
            os.remove(filepath)
        except (json.JSONDecodeError, OSError) as e:
            # Skip malformed files but don't crash
            print(f"Warning: Could not process {filename}: {e}", file=sys.stderr)
            continue

    result = {
        "success": True,
        "messages": messages,
        "agent_id": AGENT_NAME,
        "count": len(messages),
    }
    print(json.dumps(result, indent=2))


async def send_result(args: argparse.Namespace) -> None:
    """Send a task result message back to the originating agent via NATS."""
    message = {
        "message_id": generate_message_id(),
        "task_type": TASK_TYPE_RESULT,
        "original_message_id": args.original_msg_id,
        "sender_agent_id": AGENT_NAME,
        "target_agent_id": args.target,
        "timestamp": get_timestamp(),
        "status": args.status,
        "payload": {
            "summary": args.summary or "",
            "result_path": args.result_path or "",
            "error": args.error or "",
            "duration_seconds": args.duration if args.duration is not None else 0,
        },
    }

    nc = None
    try:
        nc = await nats.connect(NATS_URL, name=f"agent-{AGENT_NAME}-result")
        subject = f"agent.{args.target}.results"
        await nc.publish(subject, json.dumps(message).encode())
        await nc.flush()
        result = {
            "success": True,
            "message_id": message["message_id"],
            "subject": subject,
            "sender_agent_id": AGENT_NAME,
            "target_agent_id": args.target,
        }
        print(json.dumps(result, indent=2))
    except (ConnectionClosedError, TimeoutError, NoServersError) as e:
        result = {"success": False, "error": f"NATS connection error: {str(e)}"}
        print(json.dumps(result, indent=2))
        sys.exit(1)
    except Exception as e:
        result = {"success": False, "error": str(e)}
        print(json.dumps(result, indent=2))
        sys.exit(1)
    finally:
        if nc and nc.is_connected:
            await nc.drain()


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="NATS Helper for agent-to-agent communication",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Actions:
  send_task       Send a task to a target agent via NATS
  check_messages  Check for pending incoming messages
  send_result     Send a task result back to the originating agent

Examples:
  # Send a task to researcher agent
  python3 nats-helper.py --action send_task --target researcher --goal "Research AI trends"

  # Check for messages
  python3 nats-helper.py --action check_messages

  # Send a result back
  python3 nats-helper.py --action send_result --target main --original-msg-id <id> --status completed --summary "Done"
""",
    )

    parser.add_argument(
        "--action",
        required=True,
        choices=["send_task", "check_messages", "send_result"],
        help="Action to perform",
    )

    # send_task arguments
    parser.add_argument("--target", help="Target agent ID (required for send_task and send_result)")
    parser.add_argument("--goal", help="Task goal description (required for send_task)")
    parser.add_argument("--context", help="Additional context for the task")
    parser.add_argument(
        "--toolsets", help="Comma-separated list of toolsets for the task"
    )
    parser.add_argument(
        "--save-results-to", help="Path where results should be saved"
    )

    # send_result arguments
    parser.add_argument(
        "--original-msg-id", help="Original message ID this result is for"
    )
    parser.add_argument(
        "--status",
        choices=["completed", "failed"],
        help="Task completion status",
    )
    parser.add_argument("--summary", help="Summary of the task result")
    parser.add_argument("--result-path", help="Path to result files")
    parser.add_argument("--error", help="Error message if task failed")
    parser.add_argument(
        "--duration", type=int, help="Task duration in seconds"
    )

    # check_messages arguments
    parser.add_argument(
        "--timeout",
        type=int,
        default=0,
        help="Timeout in seconds to wait for messages (0 = non-blocking poll)",
    )

    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    """Validate that required arguments are present for the chosen action."""
    if args.action == "send_task":
        if not args.target:
            parser = argparse.ArgumentParser()
            parser.error("--target is required for send_task action")
        if not args.goal:
            parser = argparse.ArgumentParser()
            parser.error("--goal is required for send_task action")
    elif args.action == "send_result":
        if not args.target:
            parser = argparse.ArgumentParser()
            parser.error("--target is required for send_result action")
        if not args.original_msg_id:
            parser = argparse.ArgumentParser()
            parser.error("--original-msg-id is required for send_result action")
        if not args.status:
            parser = argparse.ArgumentParser()
            parser.error("--status is required for send_result action")


async def main() -> None:
    """Main entry point."""
    args = parse_args()
    validate_args(args)

    if args.action == "send_task":
        await send_task(args)
    elif args.action == "check_messages":
        await check_messages(args)
    elif args.action == "send_result":
        await send_result(args)


if __name__ == "__main__":
    asyncio.run(main())