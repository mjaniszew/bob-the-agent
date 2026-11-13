"""Zero-network Discord fakes (tests only): the six-method client surface the
bot logic is allowed to call (see harness.gateway.discord_bot) plus the
discord object shapes the bot touches — channels, threads, users, members,
roles, messages. Mirrors discord.py 2.7.x shapes narrowly: only the fields the
parity tests assert (``.id``/``.name``/``.content``/``.member``/...). Real
gateway connections are NEVER opened (``FakeBotClient.start`` raises).

Recording surface used by tests: ``FakeBotClient.sent`` (channel, text),
``FakeBotClient.edits`` (message, text), ``FakeBotClient.sent_to[<id>]``,
``FakeChannel.sent``, ``FakeMessage.edited``, ``FakeChannel.threads_created``
and ``FakeBotClient.fetch_attempts``.
"""

from __future__ import annotations

__all__ = [
    "FakeBotClient",
    "FakeChannel",
    "FakeMember",
    "FakeMessage",
    "FakeRole",
    "FakeThread",
    "FakeUser",
    "make_message",
]

_THREAD_ID_NEXT = [720000000]
_MESSAGE_ID_NEXT = [410000000]


class FakeUser:
    def __init__(self, uid: int, name: str = "user") -> None:
        self.id = uid
        self.name = name
        self.bot = False


class FakeRole:
    def __init__(self, rid: int, name: str = "role") -> None:
        self.id = rid
        self.name = name


class FakeMember(FakeUser):
    def __init__(self, uid: int, roles: list[FakeRole] | None = None, name: str = "member") -> None:
        super().__init__(uid, name)
        self.roles = list(roles or [])


class FakeThread:
    kind = "thread"

    def __init__(self, tid: int, name: str) -> None:
        self.id = tid
        self.name = name
        self.sent: list[str] = []
        self.edited: list[str] = []

    async def send(self, text: str) -> FakeMessage:
        message = FakeMessage("", "bot", self)
        self.sent.append(text)
        return message

    async def edit(self, *, content: str) -> FakeMessage:
        return self


class FakeChannel:
    kind = "text"

    def __init__(self, cid: int, *, kind: str = "text", allow_threads: bool = True) -> None:
        self.id = cid
        self.kind = kind
        self.allow_threads = allow_threads
        self.sent: list[str] = []
        self.threads_created: list[FakeThread] = []

    async def send(self, text: str) -> FakeMessage:
        message = FakeMessage("", "bot", self)
        self.sent.append(text)
        return message

    async def create_thread(self, *, name: str) -> FakeThread:
        _THREAD_ID_NEXT[0] += 1
        thread = FakeThread(_THREAD_ID_NEXT[0], name)
        self.threads_created.append(thread)
        return thread


class FakeMessage:
    def __init__(
        self,
        content: str,
        author: object,
        channel: object,
        member: object | None = None,
        mid: int | None = None,
    ) -> None:
        self.id = mid if mid is not None else _MESSAGE_ID_NEXT[0]
        _MESSAGE_ID_NEXT[0] += 1
        self.content = content
        self.author = author
        self.channel = channel
        self.member = member
        self.mentions: list[object] = []
        self.bot = False
        self.edited: list[str] = []


class FakeBotClient:
    """The six-method shim surface (see gateway.discord_bot.DiscordBot)."""

    def __init__(self) -> None:
        self.user = FakeUser(999999, "harness-bot")
        self.channels: dict[int, FakeChannel] = {}
        self.sent: list[tuple[object, str]] = []
        self.edits: list[tuple[object, str]] = []
        self.sent_to: dict[int, list[str]] = {}
        self.fetch_attempts: list[int] = []
        self.send_fail_for: set[int] = set()

    def text_channel(self, cid: int) -> FakeChannel:
        channel = FakeChannel(cid, kind="text", allow_threads=True)
        self.channels[cid] = channel
        return channel

    def dm(self, uid: int) -> FakeChannel:
        channel = FakeChannel(uid, kind="dm", allow_threads=False)
        self.channels[uid] = channel
        return channel

    async def send_message(self, channel: object, text: str) -> FakeMessage:
        channel_id = getattr(channel, "id", None)
        if channel_id in self.send_fail_for:
            raise RuntimeError(f"send failure injected for {channel_id}")
        self.sent.append((channel, text))
        self.sent_to.setdefault(channel_id, []).append(text)
        channel.sent.append(text)
        return FakeMessage("", self.user, channel)

    async def edit_message(self, message: object, text: str) -> FakeMessage:
        self.edits.append((message, text))
        message.edited.append(text)
        return message

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self.channels.get(channel_id)

    async def fetch_channel(self, channel_id: int) -> FakeChannel | None:
        self.fetch_attempts.append(channel_id)
        return self.channels.get(channel_id)

    async def create_thread(self, channel: object, name: str) -> FakeThread | None:
        factory = getattr(channel, "create_thread", None)
        if factory is None or not getattr(channel, "allow_threads", False):
            return None
        return await factory(name=name)

    async def start(self, token: str) -> None:
        raise RuntimeError("fake client does not spawn a gateway connection")


def make_message(
    client: FakeBotClient,
    content: str,
    *,
    channel: object | None = None,
    user: object | None = None,
    member: object | None = None,
    mid: int | None = None,
) -> FakeMessage:
    """Bind one message (registering the channel on the client when needed)."""
    if channel is None:
        channel = client.dm(user.id) if user is not None else client.text_channel(5100)
    author = user if user is not None else FakeUser(1111, "alice")
    return FakeMessage(content, author, channel, member=member, mid=mid)
