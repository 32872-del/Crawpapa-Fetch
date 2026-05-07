"""MCP Client 集成 — 连接 MCP Server 并桥接到 CrewAI 工具"""
import asyncio
import threading
from concurrent.futures import Future
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, Field, create_model

try:
    from crewai.tools import BaseTool
except ImportError:
    BaseTool = None


class MCPServerManager:
    """管理与 MCP Server 的连接，提供同步调用接口供 CrewAI 使用"""

    def __init__(
        self,
        server_command: str,
        server_args: list[str],
        env: dict[str, str] | None = None,
    ):
        self._server_command = server_command
        self._server_args = server_args
        self._env = env
        self._session: ClientSession | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._queue: asyncio.Queue | None = None
        self._ready: Future | None = None
        self._stopped: Future | None = None

    @staticmethod
    def _content_to_text(result) -> str:
        texts = [item.text for item in result.content if hasattr(item, "text")]
        return "\n".join(texts) if texts else str(result)

    async def _worker(self) -> None:
        server_params = StdioServerParameters(
            command=self._server_command,
            args=self._server_args,
            env=self._env,
        )
        try:
            async with AsyncExitStack() as exit_stack:
                read, write = await exit_stack.enter_async_context(
                    stdio_client(server_params)
                )
                self._session = await exit_stack.enter_async_context(
                    ClientSession(read, write)
                )
                await self._session.initialize()
                if self._ready and not self._ready.done():
                    self._ready.set_result(True)

                while True:
                    item = await self._queue.get()
                    if item is None:
                        break
                    name, arguments, result_future = item
                    try:
                        result = await self._session.call_tool(name=name, arguments=arguments)
                        result_future.set_result(self._content_to_text(result))
                    except Exception as exc:
                        result_future.set_exception(exc)
        except Exception as exc:
            if self._ready and not self._ready.done():
                self._ready.set_exception(exc)
            if self._stopped and not self._stopped.done():
                self._stopped.set_exception(exc)
        else:
            if self._stopped and not self._stopped.done():
                self._stopped.set_result(True)
        finally:
            self._session = None

    def start(self) -> None:
        if self._loop is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._ready = Future()
        self._stopped = Future()

        def _run_loop() -> None:
            asyncio.set_event_loop(self._loop)
            self._queue = asyncio.Queue()
            self._loop.create_task(self._worker())
            self._loop.run_forever()

        self._thread = threading.Thread(
            target=_run_loop,
            name=f"mcp-client-{self._server_args[0] if self._server_args else self._server_command}",
            daemon=True,
        )
        self._thread.start()
        self._ready.result(timeout=30)

    def call_tool_sync(self, name: str, arguments: dict[str, Any]) -> str:
        """同步调用 MCP 工具（供 CrewAI Tool._run() 使用）"""
        if self._loop is None:
            self.start()
        result_future: Future = Future()

        def _enqueue() -> None:
            self._queue.put_nowait((name, arguments, result_future))

        self._loop.call_soon_threadsafe(_enqueue)
        return result_future.result(timeout=60)

    def stop(self) -> None:
        if self._loop:
            if self._queue is not None:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
                if self._stopped is not None:
                    self._stopped.result(timeout=30)
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread:
                self._thread.join(timeout=30)
            self._loop.close()
            self._loop = None
            self._thread = None
            self._session = None
            self._queue = None
            self._ready = None
            self._stopped = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


def make_mcp_tool(
    tool_name: str,
    tool_description: str,
    params: dict[str, tuple[type, str]],
    manager: MCPServerManager,
) -> BaseTool:
    """将 MCP Server 的工具动态适配为 CrewAI BaseTool

    Args:
        tool_name: MCP 工具名称（如 "fetch_page"）
        tool_description: 工具描述
        params: 参数定义 {参数名: (类型, 描述)}
        manager: MCPServerManager 实例
    """
    if BaseTool is None:
        raise RuntimeError("crewai is not installed; install the agents extra to use CrewAI tools")

    field_defs = {}
    for pname, (ptype, pdesc) in params.items():
        field_defs[pname] = (ptype, Field(description=pdesc))
    InputModel = create_model(f"{tool_name}Input", **field_defs)

    _mgr = manager

    class MCPBridgeTool(BaseTool):
        name: str = tool_name
        description: str = tool_description
        args_schema: type[BaseModel] = InputModel

        def _run(self, **kwargs: Any) -> str:
            return _mgr.call_tool_sync(tool_name, kwargs)

    return MCPBridgeTool()
