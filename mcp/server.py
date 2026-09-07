"""
vibeMK MCP Server implementation

Copyright (C) 2024 Andre <andre@example.com>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.
"""

import json
import sys
from typing import Any, Dict, Optional

from api import CheckMKClient
from config import CheckMKConfig, MCPConfig
from mcp.dispatch import Dispatcher
from mcp.registry import ToolRegistry
from utils import get_logger

logger = get_logger(__name__)


class CheckMKMCPServer:
    """vibeMK MCP Server for CheckMK integration"""

    def __init__(self):
        self.mcp_config = MCPConfig()

        # Defer CheckMK configuration validation until first API call
        self.config = None
        self.client = None
        self.registry = None
        self._initialized = False

        self._dispatcher = Dispatcher(self._get_registry, self.mcp_config)

    def _ensure_initialized(self):
        """Initialize CheckMK connection and handlers on first use"""
        if self._initialized:
            logger.debug("CheckMK connection already initialized")
            return

        logger.info("Initializing CheckMK connection for first tool call...")

        try:
            # Load and validate CheckMK configuration
            logger.debug("Loading CheckMK configuration from environment...")
            self.config = CheckMKConfig.from_env()
            logger.info(
                f"CheckMK config loaded: {self.config.server_url} site={self.config.site} user={self.config.username}"
            )

            logger.debug("Validating CheckMK configuration...")
            self.config.validate()
            logger.info("CheckMK configuration validated successfully")

            # Setup client and handlers
            logger.debug("Creating CheckMK API client...")
            self.client = CheckMKClient(self.config)
            logger.info("CheckMK API client created")

            logger.debug("Setting up tool registry...")
            self.registry = ToolRegistry.from_client(self.client)
            logger.info("Registry initialized: %d tools available", len(self.registry.tool_names()))

            self._initialized = True
            logger.info("CheckMK connection initialization complete")

        except Exception as e:
            # Log error with full traceback but don't crash the server
            logger.exception(f"Failed to initialize CheckMK connection: {e}")
            logger.error("This is usually due to missing environment variables or unreachable CheckMK server")
            # Raise the error so it can be handled in the tool call
            raise

    def _get_registry(self) -> ToolRegistry:
        """Provide the tool registry, initializing the CheckMK connection on first use."""
        self._ensure_initialized()
        return self.registry

    async def handle_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle incoming MCP requests"""
        return await self._dispatcher.handle(request)

    async def run(self):
        """Main server loop"""
        logger.info(f"Starting vibeMK Server {self.mcp_config.server_version}")
        logger.info("CheckMK connection will be initialized on first tool call")
        logger.info("Server ready to accept MCP requests on stdin")

        while True:
            try:
                # Log that we're waiting for input
                logger.debug("Waiting for input on stdin...")
                line = sys.stdin.readline()

                if not line:
                    logger.info("No input received, stdin closed - shutting down")
                    break

                line = line.strip()
                if not line:
                    logger.debug("Empty line received, continuing")
                    continue

                logger.debug(f"Received request: {line[:100]}...")

                try:
                    request = json.loads(line)
                    logger.debug(f"Parsed JSON request, method: {request.get('method')}")
                except json.JSONDecodeError as json_err:
                    logger.error(f"Invalid JSON received: {line[:200]}... - Error: {json_err}")
                    continue

                response = await self.handle_request(request)

                if response is not None:
                    response_str = json.dumps(response, ensure_ascii=False)
                    logger.debug(f"Sending response: {response_str[:100]}...")
                    print(response_str, flush=True)
                else:
                    logger.debug("No response to send")

            except KeyboardInterrupt:
                logger.info("Server stopped by user (KeyboardInterrupt)")
                break
            except EOFError:
                logger.info("EOF reached, exiting gracefully")
                break
            except Exception as e:
                # Log the error with full traceback for debugging
                logger.exception(f"Unexpected error in main loop (continuing): {e}")
                continue

        logger.info("vibeMK Server shutdown complete")
